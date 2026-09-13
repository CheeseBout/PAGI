"""RAG / Knowledge Base (Phase 12, SPEC §14).

Three data tiers, kept separate (SPEC §14.1):
  - knowledge  -> ``kb_*`` tables, this package  (RAG)
  - contextual -> ``memory_chunks``               (core/memory_store.py)
  - operational-> ``sandbox/workspaces``          (file tools)
"""

from __future__ import annotations

from .config import RagConfig, resolve_config

__all__ = ["RagConfig", "resolve_config"]
