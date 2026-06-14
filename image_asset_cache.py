from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple


DEFAULT_IMAGE_PROXY_MAX_SIDE = 2048
DEFAULT_IMAGE_PROXY_MAX_PIXELS = 2_200_000

_ALPHA_FRIENDLY_EXTS = {".png", ".webp"}


@dataclass(frozen=True)
class ImageProxyInfo:
    original_path: str
    proxy_path: str
    width: int = 0
    height: int = 0
    max_side: int = DEFAULT_IMAGE_PROXY_MAX_SIDE
    used_proxy: bool = False
    reason: str = ""


def should_proxy_image(
    width: int,
    height: int,
    *,
    max_side: int = DEFAULT_IMAGE_PROXY_MAX_SIDE,
    max_pixels: int = DEFAULT_IMAGE_PROXY_MAX_PIXELS,
) -> bool:
    if width <= 0 or height <= 0:
        return False
    return width > max_side or height > max_side or (width * height) > max_pixels


def image_file_signature(path: os.PathLike[str] | str) -> str:
    src = Path(path)
    try:
        stat = src.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "missing:0"


def image_cache_key(
    path: os.PathLike[str] | str,
    *,
    max_side: int = DEFAULT_IMAGE_PROXY_MAX_SIDE,
    signature: Optional[str] = None,
) -> str:
    src = Path(path)
    sig = signature if signature is not None else image_file_signature(src)
    payload = f"{src.resolve() if src.exists() else src.absolute()}|{sig}|{max_side}"
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:20]


def default_image_proxy_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "SubtitleComposer" / "image_proxy_cache"
    return Path(tempfile.gettempdir()) / "SubtitleComposer" / "image_proxy_cache"


def proxy_extension_for_source(path: os.PathLike[str] | str, *, has_alpha: bool = False) -> str:
    ext = Path(path).suffix.lower()
    if has_alpha or ext in _ALPHA_FRIENDLY_EXTS:
        return ".png"
    return ".jpg"


def proxy_path_for_image(
    path: os.PathLike[str] | str,
    *,
    max_side: int = DEFAULT_IMAGE_PROXY_MAX_SIDE,
    cache_root: os.PathLike[str] | str | None = None,
    has_alpha: bool = False,
) -> Path:
    root = Path(cache_root) if cache_root is not None else default_image_proxy_cache_dir()
    suffix = proxy_extension_for_source(path, has_alpha=has_alpha)
    return root / f"{image_cache_key(path, max_side=max_side)}_{max_side}{suffix}"


def read_image_size(path: os.PathLike[str] | str) -> Tuple[int, int]:
    from PyQt6.QtGui import QImageReader

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        return 0, 0
    return int(size.width()), int(size.height())


def ensure_downscaled_image(
    path: os.PathLike[str] | str,
    *,
    max_side: int = DEFAULT_IMAGE_PROXY_MAX_SIDE,
    max_pixels: int = DEFAULT_IMAGE_PROXY_MAX_PIXELS,
    cache_root: os.PathLike[str] | str | None = None,
    quality: int = 92,
) -> tuple[str, ImageProxyInfo]:
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QImageReader

    src = Path(path)
    original = str(src)
    reader = QImageReader(original)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        return original, ImageProxyInfo(original, original, reason="size_unavailable")

    width, height = int(size.width()), int(size.height())
    if not should_proxy_image(width, height, max_side=max_side, max_pixels=max_pixels):
        return original, ImageProxyInfo(original, original, width, height, max_side, False, "within_limit")

    scaled_size = QSize(width, height)
    scaled_size.scale(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio)
    reader.setScaledSize(scaled_size)
    image = reader.read()
    if image.isNull():
        return original, ImageProxyInfo(original, original, width, height, max_side, False, "read_failed")

    has_alpha = image.hasAlphaChannel()
    proxy_path = proxy_path_for_image(src, max_side=max_side, cache_root=cache_root, has_alpha=has_alpha)
    if proxy_path.exists():
        return str(proxy_path), ImageProxyInfo(original, str(proxy_path), width, height, max_side, True, "cache_hit")

    proxy_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "PNG" if proxy_path.suffix.lower() == ".png" else "JPG"
    if not image.save(str(proxy_path), fmt, quality):
        return original, ImageProxyInfo(original, original, width, height, max_side, False, "save_failed")

    return str(proxy_path), ImageProxyInfo(original, str(proxy_path), width, height, max_side, True, "created")
