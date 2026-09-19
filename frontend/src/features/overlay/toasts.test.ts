import { describe, expect, it } from "vitest";
import type { NotificationEvent } from "@/api/ws";
import { toastFor } from "./toasts";

const hidden = { windowVisible: false, overlaySessionId: "S" };
const visible = { windowVisible: true, overlaySessionId: "S" };

const approval = (session_id: string): NotificationEvent => ({
  type: "approval_pending",
  approval_id: "a1",
  session_id,
  tool_name: "execute_code",
  args_preview: "print(1)",
});

describe("approval toasts (SPEC §21.8)", () => {
  it("toasts when the overlay is hidden, and clicking returns to the overlay's own card", () => {
    const t = toastFor(approval("S"), hidden);
    expect(t).toMatchObject({ kind: "approval", approvalId: "a1", body: "execute_code" });
    expect(t?.target).toEqual({ type: "overlay-approval", sessionId: "S", approvalId: "a1" });
  });

  it("does not toast when the card is already on screen in the overlay's session", () => {
    expect(toastFor(approval("S"), visible)).toBeNull();
  });

  it("still toasts for another session even when the overlay is visible — its card lives on the web", () => {
    const t = toastFor(approval("OTHER"), visible);
    expect(t?.target).toEqual({ type: "web", path: "/?session=OTHER" });
  });

  it("never puts tool arguments in the toast", () => {
    const t = toastFor(approval("S"), hidden);
    expect(JSON.stringify(t)).not.toContain("print(1)");
  });

  it("approval_resolved / hello / ping produce no toast", () => {
    for (const e of [
      { type: "approval_resolved", approval_id: "a1", status: "approved" },
      { type: "hello", pending_approvals: 2 },
      { type: "ping" },
    ] as NotificationEvent[]) {
      expect(toastFor(e, hidden)).toBeNull();
    }
  });
});

describe("cron / project toasts (SPEC §21.10)", () => {
  it("cron ok and cron error are distinct kinds and open the run's session on the web", () => {
    const ok = toastFor(
      { type: "cron_run_finished", job_id: "j", job_name: "nightly", session_id: "s1", status: "ok", summary: "done" },
      visible,
    );
    const bad = toastFor(
      { type: "cron_run_finished", job_id: "j", job_name: "nightly", session_id: "s1", status: "error", summary: "" },
      visible,
    );
    expect(ok).toMatchObject({ kind: "cron", body: "done", target: { type: "web", path: "/?session=s1" } });
    expect(bad?.kind).toBe("cron-error");
    expect(bad?.body).not.toBe("");
  });

  it("project iteration reports the QA outcome and opens the projects page", () => {
    const t = toastFor(
      {
        type: "project_iteration_finished",
        project_run_id: "p",
        project_name: "demo",
        iteration_no: 3,
        qa_verdict: "fail",
        status: "qa_failed",
      },
      hidden,
    );
    expect(t?.body).toContain("QA không đạt");
    expect(t?.target).toEqual({ type: "web", path: "/settings/projects" });
  });

  it("an errored iteration says so, and a pause says why", () => {
    const err = toastFor(
      {
        type: "project_iteration_finished",
        project_run_id: "p",
        project_name: "demo",
        iteration_no: 1,
        qa_verdict: null,
        status: "error",
      },
      hidden,
    );
    expect(err?.body).toContain("lỗi");
    const paused = toastFor(
      { type: "project_paused", project_run_id: "p", project_name: "demo", reason: "budget" },
      hidden,
    );
    expect(paused).toMatchObject({ kind: "project-paused" });
    expect(paused?.body).toContain("ngân sách");
  });

  it("cron/project always toast, whether the window is visible or not", () => {
    const e: NotificationEvent = { type: "project_paused", project_run_id: "p", project_name: "d", reason: "qa_fail_streak" };
    expect(toastFor(e, visible)).not.toBeNull();
    expect(toastFor(e, hidden)).not.toBeNull();
  });
});
