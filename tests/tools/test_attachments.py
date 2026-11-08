import io

import av
from PIL import Image

from mcp_jira.tools.jira.attachments import TEXT_LIMIT, AttachmentMeta, describe, safe_filename


def meta(name: str, mime: str) -> AttachmentMeta:
    return AttachmentMeta(id="1", filename=name, mime_type=mime, size=0)


def gif_bytes(frames: int = 10) -> bytes:
    images = [Image.new("RGB", (64, 32), (i * 20, 0, 0)) for i in range(frames)]
    buf = io.BytesIO()
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:], duration=200, loop=0)
    return buf.getvalue()


def mp4_bytes(seconds: int = 3, fps: int = 10) -> bytes:
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(seconds * fps):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 48), (i * 8 % 255, 80, 160)))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buf.getvalue()


def kinds(content) -> list[str]:
    return [c.type for c in content]


def test_animated_gif_becomes_timestamped_frames():
    out = describe(meta("demo.gif", "image/gif"), gif_bytes(), None, max_frames=4)
    assert "animated GIF, 10 frames, 2.0s" in out[0].text
    assert kinds(out).count("image") == 4
    assert out[1].text.startswith("Frame at ")


def test_video_becomes_sampled_frames():
    out = describe(meta("bug.mp4", "video/mp4"), mp4_bytes(), "/host/bug.mp4", max_frames=3)
    assert "64x48 video" in out[0].text
    assert "saved to /host/bug.mp4" in out[0].text
    assert kinds(out).count("image") == 3


def test_long_text_is_truncated_with_a_pointer():
    out = describe(meta("big.log", "text/plain"), b"x" * (TEXT_LIMIT + 10), None, max_frames=1)
    assert out[1].text.endswith("open it in Jira for the rest)")


def test_unknown_binary_has_no_inline_preview():
    out = describe(meta("spec.pdf", "application/pdf"), b"%PDF-1.7", "/host/spec.pdf", max_frames=1)
    assert len(out) == 1
    assert "open the saved copy with local tools" in out[0].text


def test_corrupt_image_degrades_to_a_message():
    out = describe(meta("broken.png", "image/png"), b"not an image", None, max_frames=1)
    assert "preview failed" in out[0].text


def test_safe_filename_strips_paths_and_odd_characters():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("Screen Shot 2026-11-01 at 10.00.png") == "Screen_Shot_2026-11-01_at_10.00.png"
