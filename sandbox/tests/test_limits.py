import threading
import time

import pytest

import limits


def test_exec_slot_serialises_one_session():
    held = threading.Event()
    release = threading.Event()

    def worker():
        with limits.exec_slot("sessA"):
            held.set()
            release.wait(timeout=2)

    t = threading.Thread(target=worker)
    t.start()
    assert held.wait(timeout=2)

    with pytest.raises(limits.SandboxBusy):
        with limits.exec_slot("sessA"):
            pass

    release.set()
    t.join(timeout=2)

    # slot is free again for the same session
    with limits.exec_slot("sessA"):
        pass


def test_check_quota(tmp_path, monkeypatch):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)

    monkeypatch.setattr(limits, "_QUOTA_MB", 0)
    limits.check_quota(tmp_path, 10 * 1024 * 1024)  # disabled -> never raises

    monkeypatch.setattr(limits, "_QUOTA_MB", 1)
    limits.check_quota(tmp_path, 10)  # 2 KB on disk, well under 1 MiB
    with pytest.raises(limits.QuotaExceeded):
        limits.check_quota(tmp_path, 2 * 1024 * 1024)


def test_stats_shape():
    s = limits.stats()
    assert {"active", "max", "quota_mb"} <= set(s)
    assert s["active"] == 0
