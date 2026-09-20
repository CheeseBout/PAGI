// 2D avatar canvas (SPEC §20.7, v2). Renders a Live2D model via the vendored
// Cubism Web Framework (`frontend/src/vendor/live2d/`, see 2D_PLAN.md §3.2 —
// this replaced the `pixi-live2d-display` wrapper in v1, which never caught
// up to Cubism 5) and reacts to `aiState`. Model/texture/motion files are
// served from `/avatars/{agentId}/...` (routes_avatar_files.py, SPEC §20.4).
// Owns mouse interaction too (SPEC §20.14, Phase 21: look-follow + hover/tap
// reactions) — the pure math/classification helpers live in
// `avatarPointer.ts` so they're unit-testable without a WebGL2 context.
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { CubismFramework, Option } from "@framework/live2dcubismframework";
import { CubismMatrix44 } from "@framework/math/cubismmatrix44";
import * as LAppDefine from "@/vendor/live2d/app/lappdefine";
import { LAppModel, LoadStep } from "@/vendor/live2d/app/lappmodel";
import { LAppPal } from "@/vendor/live2d/app/lapppal";
import { LAppSubdelegate } from "@/vendor/live2d/app/lappsubdelegate";
import {
  canReactToTouch,
  canTap,
  clientPointToNdc,
  eyeAnchorModelY,
  invertMvpPoint,
  isTap,
  lookTarget,
  modelYExtent,
} from "./avatarPointer";

const DEFAULT_MAX_FPS = 30; // §20.7/§20.9: cap at 30fps

export type AiState = "idle" | "thinking" | "talking" | "acting" | "waiting";

export interface AvatarConfig {
  version: 1;
  enabled: boolean;
  model_path: string;
  scale?: number;
  offset_x?: number;
  offset_y?: number;
  idle_motion_group?: string;
  motion_map?: Partial<Record<AiState, string>>;
  expression_map?: Partial<Record<AiState, string>>;
}

export interface AvatarCanvasProps {
  agentId: string;
  avatarConfig: AvatarConfig;
  aiState: AiState;
  /** 0–1, optional — the *only* hook Phase TTS is allowed to use for lip-sync
   * (SPEC §20.11). Undefined in v1; when aiState is "talking" and no real
   * value is supplied, a cheap internal oscillation stands in so the mouth
   * still moves — swap in a real AnalyserNode reading later without touching
   * this component. */
  audioLevel?: number;
  /** frame-rate cap, default 30 (§20.9). The desktop overlay lowers it when idle (§21.12). */
  maxFps?: number;
  className?: string;
  onError?: (err: Error) => void;
}

// CubismFramework.startUp()/initialize() are both internally idempotent
// (guarded by their own s_isStarted/s_isInitialized flags — see
// vendor/live2d/framework/src/live2dcubismframework.ts) and, unlike
// everything else here, are meant to run exactly once for the page's whole
// lifetime, never torn down on unmount. Every AvatarCanvas mount calls this;
// only the first one actually does anything.
function ensureCubismFramework(): void {
  const option = new Option();
  option.logFunction = (message: string) => {
    // eslint-disable-next-line no-console
    console.debug("[Cubism]", message);
  };
  option.loggingLevel = LAppDefine.CubismLoggingLevel;
  CubismFramework.startUp(option);
  CubismFramework.initialize();
}

function supportsWebGL2(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!canvas.getContext("webgl2");
  } catch {
    return false;
  }
}

/** §20.6 last rule: an AiState with no mapping and no idle fallback keeps
 * whatever motion is currently playing instead of forcing idle. Framework's
 * own `LAppModel.update()` already auto-restarts a random Idle motion once
 * nothing else is playing, so "keep current" here just means "don't
 * interrupt it with an explicit call". */
function motionGroupFor(state: AiState, cfg: AvatarConfig): string | undefined {
  const mapped = cfg.motion_map?.[state];
  if (mapped) return mapped;
  if (state === "idle") return cfg.idle_motion_group || LAppDefine.MotionGroupIdle;
  return undefined;
}

/** Split "hiyori/Hiyori.model3.json" into ("hiyori/", "Hiyori.model3.json") —
 * LAppModel.loadAssets(dir, fileName) wants them separate, dir ending in "/". */
function splitModelPath(modelPath: string): { dir: string; file: string } {
  const idx = modelPath.lastIndexOf("/");
  if (idx === -1) return { dir: "", file: modelPath };
  return { dir: modelPath.slice(0, idx + 1), file: modelPath.slice(idx + 1) };
}

