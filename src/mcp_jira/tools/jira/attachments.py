"""Turn a downloaded attachment into something a model can look at.

Images come back inline (downscaled JPEG). Animated GIFs and videos come back as evenly spaced
frames. Text-like files come back as text. When the server shares a filesystem with the agent (local
use), every file is also saved so the agent can open it with its own tools (PDFs, archives, ...).
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import av
from loguru import logger
from mcp_types import ImageContent, TextContent
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_DOWNLOAD_BYTES = 150_000_000
MAX_IMAGE_SIDE = 1568
JPEG_QUALITY = 85
TEXT_LIMIT = 40_000
TEXT_EXTENSIONS = {
    ".txt",
    ".log",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".har",
    ".html",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".kt",
    ".swift",
    ".go",
    ".rb",
    ".sh",
    ".ini",
    ".toml",
    ".env.example",
}
TEXT_MIME_EXTRA = {"application/json", "application/xml", "application/x-yaml", "application/sql"}

Content = TextContent | ImageContent


@dataclass(frozen=True)
class AttachmentMeta:
    id: str
    filename: str
    mime_type: str
    size: int

    @classmethod
    def from_api(cls, data: dict) -> AttachmentMeta:
        return cls(
            id=str(data["id"]),
            filename=data.get("filename") or f"attachment-{data['id']}",
            mime_type=(data.get("mimeType") or "application/octet-stream").lower(),
            size=int(data.get("size", 0)),
        )

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.filename.lower()).suffix

    @property
    def kind(self) -> str:
        if self.mime_type == "image/gif" or self.suffix == ".gif":
            return "gif"
        if self.mime_type.startswith("image/"):
            return "image"
        if self.mime_type.startswith("video/") or self.suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi"}:
            return "video"
        if self.mime_type.startswith("text/") or self.mime_type in TEXT_MIME_EXTRA or self.suffix in TEXT_EXTENSIONS:
            return "text"
        return "other"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", PurePosixPath(name).name).strip("._") or "file"
    return cleaned[-120:]


def save(meta: AttachmentMeta, data: bytes, downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    path = downloads_dir / f"{meta.id}_{safe_filename(meta.filename)}"
    path.write_bytes(data)  # same id -> same path: re-downloading is idempotent
    return path


def _jpeg(img: Image.Image) -> ImageContent:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        img = background
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return ImageContent(type="image", data=base64.b64encode(buf.getvalue()).decode(), mime_type="image/jpeg")


def _sample(count: int, wanted: int) -> list[int]:
    if count <= wanted:
        return list(range(count))
    return [int((i + 0.5) * count / wanted) for i in range(wanted)]


def gif_frames(data: bytes, max_frames: int) -> tuple[str, list[Content]]:
    with Image.open(io.BytesIO(data)) as img:
        total = getattr(img, "n_frames", 1)
        if total == 1:
            return f"{img.width}x{img.height} still GIF", [_jpeg(img)]
        durations = []
        for i in range(total):
            img.seek(i)
            durations.append(img.info.get("duration", 100) / 1000)
        starts = [sum(durations[:i]) for i in range(total)]
        out: list[Content] = []
        for index in _sample(total, max_frames):
            img.seek(index)
            out += [TextContent(type="text", text=f"Frame at {starts[index]:.1f}s"), _jpeg(img.copy())]
        return f"{img.width}x{img.height} animated GIF, {total} frames, {sum(durations):.1f}s", out


def video_frames(data: bytes, max_frames: int) -> tuple[str, list[Content]]:
    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.video[0]
        duration = container.duration / av.time_base if container.duration else 0.0
        size = f"{stream.codec_context.width}x{stream.codec_context.height}"
        out: list[Content] = []
        if duration <= 0:
            for frame in container.decode(stream):
                out += [TextContent(type="text", text="First frame"), _jpeg(frame.to_image())]
                break
            return f"{size} video, unknown duration", out
        for i in range(max_frames):
            at = duration * (i + 0.5) / max_frames
            container.seek(int(at * av.time_base), backward=True, any_frame=False)
            for frame in container.decode(stream):
                if frame.time is None or frame.time >= at - 0.05:
                    out += [TextContent(type="text", text=f"Frame at {at:.1f}s"), _jpeg(frame.to_image())]
                    break
        return f"{size} video, {duration:.1f}s", out


def describe(meta: AttachmentMeta, data: bytes, saved_at: str | None, max_frames: int) -> list[Content]:
    """Model-viewable content for one attachment; saved_at is where the agent can open the file, if anywhere."""
    where = f" · saved to {saved_at}" if saved_at else ""
    header = f"{meta.filename} · {meta.mime_type} · {len(data)} bytes{where}"
    elsewhere = "open the saved copy with local tools" if saved_at else "open it in Jira"
    try:
        match meta.kind:
            case "image":
                with Image.open(io.BytesIO(data)) as img:
                    info = f"{img.width}x{img.height} image"
                    body: list[Content] = [_jpeg(img)]
            case "gif":
                info, body = gif_frames(data, max_frames)
            case "video":
                info, body = video_frames(data, max_frames)
            case "text":
                text = data.decode("utf-8", errors="replace")
                cut = f"\n…(truncated; {elsewhere} for the rest)" if len(text) > TEXT_LIMIT else ""
                info, body = "text", [TextContent(type="text", text=text[:TEXT_LIMIT] + cut)]
            case _:
                info, body = f"no inline preview for this type; {elsewhere}", []
    except (UnidentifiedImageError, av.FFmpegError, OSError, IndexError) as exc:
        logger.warning(f"Could not preview attachment {meta.id} ({meta.mime_type}): {exc}")
        info, body = f"preview failed ({exc}); {elsewhere}", []
    return [TextContent(type="text", text=f"{header}\n{info}"), *body]
