"""Egress filtering for ephemeral exec containers (PLAN §1, §10 risk table; SPEC §5.2).

Ephemeral containers created by `docker_runner` join a dedicated bridge network
(`exec_network_name`) instead of the default `bridge`. On startup we best-effort
install iptables rules — in Docker's own `DOCKER-USER` chain, the documented safe
customization point evaluated before Docker's own forwarding rules — that DROP
traffic *sourced from that network* destined to RFC1918/loopback/link-local
(which covers the 169.254.169.254 cloud metadata IP) while leaving outbound
Internet untouched.

This only works on a real Linux Docker host (the VPS deployment PLAN assumes).
On Docker Desktop (Windows/Mac) `--network host` for the helper container is
unreliable or unsupported, so this degrades to "unavailable" rather than failing
the service — `execute_code` still works, just without the extra network guard;
that gap is already called out in the README.
"""

from __future__ import annotations

import logging

from config import config

log = logging.getLogger("pagi.sandbox.network")

_CHAIN = "PAGI-SANDBOX-EGRESS"
_BLOCKED_CIDRS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",  # covers the cloud metadata endpoint 169.254.169.254
]

_status = "disabled"


def status() -> str:
    return _status


def _docker_client():
    import docker

    client = docker.from_env()
    client.ping()
    return client


def ensure_network() -> str | None:
    """Create (or reuse) the dedicated bridge network for exec containers.

    Returns the network name to pass to `containers.run(network=...)`, or None
    if Docker is unreachable (caller should fall back to the default bridge).
    """
    try:
        client = _docker_client()
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("docker_unreachable_for_network_setup: %s", exc)
        return None

    name = config.exec_network_name
    try:
        client.networks.get(name)
        return name
    except Exception:
        pass  # doesn't exist yet

    try:
        import docker.types

        ipam = docker.types.IPAMConfig(
            pool_configs=[docker.types.IPAMPool(subnet=config.exec_network_subnet)]
        )
        client.networks.create(name, driver="bridge", ipam=ipam, check_duplicate=True)
        log.info("created_exec_network name=%s subnet=%s", name, config.exec_network_subnet)
        return name
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("exec_network_create_failed: %s", exc)
        return None


def apply_egress_rules() -> bool:
    """Best-effort: install the DOCKER-USER DROP rules via a short-lived,
    elevated helper container (network_mode=host + NET_ADMIN). Idempotent."""
    global _status

    if not config.enable_egress_filter:
        _status = "disabled"
        return False

    try:
        client = _docker_client()
    except Exception as exc:  # pragma: no cover
        log.warning("docker_unreachable_for_egress_rules: %s", exc)
        _status = "unavailable"
        return False

    subnet = config.exec_network_subnet
    drops = "\n".join(f"iptables -A {_CHAIN} -d {cidr} -j DROP" for cidr in _BLOCKED_CIDRS)
    script = f"""
set -e
apk add --no-cache iptables >/dev/null 2>&1 || true
iptables -N {_CHAIN} 2>/dev/null || true
iptables -F {_CHAIN}
{drops}
iptables -A {_CHAIN} -j RETURN
iptables -C DOCKER-USER -s {subnet} -j {_CHAIN} 2>/dev/null || iptables -I DOCKER-USER 1 -s {subnet} -j {_CHAIN}
echo PAGI_EGRESS_RULES_OK
"""
    try:
        output = client.containers.run(
            image="alpine:3.20",
            command=["sh", "-c", script],
            network_mode="host",
            cap_add=["NET_ADMIN"],
            remove=True,
            stdout=True,
            stderr=True,
        )
        ok = b"PAGI_EGRESS_RULES_OK" in output
        _status = "active" if ok else "unavailable"
        if ok:
            log.info("egress_rules_applied subnet=%s", subnet)
        else:  # pragma: no cover
            log.warning("egress_rules_unconfirmed output=%r", output[-500:])
        return ok
    except Exception as exc:  # pragma: no cover - environment dependent (e.g. Docker Desktop)
        log.warning("egress_rules_failed (expected on non-Linux Docker hosts): %s", exc)
        _status = "unavailable"
        return False


def setup() -> str:
    """Call once at service startup. Never raises."""
    global _status
    net = ensure_network()
    if net is None:
        _status = "unavailable"
        return _status
    apply_egress_rules()
    return _status
