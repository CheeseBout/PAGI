"""Sandbox service settings (SPEC §11.3)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    internal_token: str = os.getenv("SANDBOX_INTERNAL_TOKEN", "dev-internal-token")
    workspace_root: str = os.getenv("WORKSPACE_ROOT", "/workspaces")
    memory_limit: str = os.getenv("SANDBOX_MEMORY_LIMIT", "512m")
    cpu_limit: float = float(os.getenv("SANDBOX_CPU_LIMIT", "1.0"))
    pids_limit: int = int(os.getenv("SANDBOX_PIDS_LIMIT", "64"))
    timeout_seconds: int = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "30"))
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "pagi-sandbox:latest")
    # When Docker is unreachable, fall back to running code as a local
    # subprocess in the workspace dir. UNSAFE — dev convenience only.
    allow_subprocess_fallback: bool = os.getenv("SANDBOX_SUBPROCESS_FALLBACK", "true").lower() == "true"

    # Dedicated network for ephemeral exec containers + iptables egress filter
    # blocking RFC1918/loopback/link-local (incl. the cloud metadata IP) while
    # still allowing outbound Internet (PLAN §1, SPEC §5.2). Linux Docker hosts
    # only — best-effort, never fatal if unsupported (see network_guard.py).
    exec_network_name: str = os.getenv("SANDBOX_EXEC_NETWORK", "pagi-exec")
    exec_network_subnet: str = os.getenv("SANDBOX_EXEC_SUBNET", "172.28.0.0/16")
    enable_egress_filter: bool = os.getenv("SANDBOX_ENABLE_EGRESS_FILTER", "true").lower() == "true"


config = Config()
