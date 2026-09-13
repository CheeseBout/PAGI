"""Source -> plain text / Markdown (SPEC §14.3).

Everything is reduced to text before chunking. Markdown is the target shape
because it is compact and keeps structure the model can read.

Supported inputs: PDF (pypdf), HTML (BeautifulSoup), Markdown / plain text
(passthrough), JSON (pretty-printed). URL ingestion reuses ``fetch_url``'s SSRF
guard.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field

import httpx

from ..core.security import SSRFError, safe_async_client, ssrf_guard


@dataclass
class LoadedDoc:
    text: str
    title: str
    meta: dict = field(default_factory=dict)


class LoaderError(ValueError):
    pass


_TEXT_TYPES = ("text/plain", "text/markdown", "text/x-markdown", "application/x-markdown")


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise LoaderError("pypdf is not installed") from exc
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # pragma: no cover - malformed page
            continue
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _from_html(data: bytes) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise LoaderError("beautifulsoup4 is not installed") from exc
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)
    return (f"# {title}\n\n{body}") if title else body


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_bytes(*, filename: str, content_type: str, data: bytes) -> LoadedDoc:
    if not data:
        raise LoaderError("empty file")
    ct = (content_type or "").split(";")[0].strip().lower()
    name = filename or "document"
    stem = name.rsplit(".", 1)[0]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ct == "application/pdf" or ext == "pdf":
        text = _from_pdf(data)
        kind = "pdf"
    elif ct in ("text/html", "application/xhtml+xml") or ext in ("html", "htm"):
        text = _from_html(data)
        kind = "html"
    elif ct == "application/json" or ext == "json":
        try:
            text = json.dumps(json.loads(_decode(data)), indent=2, ensure_ascii=False)
        except ValueError:
            text = _decode(data)
        kind = "json"
    elif ct in _TEXT_TYPES or ct.startswith("text/") or ext in ("md", "markdown", "txt", "text", ""):
        text = _decode(data)
        kind = "markdown" if ext in ("md", "markdown") else "text"
    else:
        raise LoaderError(f"unsupported type: {content_type or ext or 'unknown'}")

    text = text.strip()
    if not text:
        raise LoaderError("no extractable text")
    return LoadedDoc(text=text, title=stem or name, meta={"loader": kind})


async def load_url(url: str) -> LoadedDoc:
    try:
        ssrf_guard(url)
    except SSRFError as exc:
        raise LoaderError(f"blocked by SSRF guard: {exc}") from exc
    try:
        async with safe_async_client(timeout=25.0) as client:
            resp = await client.get(url, headers={"User-Agent": "PAGI/0.1 (+rag ingest)"})
    except SSRFError as exc:
        raise LoaderError(f"blocked by SSRF guard: {exc}") from exc
    except httpx.HTTPError as exc:
        raise LoaderError(f"fetch failed: {exc}") from exc
    if resp.status_code >= 400:
        raise LoaderError(f"fetch returned HTTP {resp.status_code}")
    ct = resp.headers.get("content-type", "")
    doc = load_bytes(filename=url.rsplit("/", 1)[-1] or "page", content_type=ct, data=resp.content)
    doc.meta["source_url"] = url
    doc.title = doc.title or url
    return doc
