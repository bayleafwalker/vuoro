"""Text extraction for search. Never executes document content.

Markdown and plain text are extracted with heading anchors; PDF text with
page anchors when ``pypdf`` is installed. Everything else is ``unsupported``
and stays downloadable.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

EXTRACTOR = "vuoro-workspace-extract"
EXTRACTOR_VERSION = "1"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Extraction:
    state: str  # ready | unsupported | failed
    text: str = ""
    anchors: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _markdown_anchors(text: str) -> list[dict[str, Any]]:
    return [
        {"kind": "heading", "level": len(m.group(1)), "text": m.group(2), "offset": m.start()}
        for m in _HEADING_RE.finditer(text)
    ]


def _pdf(data: bytes) -> Extraction:
    try:
        import pypdf  # type: ignore[import-not-found]
    except ImportError:
        return Extraction("unsupported", error="pypdf not installed")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        anchors: list[dict[str, Any]] = []
        offset = 0
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            anchors.append({"kind": "page", "page": number, "offset": offset})
            parts.append(text)
            offset += len(text) + 1
        return Extraction("ready", "\n".join(parts), anchors)
    except Exception as exc:  # malformed PDFs must not break finalize
        return Extraction("failed", error=f"{type(exc).__name__}: {exc}"[:500])


def extract(media_type: str, data: bytes) -> Extraction:
    if media_type in {"text/markdown", "text/x-markdown"} or media_type.startswith("text/"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            return Extraction("failed", error=str(exc)[:500])
        text = text.replace("\x00", "")
        anchors = _markdown_anchors(text) if "markdown" in media_type else []
        return Extraction("ready", text, anchors)
    if media_type == "application/pdf":
        return _pdf(data)
    return Extraction("unsupported")
