"""On-disk copy of ingested source files so re-ingest works after a restart.

Mirrors ``core/attachments`` — files live under ``RAG_DOC_DIR`` (default
``data/kb_docs``), one per ``kb_documents.id``. URL sources keep only their URL
(re-fetched on re-ingest); text sources are stored as ``.txt``.
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(os.getenv("RAG_DOC_DIR", "./data/kb_docs")).expanduser()


def _dir() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT


def save(doc_id: str, data: bytes, ext: str = "") -> str:
    ext = ("." + ext.lstrip(".")) if ext else ""
    p = _dir() / f"{doc_id}{ext}"
    p.write_bytes(data)
    return p.name


def load(doc_id: str) -> tuple[bytes, str] | None:
    for p in _dir().glob(f"{doc_id}*"):
        if p.is_file():
            return p.read_bytes(), p.suffix.lstrip(".")
    return None


def delete(doc_id: str) -> None:
    for p in _dir().glob(f"{doc_id}*"):
        try:
            p.unlink()
        except OSError:
            pass
