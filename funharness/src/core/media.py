"""Local image ingestion shared by file tools and session attachments."""
from __future__ import annotations

import base64
import io
import warnings
from pathlib import Path

from PIL import Image

from .content import ToolResult


MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
IMAGE_DETAILS = {"auto", "low", "high", "original"}


def is_image_file(path: str | Path) -> bool:
    """Sniff supported formats even when a screenshot has no/wrong extension."""
    path = Path(path)
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return True
    with path.open("rb") as handle:
        header = handle.read(16)
    return (
        header.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def read_image(path: str | Path, detail: str = "auto") -> ToolResult:
    """Validate and snapshot original pixels; never silently downscale screenshots."""
    if detail not in IMAGE_DETAILS:
        raise ValueError("detail must be auto, low, high, or original")
    path = Path(path)
    # A bounded read also handles files that grow between stat and read.
    with path.open("rb") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 32 MiB; crop or resize it before reading")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as img:
            mime_type = IMAGE_MIME_TYPES.get(img.format)
            if mime_type is None:
                raise ValueError("Supported image formats: PNG, JPEG, GIF, WebP")
            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image exceeds 40 million pixels; crop or resize it before reading")
            frames = getattr(img, "n_frames", 1)
            img.verify()
        # Verify alone does not decode JPEG pixels or detect all truncations.
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    text = f"Image read: {path} ({width}x{height}, {mime_type})."
    if frames > 1:
        text += " Animated image: only the first frame is guaranteed; extract individual frames for inspection."
    return ToolResult(content=[
        {"type": "text", "text": text},
        {"type": "image", "mime_type": mime_type,
         "data": base64.b64encode(data).decode("ascii"),
         "width": width, "height": height, "detail": detail,
         "source_path": str(path.resolve())},
    ])
