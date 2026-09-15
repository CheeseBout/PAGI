"""Workspace file operations with path-traversal protection (SPEC §6.2, §6.3)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from config import config


class InvalidPath(ValueError):
    pass


def workspace_dir(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""):
        raise InvalidPath("invalid session_id")
    root = Path(config.workspace_root).resolve()
    ws = (root / session_id).resolve()
    if root not in ws.parents and ws != root:
        raise InvalidPath("session workspace escapes root")
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def resolve(session_id: str, rel_path: str | None) -> Path:
    ws = workspace_dir(session_id)
    rel = (rel_path or "").strip()
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise InvalidPath("absolute paths are not allowed")
    target = (ws / os.path.normpath(rel)).resolve()
    if target != ws and ws not in target.parents:
        raise InvalidPath("path escapes the session workspace")
    return target


# ── operations ─────────────────────────────────────────────────────────
def read_file(session_id: str, path: str) -> dict:
    target = resolve(session_id, path)
    if not target.is_file():
        raise InvalidPath("file not found")
    data = target.read_bytes()
    return {"content": data.decode("utf-8", errors="replace"), "size_bytes": len(data)}


def write_file(session_id: str, path: str, content: str, mode: str = "overwrite") -> dict:
    target = resolve(session_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    open_mode = "a" if mode == "append" else "w"
    with open(target, open_mode, encoding="utf-8") as fh:
        fh.write(content)
    return {"bytes_written": len(content.encode("utf-8"))}


def write_bytes(session_id: str, path: str, data: bytes, mode: str = "overwrite") -> dict:
    target = resolve(session_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "ab" if mode == "append" else "wb") as fh:
        fh.write(data)
    return {"bytes_written": len(data)}


def edit_file(session_id: str, path: str, find: str, replace: str, occurrence: str = "first") -> dict:
    target = resolve(session_id, path)
    if not target.is_file():
        raise InvalidPath("file not found")
    text = target.read_text(encoding="utf-8", errors="replace")
    if find not in text:
        raise InvalidPath("`find` string not present in file")
    count = -1 if occurrence == "all" else 1
    new_text = text.replace(find, replace, count)
    made = text.count(find) if occurrence == "all" else 1
    target.write_text(new_text, encoding="utf-8")
    return {"replacements_made": made}


def delete_file(session_id: str, path: str) -> dict:
    target = resolve(session_id, path)
    if target.is_dir():
        shutil.rmtree(target)
        return {"deleted": True}
    if target.exists():
        target.unlink()
        return {"deleted": True}
    return {"deleted": False}


def list_files(session_id: str, path: str | None = None) -> dict:
    target = resolve(session_id, path)
    if not target.is_dir():
        raise InvalidPath("not a directory")
    entries = []
    for child in sorted(target.iterdir()):
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size_bytes": child.stat().st_size if child.is_file() else 0,
            }
        )
    return {"entries": entries}


_SEARCH_HARD_CAP = 2000
_SEARCH_DEFAULT_LIMIT = 200
_SEARCH_MAX_LIMIT = 500


def _paged(matches: list[dict], offset: int, limit: int, scan_cap_hit: bool) -> dict:
    page = matches[offset : offset + limit]
    # scan_cap_hit means the scan itself was truncated at _SEARCH_HARD_CAP, so
    # there may be even more matches than `matches` reflects — either way,
    # more exists beyond this page.
    has_more = scan_cap_hit or len(matches) > offset + limit
    return {"matches": page, "offset": offset, "limit": limit, "has_more": has_more}


def search_files(
    session_id: str,
    pattern: str,
    path: str | None = None,
    regex: bool = False,
    offset: int = 0,
    limit: int = _SEARCH_DEFAULT_LIMIT,
) -> dict:
    limit = max(1, min(limit, _SEARCH_MAX_LIMIT))
    # Always accumulate up to the hard cap (not just offset+limit) so `has_more`
    # can be computed correctly for the requested page — stopping early at
    # offset+limit would leave us unable to tell whether a later page exists.
    base = resolve(session_id, path)
    matches: list[dict] = []
    rg = shutil.which("rg")
    if rg:
        # No --max-count here: it caps matches *per file*, not overall, so it
        # can't be used to bound total work — the accumulation loop below does
        # that instead, uniformly with the pure-python fallback.
        cmd = [rg, "--line-number", "--no-heading", "--color=never"]
        if not regex:
            cmd.append("--fixed-strings")
        cmd += [pattern, str(base)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        scan_cap_hit = False
        for line in proc.stdout.splitlines():
            if len(matches) >= _SEARCH_HARD_CAP:
                scan_cap_hit = True
                break
            parts = line.split(":", 2)
            if len(parts) == 3:
                matches.append(
                    {"file": _relpath(parts[0], session_id), "line_number": int(parts[1]),
                     "line_content": parts[2]}
                )
        return _paged(matches, offset, limit, scan_cap_hit)

    # fallback: pure-python scan
    compiled = re.compile(pattern) if regex else None
    scan_cap_hit = False
    for root, _dirs, files in os.walk(base):
        for fname in files:
            fpath = Path(root) / fname
            try:
                for i, line in enumerate(fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    hit = compiled.search(line) if compiled else (pattern in line)
                    if hit:
                        matches.append(
                            {"file": _relpath(str(fpath), session_id), "line_number": i,
                             "line_content": line}
                        )
                        if len(matches) >= _SEARCH_HARD_CAP:
                            scan_cap_hit = True
                            return _paged(matches, offset, limit, scan_cap_hit)
            except (OSError, UnicodeError):
                continue
    return _paged(matches, offset, limit, scan_cap_hit)


def clone_workspace(from_session_id: str, to_session_id: str) -> dict:
    """Mirror one workspace into another (Phase 18: QA's isolated checkout).

    A host-level copy rather than a container-side ``git clone`` — containers
    only bind-mount one workspace dir each (``docker_runner.py``), so there is
    no path from which a container could reach a *second* session's directory.
    Always a full, exact mirror: the destination is wiped first so a stale
    file from a previous iteration's QA run can never linger.
    """
    src = workspace_dir(from_session_id)
    dst = workspace_dir(to_session_id)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return {"cloned": True}


def _relpath(abs_path: str, session_id: str) -> str:
    try:
        return str(Path(abs_path).resolve().relative_to(workspace_dir(session_id)))
    except ValueError:
        return abs_path
