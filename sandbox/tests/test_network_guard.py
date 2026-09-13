import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import docker_runner  # noqa: E402
import network_guard  # noqa: E402

_DOCKER_UP = docker_runner.docker_status() == "ok"


def test_ensure_network_never_raises():
    """Must degrade to None without Docker, and never raise either way."""
    result = network_guard.ensure_network()
    if _DOCKER_UP:
        assert result == network_guard.config.exec_network_name
    else:
        assert result is None


def test_apply_egress_rules_never_raises():
    result = network_guard.apply_egress_rules()
    assert isinstance(result, bool)
    if not _DOCKER_UP:
        assert result is False
        assert network_guard.status() == "unavailable"
    else:
        # Succeeds on a real Linux Docker host; degrades to "unavailable" on
        # Docker Desktop where `--network host` for the helper container isn't
        # fully supported — either way it must not raise.
        assert network_guard.status() in ("active", "unavailable")


def test_setup_never_raises():
    assert network_guard.setup() in ("unavailable", "disabled", "active")


def test_disabled_via_config(monkeypatch):
    # Config is a frozen dataclass — swap the module-level reference instead
    # of mutating it in place.
    monkeypatch.setattr(network_guard, "config", replace(network_guard.config, enable_egress_filter=False))
    assert network_guard.apply_egress_rules() is False
    assert network_guard.status() == "disabled"
