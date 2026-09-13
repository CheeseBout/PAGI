import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("WORKSPACE_ROOT", tempfile.mkdtemp(prefix="pagi-sbx-"))

import pytest  # noqa: E402

import fs_ops  # noqa: E402


def test_write_read_roundtrip():
    fs_ops.write_file("sess1", "a/b.txt", "hello")
    out = fs_ops.read_file("sess1", "a/b.txt")
    assert out["content"] == "hello"
    assert out["size_bytes"] == 5


def test_edit_file():
    fs_ops.write_file("sess1", "c.txt", "foo bar foo")
    assert fs_ops.edit_file("sess1", "c.txt", "foo", "baz", "all")["replacements_made"] == 2
    assert fs_ops.read_file("sess1", "c.txt")["content"] == "baz bar baz"


def test_list_and_search():
    fs_ops.write_file("sess2", "x.txt", "needle here\nother line")
    names = {e["name"] for e in fs_ops.list_files("sess2", None)["entries"]}
    assert "x.txt" in names
    matches = fs_ops.search_files("sess2", "needle")["matches"]
    assert matches and matches[0]["line_number"] == 1


def test_write_bytes_roundtrip():
    data = b"\x89PNG\r\n\x00\xff\x10binary"
    out = fs_ops.write_bytes("sessb", "img/logo.png", data)
    assert out["bytes_written"] == len(data)
    target = fs_ops.resolve("sessb", "img/logo.png")
    assert target.read_bytes() == data


def test_write_bytes_rejects_traversal():
    with pytest.raises(fs_ops.InvalidPath):
        fs_ops.write_bytes("sessb", "../evil.png", b"x")


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "a/../../b"])
def test_path_traversal_blocked(bad):
    with pytest.raises(fs_ops.InvalidPath):
        fs_ops.resolve("sess3", bad)


def test_bad_session_id():
    with pytest.raises(fs_ops.InvalidPath):
        fs_ops.workspace_dir("../evil")


def test_search_files_pagination():
    lines = "\n".join(f"needle {i}" for i in range(250))
    fs_ops.write_file("sess4", "many.txt", lines)

    first = fs_ops.search_files("sess4", "needle", offset=0, limit=200)
    assert len(first["matches"]) == 200
    assert first["offset"] == 0
    assert first["limit"] == 200
    assert first["has_more"] is True

    second = fs_ops.search_files("sess4", "needle", offset=200, limit=200)
    assert len(second["matches"]) == 50
    assert second["offset"] == 200
    assert second["has_more"] is False

    # pages don't overlap and together cover every match
    seen = {m["line_number"] for m in first["matches"]} | {m["line_number"] for m in second["matches"]}
    assert seen == set(range(1, 251))


def test_search_files_limit_is_clamped():
    fs_ops.write_file("sess5", "one.txt", "needle")
    out = fs_ops.search_files("sess5", "needle", limit=10_000)
    assert out["limit"] == fs_ops._SEARCH_MAX_LIMIT
