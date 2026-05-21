"""
Image processing tools — watermark + resize.
Business plan only.
Uses Pillow (PIL).
"""
import io
import os
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


POSITION_MAP = {
    "top_left":     lambda w, h, tw, th: (10, 10),
    "top_right":    lambda w, h, tw, th: (w - tw - 10, 10),
    "bottom_left":  lambda w, h, tw, th: (10, h - th - 10),
    "bottom_right": lambda w, h, tw, th: (w - tw - 10, h - th - 10),
    "center":       lambda w, h, tw, th: ((w - tw) // 2, (h - th) // 2),
}


def _get_font(size: int = 24):
    """Try to load a font, fall back to default."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def add_watermark(
    image_bytes: bytes,
    text: str,
    position: str = "bottom_right",
    font_size: int = 28,
    opacity: int = 180,      # 0-255
    text_color: tuple = (255, 255, 255),
    shadow_color: tuple = (0, 0, 0),
) -> bytes:
    """
    Add text watermark to image bytes.
    Returns modified image bytes as JPEG.
    """
    if not PIL_AVAILABLE:
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        w, h = img.size

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _get_font(font_size)

        # Get text size
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        pos_fn = POSITION_MAP.get(position, POSITION_MAP["bottom_right"])
        x, y = pos_fn(w, h, tw, th)

        # Shadow
        draw.text((x + 2, y + 2), text, font=font, fill=(*shadow_color, opacity))
        # Main text
        draw.text((x, y), text, font=font, fill=(*text_color, opacity))

        combined = Image.alpha_composite(img, overlay)
        result = combined.convert("RGB")

        out = io.BytesIO()
        result.save(out, format="JPEG", quality=92)
        return out.getvalue()

    except Exception:
        return image_bytes


def is_image_bytes(data: bytes) -> bool:
    """Quick check if bytes are likely an image."""
    if not data:
        return False
    # Check magic bytes: JPEG, PNG, WEBP
    return (
        data[:2] == b"\xff\xd8"       # JPEG
        or data[:8] == b"\x89PNG\r\n\x1a\n"   # PNG
        or data[8:12] == b"WEBP"      # WEBP
    )
