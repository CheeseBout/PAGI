// What a notification-stream event turns into on the desktop (SPEC §21.7, §21.8, §21.10).
// Pure: the decision "toast or not, and what does clicking it do" is separate from
// the socket and from Electron, so it can be unit-tested.
import type { NotificationEvent } from "@/api/ws";

/** What the overlay does when the user clicks the toast. The shell only relays
 * this object back verbatim (desktop/src/main/policy.ts checks its size/shape). */
export type ToastTarget =
  | { type: "overlay-approval"; sessionId: string; approvalId: string }
  | { type: "web"; path: string };

export type ToastKind = "approval" | "cron" | "cron-error" | "project" | "project-paused";

export interface Toast {
  title: string;
  body: string;
  kind: ToastKind;
  target: ToastTarget;
  /** approvals only: lets `approval_resolved` close this exact toast */
  approvalId?: string;
}

export interface ToastContext {
  /** is the overlay window on screen right now (`!document.hidden`) */
  windowVisible: boolean;
  /** the overlay's own continuous session */
  overlaySessionId: string | null;
}

const QA_VERDICT_LABEL: Record<string, string> = { pass: "QA đạt", fail: "QA không đạt" };

const PAUSE_REASON_LABEL: Record<string, string> = {
  budget: "vượt ngân sách",
  qa_fail_streak: "QA không đạt liên tiếp",
};

export function webSessionPath(sessionId: string): string {
  return `/?session=${encodeURIComponent(sessionId)}`;
}

/** SPEC §21.8.2: an approval only needs a toast when the overlay can't already be
 * showing its card — hidden, or the approval belongs to some other session.
 * Every cron/project event always toasts. Returns null for events that don't. */
export function toastFor(event: NotificationEvent, ctx: ToastContext): Toast | null {
  switch (event.type) {
    case "approval_pending": {
      const cardAlreadyVisible = ctx.windowVisible && event.session_id === ctx.overlaySessionId;
      if (cardAlreadyVisible) return null;
      return {
        title: "PAGI cần bạn duyệt",
        body: event.tool_name,
        kind: "approval",
        approvalId: event.approval_id,
        target:
          event.session_id === ctx.overlaySessionId
            ? { type: "overlay-approval", sessionId: event.session_id, approvalId: event.approval_id }
            : // an approval in some other session (a web chat, a cron run) has its
              // card in the web UI — the overlay is bound to its own session only
              { type: "web", path: webSessionPath(event.session_id) },
      };
    }

    case "cron_run_finished":
      return {
        title: event.status === "ok" ? `Cron xong: ${event.job_name}` : `Cron lỗi: ${event.job_name}`,
        body: event.summary || (event.status === "ok" ? "Đã chạy xong." : "Lượt chạy thất bại."),
        kind: event.status === "ok" ? "cron" : "cron-error",
        target: { type: "web", path: webSessionPath(event.session_id) },
      };

    case "project_iteration_finished": {
      const outcome =
        event.status === "error"
          ? "gặp lỗi"
          : (event.qa_verdict && QA_VERDICT_LABEL[event.qa_verdict]) || "hoàn tất";
      return {
        title: `${event.project_name || "Dự án"} · vòng ${event.iteration_no}`,
        body: `Vòng ${event.iteration_no} ${outcome}.`,
        kind: "project",
        target: { type: "web", path: "/settings/projects" },
      };
    }

    case "project_paused":
      return {
        title: `${event.project_name || "Dự án"} đã tạm dừng`,
        body: `Lý do: ${PAUSE_REASON_LABEL[event.reason] ?? event.reason}.`,
        kind: "project-paused",
        target: { type: "web", path: "/settings/projects" },
      };

    default:
      return null; // hello / ping / approval_resolved
  }
}
