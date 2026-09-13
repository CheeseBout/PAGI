"""Run agent code in an ephemeral container (SPEC §6.1).

Each call: create a container from the sandbox image, mount the session
workspace at /workspace, apply --memory/--cpus/--pids-limit, enforce a wall
timeout, capture stdout/stderr/exit_code, then force-remove the container.

If the Docker daemon is unreachable and SANDBOX_SUBPROCESS_FALLBACK=true, the
code is instead run as a local subprocess (dev only — NOT isolated).
"""

from __future__ import annotations

import logging
import subprocess
import time
import uuid
from pathlib import Path

import network_guard
from config import config
from fs_ops import workspace_dir

log = logging.getLogger("pagi.sandbox.runner")

_LANG = {
    "python": ("py", ["python", "-I"]),
    "node": ("js", ["node"]),
    "bash": ("sh", ["bash"]),
}

# Label every exec container so a restarted service can find & reap orphans
# (e.g. the process died between `run` and `remove` — SPEC §9 checklist item
# "container luôn bị dọn dù crash giữa chừng").
_LABELS = {"pagi.sandbox": "exec"}


def _docker_client():
    import docker

    client = docker.from_env()
    client.ping()
    return client


def docker_status() -> str:
    try:
        _docker_client()
        return "ok"
    except Exception:
        return "unreachable"


def reap_orphans() -> int:
    """Force-remove any leftover exec containers from a crashed previous run.

    Called once at service startup (best-effort, never raises) — every exec
    container is `detach=True` + `remove(force=True)` in a finally block, but a
    hard process kill mid-request can still strand one.
    """
    try:
        client = _docker_client()
    except Exception:
        return 0
    removed = 0
    try:
        for c in client.containers.list(all=True, filters={"label": "pagi.sandbox=exec"}):
            try:
                c.remove(force=True)
                removed += 1
            except Exception as exc:  # pragma: no cover
                log.warning("reap_failed id=%s error=%s", c.id, exc)
    except Exception as exc:  # pragma: no cover
        log.warning("reap_list_failed: %s", exc)
    if removed:
        log.info("reaped_orphan_containers count=%d", removed)
    return removed


def execute(session_id: str, language: str, code: str, timeout_seconds: int | None) -> dict:
    if language not in _LANG:
        raise ValueError("language must be python | node | bash")
    timeout = int(timeout_seconds or config.timeout_seconds)
    timeout = max(1, min(timeout, 120))

    ws = workspace_dir(session_id)
    ext, argv = _LANG[language]
    script_name = f".pagi_run_{uuid.uuid4().hex}.{ext}"
    script_path = ws / script_name
    script_path.write_text(code, encoding="utf-8")

    try:
        try:
            return _run_in_container(ws, argv, script_name, timeout)
        except _DockerUnavailable:
            if not config.allow_subprocess_fallback:
                raise
            return _run_subprocess(ws, argv, script_path, timeout)
    finally:
        script_path.unlink(missing_ok=True)


class _DockerUnavailable(RuntimeError):
    pass


def _run_in_container(ws: Path, argv: list[str], script_name: str, timeout: int) -> dict:
    try:
        client = _docker_client()
    except Exception as exc:  # daemon down / SDK missing
        raise _DockerUnavailable(str(exc))

    started = time.perf_counter()
    exec_network = network_guard.ensure_network()
    run_kwargs = dict(
        image=config.sandbox_image,
        command=[*argv, f"/workspace/{script_name}"],
        working_dir="/workspace",
        volumes={str(ws): {"bind": "/workspace", "mode": "rw"}},
        mem_limit=config.memory_limit,
        nano_cpus=int(config.cpu_limit * 1e9),
        pids_limit=config.pids_limit,
        labels=_LABELS,
        detach=True,
        stdout=True,
        stderr=True,
    )
    if exec_network:
        run_kwargs["network"] = exec_network
    else:
        run_kwargs["network_mode"] = "bridge"
    container = client.containers.run(**run_kwargs)
    timed_out = False
    exit_code = -1
    try:
        try:
            result = container.wait(timeout=timeout)
            exit_code = int(result.get("StatusCode", -1))
        except Exception:
            timed_out = True
            try:
                container.kill()
            except Exception:
                pass
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _run_subprocess(ws: Path, argv: list[str], script_path: Path, timeout: int) -> dict:
    started = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(
            [*argv, str(script_path)],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "" if isinstance(exc.stderr, str) else "") + "\n[timed out]"
        exit_code = -1
    except FileNotFoundError as exc:
        stdout, stderr, exit_code = "", f"interpreter not found: {exc}", 127

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
