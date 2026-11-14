import io
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

UPLOAD_DIR = Path(os.environ.get("DELTACHAT_UPLOADS", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_STANDARD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_HD_BYTES = 15 * 1024 * 1024       # 15 MB
HD_TTL_SECONDS = 600                  # 10 minutes
TARGET_STANDARD_BYTES = 300 * 1024    # ~300 KB


def process_and_save_image(raw_bytes: bytes, original_filename: str, is_hd: bool = False) -> dict:
    """Validate, strip EXIF, compress (or keep HD), and save image to disk."""
    max_allowed = MAX_HD_BYTES if is_hd else MAX_STANDARD_BYTES
    if len(raw_bytes) > max_allowed:
        limit_mb = max_allowed // (1024 * 1024)
        raise ValueError(f"La imagen supera el límite máximo permitido de {limit_mb} MB.")

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
    except Exception as exc:
        raise ValueError("El archivo no es una imagen válida (PNG, JPG, WebP, GIF).") from exc

    # Convert to RGB/RGBA cleanly while stripping EXIF/GPS metadata
    if img.mode in ("P", "LA"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    prefix = "hd" if is_hd else "img"
    file_id = f"{prefix}_{uuid.uuid4().hex[:12]}.webp"
    dest_path = UPLOAD_DIR / file_id

    out_buf = io.BytesIO()
    if is_hd:
        # Preserve full resolution in clean lossless/high-quality WebP without EXIF
        img.save(out_buf, format="WEBP", quality=92, method=4)
        width, height = img.size
        expires_at: Optional[float] = time.time() + HD_TTL_SECONDS
    else:
        # Resize proportionally to max 1280px and compress to ~300 KB
        img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        width, height = img.size
        quality = 80
        while quality >= 40:
            out_buf = io.BytesIO()
            img.save(out_buf, format="WEBP", quality=quality, method=4)
            if out_buf.tell() <= TARGET_STANDARD_BYTES or quality == 40:
                break
            quality -= 10
        expires_at = None

    data = out_buf.getvalue()
    dest_path.write_bytes(data)
    size_kb = max(1, round(len(data) / 1024))

    safe_name = Path(original_filename or "imagen.webp").stem[:40] + ".webp"
    return {
        "image_url": f"/uploads/{file_id}",
        "image_name": safe_name,
        "image_size_kb": size_kb,
        "image_dims": f"{width}x{height}",
        "is_hd": is_hd,
        "expires_at": expires_at,
    }


def delete_upload_file(image_url: Optional[str]) -> None:
    """Safely remove an image file inside UPLOAD_DIR."""
    if not image_url:
        return
    filename = Path(image_url).name
    target = (UPLOAD_DIR / filename).resolve()
    if target.parent == UPLOAD_DIR.resolve() and target.is_file():
        try:
            target.unlink()
        except OSError:
            pass
