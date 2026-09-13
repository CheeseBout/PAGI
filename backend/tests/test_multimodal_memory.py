"""to_provider_messages multimodal expansion (SPEC §4.2)."""

from app.core import attachments as att
from app.core.memory import to_provider_messages
from app.db.models import Message

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_plain_message_unchanged():
    msgs = [Message(session_id="s", role="user", content="hi")]
    out = to_provider_messages("sys", msgs, session_id="s")
    assert out[1] == {"role": "user", "content": "hi"}


def test_image_attachment_becomes_parts_list(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "_root", lambda: tmp_path)
    meta = att.save("sess", filename="a.png", content_type="image/png", data=_PNG)

    m = Message(session_id="sess", role="user", content="what is this", attachments=[meta])
    out = to_provider_messages("sys", [m], session_id="sess", include_images=True)
    parts = out[1]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "what is this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_screenshot_attachment_gets_live_screen_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "_root", lambda: tmp_path)
    meta = att.save("sess", filename="screen-123456.jpg", content_type="image/jpeg", data=_PNG)

    m = Message(session_id="sess", role="user", content="what is on my screen", attachments=[meta])
    out = to_provider_messages("sys", [m], session_id="sess", include_images=True)
    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "what is on my screen"}
    assert parts[1]["type"] == "text"
    assert "live screenshot" in parts[1]["text"]
    assert parts[2]["type"] == "image_url"


def test_include_images_false_drops_image(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "_root", lambda: tmp_path)
    meta = att.save("sess", filename="a.png", content_type="image/png", data=_PNG)
    m = Message(session_id="sess", role="user", content="hello", attachments=[meta])
    out = to_provider_messages("sys", [m], session_id="sess", include_images=False)
    assert out[1]["content"] == "hello"


def test_text_attachment_inlined(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "_root", lambda: tmp_path)
    meta = att.save("sess", filename="note.txt", content_type="text/plain", data=b"secret notes")
    m = Message(session_id="sess", role="user", content="summarise", attachments=[meta])
    out = to_provider_messages("sys", [m], session_id="sess")
    content = out[1]["content"]
    assert isinstance(content, str)
    assert "secret notes" in content
    assert "[Attached file: note.txt]" in content
