import io
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

UPLOAD_DIR = Path(os.environ.get("DELTACHAT_UPLOADS", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_BYTES = 25 * 1024 * 1024   # 25 MB max video limit (Decision 6B)
MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10 MB max audio limit
MAX_IMAGE_BYTES = 15 * 1024 * 1024   # 15 MB max image limit

ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
ALLOWED_AUDIO_EXTS = {".webm", ".ogg", ".mp3", ".wav", ".m4a", ".aac"}


def save_uploaded_media(
    raw_bytes: bytes,
    original_filename: str,
    media_type: str,
    thumbnail_bytes: Optional[bytes] = None,
    duration_seconds: Optional[float] = None,
    is_gif_mode: bool = False,
    is_nsfw: bool = False,
) -> dict:
    """Save an audio or video file to UPLOAD_DIR and optionally save its WebP thumbnail."""
    file_size = len(raw_bytes)
    ext = Path(original_filename or "").suffix.lower()

    if media_type == "video":
        if file_size > MAX_VIDEO_BYTES:
            raise ValueError(f"El video supera el límite máximo permitido de 25 MB ({round(file_size/(1024*1024), 1)} MB).")
        if ext and ext not in ALLOWED_VIDEO_EXTS:
            ext = ".mp4"
        elif not ext:
            ext = ".mp4"
        prefix = "vid"
    elif media_type == "audio":
        if file_size > MAX_AUDIO_BYTES:
            raise ValueError(f"El audio supera el límite máximo permitido de 10 MB ({round(file_size/(1024*1024), 1)} MB).")
        if ext and ext not in ALLOWED_AUDIO_EXTS:
            ext = ".webm"
        elif not ext:
            ext = ".webm"
        prefix = "aud"
    else:
        prefix = "med"
        if not ext:
            ext = ".bin"

    uid = uuid.uuid4().hex[:12]
    filename = f"{prefix}_{uid}{ext}"
    dest_path = UPLOAD_DIR / filename
    dest_path.write_bytes(raw_bytes)

    thumbnail_url: Optional[str] = None
    # Save WebP thumbnail if provided (e.g. first frame captured via canvas on client)
    if thumbnail_bytes and len(thumbnail_bytes) > 0:
        try:
            thumb_img = Image.open(io.BytesIO(thumbnail_bytes))
            thumb_name = f"thumb_{uid}.webp"
            thumb_path = UPLOAD_DIR / thumb_name
            thumb_img.thumbnail((640, 640), Image.Resampling.LANCZOS)
            thumb_img.save(thumb_path, format="WEBP", quality=75, method=3)
            thumbnail_url = f"/uploads/{thumb_name}"
        except Exception:
            # If thumbnail decoding fails, proceed without thumbnail
            pass

    size_kb = max(1, round(file_size / 1024))
    safe_name = Path(original_filename or f"{media_type}{ext}").name[:50]

    # Cloud storage fallback check (Decision 19B):
    # If CLOUDFLARE_R2_PUBLIC_URL or CLOUDINARY_URL is configured, prepend it; otherwise use local /uploads/
    r2_base = os.environ.get("CLOUDFLARE_R2_PUBLIC_URL", "").rstrip("/")
    media_url = f"{r2_base}/uploads/{filename}" if r2_base else f"/uploads/{filename}"

    return {
        "media_url": media_url,
        "media_name": safe_name,
        "media_size_kb": size_kb,
        "media_type": media_type,
        "media_duration": duration_seconds,
        "thumbnail_url": thumbnail_url,
        "is_gif_mode": bool(is_gif_mode),
        "is_nsfw": bool(is_nsfw),
    }


def delete_media_file(media_url: Optional[str]) -> None:
    """Safely delete uploaded media and its corresponding thumbnail if present."""
    if not media_url:
        return
    filename = Path(media_url).name
    target = (UPLOAD_DIR / filename).resolve()
    if target.parent == UPLOAD_DIR.resolve() and target.is_file():
        try:
            target.unlink()
        except OSError:
            pass

    # Also clean matching thumbnail if name started with vid_
    if filename.startswith("vid_"):
        raw_uid = filename[4:].split(".")[0]
        thumb_target = (UPLOAD_DIR / f"thumb_{raw_uid}.webp").resolve()
        if thumb_target.parent == UPLOAD_DIR.resolve() and thumb_target.is_file():
            try:
                thumb_target.unlink()
            except OSError:
                pass
