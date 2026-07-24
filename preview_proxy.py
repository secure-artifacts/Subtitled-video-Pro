import hashlib
import json
import os
import re
import tempfile


PREVIEW_PROXY_VERSION = 5
PREVIEW_PROXY_HEIGHT = 540
PREVIEW_PROXY_FPS = 24
PREVIEW_PROXY_DIR = "subtitle_composer_preview_proxies"
AUTO_PROXY_SIZE_BYTES = 180 * 1024 * 1024
AUTO_PROXY_DURATION_SECONDS = 90.0
AUTO_PROXY_PIXEL_COUNT = 1920 * 1080

PROXY_STATUS_PENDING = "pending"
PROXY_STATUS_GENERATING = "generating"
PROXY_STATUS_READY = "ready"
PROXY_STATUS_FAILED = "failed"


def normalize_preview_proxy_settings(proxy_height=None, proxy_fps=None, proxy_crf=None):
    try:
        height = int(float(proxy_height or PREVIEW_PROXY_HEIGHT))
    except Exception:
        height = PREVIEW_PROXY_HEIGHT
    try:
        fps = int(float(proxy_fps or PREVIEW_PROXY_FPS))
    except Exception:
        fps = PREVIEW_PROXY_FPS
    try:
        crf = int(float(proxy_crf or 26))
    except Exception:
        crf = 26
    height = max(240, min(1080, height))
    fps = max(12, min(30, fps))
    crf = max(18, min(34, crf))
    return {"height": height, "fps": fps, "crf": crf}


def source_fingerprint(source_path, proxy_height=None, proxy_fps=None, proxy_crf=None):
    source_path = os.path.abspath(source_path or "")
    try:
        stat = os.stat(source_path)
    except OSError:
        return {}
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    return {
        "path": source_path,
        "size": int(stat.st_size),
        "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
        "proxy_version": PREVIEW_PROXY_VERSION,
        "proxy_height": settings["height"],
        "proxy_fps": settings["fps"],
        "proxy_crf": settings["crf"],
    }


def fingerprint_token(fingerprint):
    if not fingerprint:
        return ""
    payload = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def proxy_cache_dir():
    return os.path.join(tempfile.gettempdir(), PREVIEW_PROXY_DIR)


def _safe_proxy_stem(source_path):
    stem = os.path.splitext(os.path.basename(source_path or ""))[0] or "video"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return (stem or "video")[:48]


def preview_proxy_path_for_source(source_path, proxy_height=None, proxy_fps=None, proxy_crf=None):
    fingerprint = source_fingerprint(source_path, proxy_height, proxy_fps, proxy_crf)
    token = fingerprint_token(fingerprint)
    if not token:
        return "", {}
    filename = f"{_safe_proxy_stem(source_path)}-{token[:16]}.proxy.mp4"
    return os.path.join(proxy_cache_dir(), filename), fingerprint


def preview_proxy_is_ready(clip):
    if not isinstance(clip, dict):
        return False
    source_path = clip.get("path", "")
    proxy_path = clip.get("preview_proxy_path", "")
    if not source_path or not proxy_path:
        return False
    if clip.get("preview_proxy_status") != PROXY_STATUS_READY:
        return False
    if not os.path.exists(proxy_path) or os.path.getsize(proxy_path) <= 1024:
        return False
    expected = source_fingerprint(
        source_path,
        clip.get("preview_proxy_height"),
        clip.get("preview_proxy_fps"),
        clip.get("preview_proxy_crf"),
    )
    return bool(expected and clip.get("preview_proxy_fingerprint") == expected)


def clip_should_auto_proxy(clip):
    if not isinstance(clip, dict):
        return False
    path = clip.get("path", "")
    try:
        file_size = os.path.getsize(path)
    except OSError:
        file_size = 0
    try:
        duration = float(clip.get("dur", 0.0) or 0.0)
    except Exception:
        duration = 0.0
    try:
        width = int(float(clip.get("width", 0) or 0))
        height = int(float(clip.get("height", 0) or 0))
    except Exception:
        width = height = 0
    pixel_count = width * height
    return (
        file_size >= AUTO_PROXY_SIZE_BYTES
        or duration >= AUTO_PROXY_DURATION_SECONDS
        or pixel_count > AUTO_PROXY_PIXEL_COUNT
    )


def preview_source_for_clip(clip):
    if preview_proxy_is_ready(clip):
        return clip.get("preview_proxy_path", "")
    if isinstance(clip, dict):
        return clip.get("path", "")
    return ""


def prepare_clip_for_preview_proxy(clip, proxy_height=None, proxy_fps=None, proxy_crf=None):
    if not isinstance(clip, dict):
        return "", {}, False
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    source_path = clip.get("path", "")
    if not source_path or not os.path.exists(source_path):
        clip["preview_proxy_status"] = PROXY_STATUS_FAILED
        clip["preview_proxy_error"] = "Source video is missing."
        return "", {}, False
    proxy_path, fingerprint = preview_proxy_path_for_source(
        source_path,
        settings["height"],
        settings["fps"],
        settings["crf"],
    )
    previous_fingerprint = clip.get("preview_proxy_fingerprint")
    if not proxy_path:
        clip["preview_proxy_status"] = PROXY_STATUS_FAILED
        clip["preview_proxy_error"] = "Could not build preview proxy path."
        return "", {}, False
    clip["preview_proxy_path"] = proxy_path
    clip["preview_proxy_fingerprint"] = fingerprint
    clip["preview_proxy_height"] = settings["height"]
    clip["preview_proxy_fps"] = settings["fps"]
    clip["preview_proxy_crf"] = settings["crf"]
    if preview_proxy_is_ready(clip):
        return proxy_path, fingerprint, False
    if os.path.exists(proxy_path) and os.path.getsize(proxy_path) > 1024:
        clip["preview_proxy_status"] = PROXY_STATUS_READY
        clip["preview_proxy_error"] = ""
        return proxy_path, fingerprint, False
    if previous_fingerprint == fingerprint and clip.get("preview_proxy_status") == PROXY_STATUS_FAILED:
        return proxy_path, fingerprint, False
    if previous_fingerprint != fingerprint or clip.get("preview_proxy_status") != PROXY_STATUS_GENERATING:
        clip["preview_proxy_status"] = PROXY_STATUS_PENDING
        clip["preview_proxy_error"] = ""
    return proxy_path, fingerprint, True


def build_preview_proxy_command(ffmpeg_cmd, source_path, proxy_path, proxy_height=None, proxy_fps=None, proxy_crf=None):
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    return [
        ffmpeg_cmd,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale=-2:{settings["height"]}:flags=fast_bilinear,fps={settings["fps"]}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(settings["crf"]),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        proxy_path,
    ]
