/**
 * Based on Live2D Inc.'s Cubism SDK for Web Samples `lappsubdelegate.ts`
 * (Live2D Open Software License), trimmed for PAGI (2D_PLAN.md §3.2).
 *
 * Dropped from the original: `LAppView`/`LAppSprite` (background + gear/power
 * button sprites, pinch-zoom/drag view manipulation) and all touch/pointer
 * handling — SPEC §17 doesn't call for any of that, PAGI's avatar is a fixed,
 * non-interactive chat companion. What's left is exactly the per-canvas
 * resource bundle `LAppModel` needs: its own GL context, its own texture
 * manager, its own Live2D manager. One instance == one `AvatarCanvas` mount;
 * `initialize()`/`release()` are meant to be called every mount/unmount, not
 * once per page load like the original Demo app's singleton usage.
 */

import { LAppGlManager } from './lappglmanager';
import { LAppTextureManager } from './lapptexturemanager';

export class LAppSubdelegate {
  public constructor() {
    this._canvas = null;
    this._glManager = new LAppGlManager();
    this._textureManager = new LAppTextureManager();
    this._frameBuffer = null;
  }

  public release(): void {
    this._textureManager?.release();
    this._textureManager = null;

    this._glManager?.release();
    this._glManager = null;
  }

  public initialize(canvas: HTMLCanvasElement): boolean {
    if (!this._glManager!.initialize(canvas)) {
      return false;
    }

    this._canvas = canvas;
    this._textureManager!.setGlManager(this._glManager!);

    const gl = this.getGl();

    if (!this._frameBuffer) {
      this._frameBuffer = gl.getParameter(gl.FRAMEBUFFER_BINDING);
    }

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    return true;
  }

  /** Resync `canvas.width/height` (device pixels) with its current CSS box —
   * call after a container resize, before the next `beginFrame()`. */
  public resize(): void {
    const canvas = this._canvas!;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    const gl = this.getGl();
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
  }

  /** Clear the canvas for a new frame — call once per `requestAnimationFrame`
   * tick before drawing any model. */
  public beginFrame(): void {
    const gl = this.getGl();
    gl.clearColor(0, 0, 0, 0);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.clearDepth(1.0);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }

  public isContextLost(): boolean {
    return this.getGl().isContextLost();
  }

  public getTextureManager(): LAppTextureManager {
    // non-null once initialize() has run — the only path any caller reaches
    // one of these getters from.
    return this._textureManager!;
  }

  public getFrameBuffer(): WebGLFramebuffer {
    return this._frameBuffer!;
  }

  public getCanvas(): HTMLCanvasElement {
    return this._canvas!;
  }

  public getGlManager(): LAppGlManager {
    return this._glManager!;
  }

  public getGl(): WebGLRenderingContext | WebGL2RenderingContext {
    return this._glManager!.getGl();
  }

  private _canvas: HTMLCanvasElement | null;
  private _textureManager: LAppTextureManager | null;
  private _frameBuffer: WebGLFramebuffer | null;
  private _glManager: LAppGlManager | null;
}