export default function AvatarCanvas({
  agentId,
  avatarConfig,
  aiState,
  audioLevel,
  maxFps = DEFAULT_MAX_FPS,
  className,
  onError,
}: AvatarCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const subdelegateRef = useRef<LAppSubdelegate | null>(null);
  const modelRef = useRef<LAppModel | null>(null);
  const loggedErrorRef = useRef(false);
  const audioLevelRef = useRef(audioLevel);
  audioLevelRef.current = audioLevel;
  const aiStateRef = useRef(aiState);
  aiStateRef.current = aiState;
  const maxFpsRef = useRef(maxFps);
  maxFpsRef.current = maxFps;
  const avatarConfigRef = useRef(avatarConfig);
  avatarConfigRef.current = avatarConfig;
  // Mouse interaction state (SPEC §20.14) — plain refs, not React state: none
  // of this should ever trigger a re-render.
  const lastMvpRef = useRef<CubismMatrix44 | null>(null);
  const pointerDownRef = useRef<{ x: number; y: number; t: number } | null>(null);
  const lastReactionAtRef = useRef<number>(-Infinity);
  const hoveringHeadRef = useRef(false);
  // Eye height in model space, measured once per loaded model (SPEC §20.14.2).
  const eyeAnchorYRef = useRef<number | null>(null);

  const [status, setStatus] = useState<"loading" | "ready" | "error" | "unsupported">("loading");

  const modelPath = avatarConfig.model_path;
  const enabled = avatarConfig.enabled === true && !!modelPath;

  // -- init / teardown: exactly one WebGL context + Live2D model per
  // (agentId, modelPath). Re-runs (and fully tears down the old context)
  // whenever either changes.
  useEffect(() => {
    if (!enabled) return;
    if (!supportsWebGL2()) {
      setStatus("unsupported");
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) return;

    eyeAnchorYRef.current = null;
    let cancelled = false;
    let rafId = 0;
    let reportedReady = false;
    setStatus("loading");

    ensureCubismFramework();

    const subdelegate = new LAppSubdelegate();
    if (!subdelegate.initialize(canvas)) {
      setStatus("error");
      onError?.(new Error("Failed to acquire a WebGL2 context for the avatar canvas"));
      return;
    }
    subdelegateRef.current = subdelegate;
    subdelegate.resize();

    const url = `/avatars/${agentId}/${modelPath}`;
    const { dir, file } = splitModelPath(modelPath);
    const modelDir = `/avatars/${agentId}/${dir}`;

    // LAppModel.loadAssets() is fire-and-forget (its fetch chain only
    // console-logs on failure, never rejects/callbacks) — §20.8 needs a real
    // onError, so gate it behind our own existence check first.
    fetch(url)
      .then((res) => {
        if (cancelled) return;
        if (!res.ok) throw new Error(`HTTP ${res.status} loading ${url}`);
        const model = new LAppModel();
        model.setSubdelegate(subdelegate);
        modelRef.current = model;
        model.loadAssets(modelDir, file);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus("error");
        if (!loggedErrorRef.current) {
          loggedErrorRef.current = true;
          // eslint-disable-next-line no-console
          console.error("AvatarCanvas: failed to load Live2D model", err);
        }
        onError?.(err instanceof Error ? err : new Error(String(err)));
      });

    const resizeObserver = new ResizeObserver(() => subdelegate.resize());
    resizeObserver.observe(canvas);

    let lastFrameAt = 0;
    const tick = (now: number) => {
      if (cancelled) return;
      rafId = requestAnimationFrame(tick);
      if (now - lastFrameAt < 1000 / maxFpsRef.current) return;
      lastFrameAt = now;
      if (subdelegate.isContextLost()) return;

      const cfg = avatarConfigRef.current;
      LAppPal.updateTime();
      subdelegate.beginFrame();

      const model = modelRef.current;
      if (!model || model._state !== LoadStep.CompleteSetup) return;

      if (!reportedReady) {
        reportedReady = true;
        setStatus("ready");
        const group = motionGroupFor(aiStateRef.current, cfg);
        if (group) model.startRandomMotion(group, LAppDefine.PriorityNormal);
      }

      // lip-sync (SPEC §20.11): real or simulated audioLevel overrides
      // whatever the model's own motion curves set the mouth parameter to,
      // applied after update() (motion/physics) but before draw().
      let level = audioLevelRef.current;
      if (level === undefined && aiStateRef.current === "talking") {
        level = Math.max(0, Math.sin(performance.now() / 90)) * 0.6;
      }

      const canvasEl = subdelegate.getCanvas();
      const projection = new CubismMatrix44();
      const coreModel = model.getModel();
      if (coreModel) {
        if (coreModel.getCanvasWidth() > 1.0 && canvasEl.width < canvasEl.height) {
          model.getModelMatrix().setWidth(2.0);
          projection.scale(1.0, canvasEl.width / canvasEl.height);
        } else {
          projection.scale(canvasEl.height / canvasEl.width, 1.0);
        }
        const scale = cfg.scale ?? 1.0;
        projection.scaleRelative(scale, scale);
        const offsetX = cfg.offset_x ?? 0;
        const offsetY = cfg.offset_y ?? 0;
        if (offsetX || offsetY) {
          projection.translateRelative(offsetX / (canvasEl.width / 2), -offsetY / (canvasEl.height / 2));
        }
      }

      model.update();
      if (eyeAnchorYRef.current === null && coreModel) {
        const ext = modelYExtent(coreModel);
        if (ext) eyeAnchorYRef.current = eyeAnchorModelY(ext);
      }
      if (level !== undefined && coreModel) {
        for (const id of model._lipSyncIds) coreModel.setParameterValueById(id, level);
      }
      model.draw(projection);
      // SPEC §20.14.3: draw() mutates `projection` in place into the full MVP
      // (projection × modelMatrix) — hold onto it so a tap can invert the
      // exact matrix this frame was drawn with, instead of rebuilding one.
      lastMvpRef.current = projection;
      // frames actually rendered (after the fps cap) — lets tests measure the real rate
      const c = canvasEl as HTMLCanvasElement & { __frames?: number };
      c.__frames = (c.__frames ?? 0) + 1;
    };
    rafId = requestAnimationFrame(tick);

    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
      // See note above `loadAssets()` call: a load still in flight when this
      // fires (StrictMode double-mount in dev) leaks its own fetch promises,
      // which resolve into a since-released subdelegate/model — harmless
      // (nothing else references them), but not fully cancellable without
      // forking the vendored loadAssets() to accept an abort signal.
      modelRef.current?.release();
      modelRef.current = null;
      subdelegate.release();
      subdelegateRef.current = null;
    };
    // avatarConfig intentionally excluded — the rAF loop re-reads it live
    // through avatarConfigRef (and the aiState effect below reads the fresh
    // prop), without tearing down the WebGL context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, modelPath, enabled]);

  // -- aiState -> motion/expression.
  useEffect(() => {
    const model = modelRef.current;
    if (!model || status !== "ready") return;
    const group = motionGroupFor(aiState, avatarConfig);
    if (group) model.startRandomMotion(group, LAppDefine.PriorityNormal);
    const expr = avatarConfig.expression_map?.[aiState];
    if (expr) model.setExpression(expr);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiState, status]);

  // -- mouse interaction (SPEC §20.14, Phase 21). Two channels, deliberately
  // different gating (§20.14.1): look-follow (setDragging) runs unconditionally
  // below; hover/tap reactions check canReactToTouch(aiStateRef.current) —
  // aiState !== "idle" skips them without touching look-follow.
  useEffect(() => {
    if (!enabled) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ndcFromEvent = (e: MouseEvent) => clientPointToNdc(e.clientX, e.clientY, canvas.getBoundingClientRect());

    // No visual cue otherwise tells the user the avatar is clickable at all —
    // this is pure UX polish, not part of SPEC §20.14.4's reaction table.
    const setCursor = (reactive: boolean) => {
      canvas.style.cursor = reactive ? "pointer" : "";
    };

    const clearHover = (model: LAppModel) => {
      if (hoveringHeadRef.current) {
        hoveringHeadRef.current = false;
        model.clearExpression();
      }
      setCursor(false);
    };

    const updateHover = (model: LAppModel, ndc: { x: number; y: number }) => {
      if (!canReactToTouch(aiStateRef.current)) {
        clearHover(model);
        return;
      }
      const mvp = lastMvpRef.current;
      if (!mvp) return;
      const modelPoint = invertMvpPoint(mvp, ndc);
      const overHead = model.hitTest(LAppDefine.HitAreaNameHead, modelPoint.x, modelPoint.y);
      if (overHead && !hoveringHeadRef.current) {
        hoveringHeadRef.current = true;
        model.setRandomExpression();
      } else if (!overHead && hoveringHeadRef.current) {
        hoveringHeadRef.current = false;
        model.clearExpression();
      }
      // Body has no hover *expression* (§20.14.4 only defines one for Head),
      // but the cursor still affords it as tappable — one extra hitTest, only
      // when Head already missed.
      const overBody = overHead ? false : model.hitTest(LAppDefine.HitAreaNameBody, modelPoint.x, modelPoint.y);
      setCursor(overHead || overBody);
    };

    // window-level, not canvas-level: the overlay/chat still needs to know
    // where the pointer is once it leaves the model's box, or look-follow
    // would freeze the instant the cursor left it (§20.14.2).
    const onMouseMove = (e: MouseEvent) => {
      const model = modelRef.current;
      if (!model || model._state !== LoadStep.CompleteSetup) return;
      const ndc = ndcFromEvent(e);
      // Look-follow: unconditional, no aiState gate. The target is relative to
      // the model's eyes, not the canvas centre (§20.14.2) — on a full-body
      // model the centre is the belly, and a cursor level with the eyes would
      // otherwise read as "look up ~21°".
      const mvp = lastMvpRef.current;
      const anchorY = eyeAnchorYRef.current;
      const anchorNdcY = mvp && anchorY !== null ? mvp.transformY(anchorY) : 0;
      const target = lookTarget(ndc, anchorNdcY);
      model.setDragging(target.x, target.y);
      // Test hooks (mirror the `__frames` counter above): let Playwright assert
      // the exact target reaching the model without needing a model whose
      // parameters visibly move.
      const hooks = canvas as HTMLCanvasElement & { __lastDragNdc?: { x: number; y: number }; __eyeAnchorNdcY?: number };
      hooks.__lastDragNdc = target;
      hooks.__eyeAnchorNdcY = anchorNdcY;
      // Hit-testing is real work (matrix invert + a vertex-bbox scan); skip it
      // whenever the pointer can't possibly be over the canvas — which is
      // most mousemoves on a chat page (typing, scrolling, reading a reply).
      const rect = canvas.getBoundingClientRect();
      const insideCanvas = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
      if (!insideCanvas) {
        clearHover(model);
        return;
      }
      updateHover(model, ndc);
    };

    const onMouseLeaveDoc = () => {
      const model = modelRef.current;
      if (!model) return;
      model.setDragging(0, 0);
      (canvas as HTMLCanvasElement & { __lastDragNdc?: { x: number; y: number } }).__lastDragNdc = { x: 0, y: 0 };
      clearHover(model);
    };

    const onCanvasDown = (e: MouseEvent) => {
      pointerDownRef.current = { x: e.screenX, y: e.screenY, t: performance.now() };
    };

    // §20.14.5: tap-vs-drag classification uses screenX/screenY, not
    // clientX/clientY — on the overlay, dragging moves the window under the
    // cursor, so client coordinates barely change during a real drag.
    const onWindowUp = (e: MouseEvent) => {
      const down = pointerDownRef.current;
      pointerDownRef.current = null;
      if (!down) return;
      if (!isTap(e.screenX - down.x, e.screenY - down.y, performance.now() - down.t)) return;
      if (!canReactToTouch(aiStateRef.current)) return;

      const model = modelRef.current;
      const mvp = lastMvpRef.current;
      if (!model || !mvp || model._state !== LoadStep.CompleteSetup) return;
      const now = performance.now();
      if (!canTap(lastReactionAtRef.current, now)) return; // dropped, not queued

      const modelPoint = invertMvpPoint(mvp, ndcFromEvent(e));
      if (model.hitTest(LAppDefine.HitAreaNameHead, modelPoint.x, modelPoint.y)) {
        lastReactionAtRef.current = now;
        model.setRandomExpression();
        return;
      }
      if (model.hitTest(LAppDefine.HitAreaNameBody, modelPoint.x, modelPoint.y)) {
        lastReactionAtRef.current = now;
        model.startRandomMotion(LAppDefine.MotionGroupTapBody, LAppDefine.PriorityForce);
      }
      // neither area hit: silent no-op (§20.14.6) — a model with no HitAreas
      // declared at all just never reaches past the two hitTest calls above.
    };

    window.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseleave", onMouseLeaveDoc);
    canvas.addEventListener("mousedown", onCanvasDown);
    window.addEventListener("mouseup", onWindowUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseleave", onMouseLeaveDoc);
      canvas.removeEventListener("mousedown", onCanvasDown);
      window.removeEventListener("mouseup", onWindowUp);
    };
  }, [enabled]);

  if (!enabled) return null;

  return (
    <div className={cn("relative", className)} data-avatar-status={status}>
      <canvas ref={canvasRef} className="block h-full w-full" />
      {status === "error" && <AvatarPlaceholder reason="Couldn't load this avatar" />}
      {status === "unsupported" && <AvatarPlaceholder reason="WebGL2 isn't available in this browser" />}
    </div>
  );
}

function AvatarPlaceholder({ reason }: { reason: string }) {
  return (
    <div
      role="img"
      aria-label={reason}
      title={reason}
      className="absolute inset-0 flex items-center justify-center rounded-lg border border-dashed border-border text-2xl text-muted"
    >
      🧑
    </div>
  );
}
