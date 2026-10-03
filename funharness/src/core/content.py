"""Provider-independent, JSON-serializable message content.

Media is a snapshot, not a path to reopen on every inference. Keeping bytes in
the session makes replay/branching independent of later edits to the source.
New modalities belong here and in the provider adapter, not in agent loops.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


class ImageBlock(TypedDict):
    type: Literal["image"]
    mime_type: str
    data: str  # base64 bytes, never a text prompt
    width: int
    height: int
    detail: str
    source_path: str


Content = str | list[TextBlock | ImageBlock]


def content_text(content: Content | None) -> str:
    """Safe text projection for hooks, UI, logs, titles and summaries."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        kind = block.get("type", "unknown")
        if kind == "text":
            parts.append(block.get("text", ""))
        elif kind == "image":
            parts.append(
                f"[Image: {block.get('source_path', '')} "
                f"{block.get('width', '?')}x{block.get('height', '?')} "
                f"{block.get('mime_type', '')}]"
            )
        else:
            # Never stringify unknown binary payloads into logs/prompts.
            parts.append(f"[{kind} content]")
    return "\n".join(parts)


@dataclass
class ToolResult:
    """Model content plus optional UI-only metadata (never sent to the model)."""

    content: Content
    display: dict[str, Any] | None = None
    is_error: bool | None = None

    def __str__(self) -> str:
        return content_text(self.content)


def tool_content(result: Any) -> Content:
    """Preserve structured tool results while accepting legacy string tools."""
    if isinstance(result, ToolResult):
        return result.content
    if isinstance(result, list) and all(isinstance(block, dict) and "type" in block for block in result):
        return result
    return str(result)


def append_text(content: Content, text: str) -> Content:
    if not text:
        return content
    if isinstance(content, str):
        return content + text
    return [*content, {"type": "text", "text": text}]


def truncate_content(content: Content, max_chars: int) -> Content:
    """Truncate only text blocks, never slice or stringify media payloads."""
    if isinstance(content, str):
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + f"\n...(truncated, was {len(content)} chars)"
    result = []
    remaining = max_chars
    for block in content:
        if block.get("type") == "text":
            text = block["text"]
            result.append({"type": "text", "text": truncate_content(text, remaining)})
            remaining = max(0, remaining - len(text))
        else:
            result.append(block)
    return result


def estimate_text_tokens(text: str) -> int:
    """Conservative mixed-language estimate; do not treat CJK as ASCII / 4."""
    non_ascii = sum(ord(char) > 127 for char in text)
    return (len(text) - non_ascii + 2) // 3 + non_ascii * 2


def estimate_content_tokens(content: Content | None) -> int:
    """Approximation only; actual provider usage is authoritative."""
    tokens = estimate_text_tokens(content_text(content))
    if isinstance(content, list):
        for block in content:
            if block.get("type") == "image":
                width = max(1, int(block.get("width", 1024)))
                height = max(1, int(block.get("height", 1024)))
                tokens += 256 if block.get("detail") == "low" else max(256, ((width + 15) // 16) * ((height + 15) // 16))
            elif block.get("type") == "image_url":
                tokens += 256 if block.get("image_url", {}).get("detail") == "low" else 4096
    return tokens
