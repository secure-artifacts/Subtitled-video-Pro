import hashlib
import json
import os
import re
import tempfile


PREVIEW_PROXY_VERSION = 5
PREVIEW_PROXY_HEIGHT = 540
PREVIEW_PROXY_FPS = 24
PREVIEW_PROXY_DIR = "subtitle_composer_preview_proxies"
TIMELINE_PREVIEW_PROXY_VERSION = 2
TIMELINE_PREVIEW_PROXY_DIR = "subtitle_composer_timeline_preview_proxies"
TIMELINE_PROXY_IMAGE_EXTS = (".jpg", ".jpeg", ".png")

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


def _even_dimension(value, minimum=2):
    try:
        value = int(round(float(value or minimum)))
    except Exception:
        value = int(minimum or 2)
    value = max(int(minimum or 2), value)
    return value if value % 2 == 0 else value - 1 if value > minimum else value + 1


def timeline_preview_dimensions(proj_w, proj_h, proxy_height=None, proxy_fps=None, proxy_crf=None):
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    try:
        proj_w = max(2, int(float(proj_w or 1080)))
        proj_h = max(2, int(float(proj_h or 1920)))
    except Exception:
        proj_w, proj_h = 1080, 1920
    max_h = max(240, min(1080, int(settings["height"])))
    if proj_h >= proj_w:
        height = min(max_h, proj_h)
        width = int(round(height * proj_w / max(1, proj_h)))
    else:
        width = min(max_h, proj_w)
        height = int(round(width * proj_h / max(1, proj_w)))
    return _even_dimension(width, 2), _even_dimension(height, 2)


def _timeline_clip_float(clip, key, default=0.0):
    try:
        return float((clip or {}).get(key, default) or default)
    except Exception:
        return float(default or 0.0)


def _timeline_preview_clip_entries(clips):
    entries = []
    for clip in clips or []:
        if not isinstance(clip, dict):
            continue
        source_path = os.path.abspath(clip.get("path", "") or "")
        if not source_path or not os.path.exists(source_path):
            continue
        start = _timeline_clip_float(clip, "start")
        end = _timeline_clip_float(clip, "end", start)
        duration = max(0.0, end - start)
        if duration <= 0.02:
            continue
        try:
            stat = os.stat(source_path)
            size = int(stat.st_size)
            mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
        except OSError:
            size = 0
            mtime_ns = 0
        source_in = max(0.0, _timeline_clip_float(clip, "source_in"))
        source_out = _timeline_clip_float(clip, "source_out", _timeline_clip_float(clip, "dur", duration))
        if source_out <= source_in:
            source_out = source_in + duration
        entries.append({
            "path": source_path,
            "size": size,
            "mtime_ns": mtime_ns,
            "start": round(start, 3),
            "end": round(end, 3),
            "source_in": round(source_in, 3),
            "source_out": round(source_out, 3),
            "dur": round(max(0.0, _timeline_clip_float(clip, "dur", duration)), 3),
            "speed": round(max(0.01, _timeline_clip_float(clip, "speed", 1.0)), 4),
        })
    entries.sort(key=lambda item: (item["start"], item["path"]))
    return entries


def timeline_preview_proxy_fingerprint(clips, proj_w, proj_h, proxy_height=None, proxy_fps=None, proxy_crf=None, target_duration=None):
    entries = _timeline_preview_clip_entries(clips)
    if len(entries) < 2:
        return {}
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    preview_w, preview_h = timeline_preview_dimensions(proj_w, proj_h, settings["height"], settings["fps"], settings["crf"])
    try:
        target_duration = float(target_duration or 0.0)
    except Exception:
        target_duration = 0.0
    if target_duration <= 0:
        target_duration = max(float(entry["end"]) for entry in entries)
    target_duration = round(max(0.05, target_duration), 3)
    return {
        "proxy_type": "timeline_preview",
        "proxy_version": TIMELINE_PREVIEW_PROXY_VERSION,
        "proxy_width": preview_w,
        "proxy_height": preview_h,
        "proxy_fps": settings["fps"],
        "proxy_crf": settings["crf"],
        "project_aspect": [int(float(proj_w or 1080)), int(float(proj_h or 1920))],
        "target_duration": target_duration,
        "clips": entries,
    }


def timeline_preview_proxy_path_for_clips(clips, proj_w, proj_h, proxy_height=None, proxy_fps=None, proxy_crf=None, target_duration=None):
    fingerprint = timeline_preview_proxy_fingerprint(clips, proj_w, proj_h, proxy_height, proxy_fps, proxy_crf, target_duration)
    token = fingerprint_token(fingerprint)
    if not token:
        return "", {}
    out_dir = os.path.join(tempfile.gettempdir(), TIMELINE_PREVIEW_PROXY_DIR)
    return os.path.join(out_dir, f"timeline-{token[:18]}.proxy.mp4"), fingerprint


def timeline_preview_proxy_is_ready(proxy_path, fingerprint):
    return bool(
        proxy_path
        and fingerprint
        and os.path.exists(proxy_path)
        and os.path.getsize(proxy_path) > 1024
    )


def build_timeline_preview_proxy_command(ffmpeg_cmd, clips, proxy_path, proj_w, proj_h, proxy_height=None, proxy_fps=None, proxy_crf=None, target_duration=None):
    settings = normalize_preview_proxy_settings(proxy_height, proxy_fps, proxy_crf)
    preview_w, preview_h = timeline_preview_dimensions(proj_w, proj_h, settings["height"], settings["fps"], settings["crf"])
    fps = int(settings["fps"])
    entries = _timeline_preview_clip_entries(clips)
    if len(entries) < 2:
        return []
    try:
        target_duration = float(target_duration or 0.0)
    except Exception:
        target_duration = 0.0
    if target_duration <= 0:
        target_duration = max(float(entry["end"]) for entry in entries)
    target_duration = max(0.05, target_duration)

    args = [ffmpeg_cmd, "-y", "-hide_banner", "-loglevel", "error"]
    filters = []
    labels = []
    input_index = 0
    cursor = 0.0
    segment_idx = 0

    def add_gap(duration):
        nonlocal segment_idx
        duration = max(0.0, float(duration or 0.0))
        if duration <= 0.02:
            return
        label = f"gap{segment_idx}"
        filters.append(f"color=c=black:s={preview_w}x{preview_h}:r={fps}:d={duration:.3f},format=yuv420p[{label}]")
        labels.append(f"[{label}]")
        segment_idx += 1

    for entry in entries:
        start = max(0.0, float(entry["start"]))
        end = max(start, float(entry["end"]))
        if start > cursor + 0.02:
            add_gap(start - cursor)
        duration = max(0.0, min(end, target_duration) - start)
        if duration <= 0.02:
            continue
        source_in = max(0.0, float(entry.get("source_in", 0.0) or 0.0))
        source_out = max(source_in + 0.001, float(entry.get("source_out", source_in + duration) or source_in + duration))
        source_len = max(0.001, source_out - source_in)
        ext = os.path.splitext(entry["path"])[1].lower()
        if ext in TIMELINE_PROXY_IMAGE_EXTS:
            args.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", entry["path"]])
        else:
            if duration > source_len + 0.04:
                args.extend(["-stream_loop", "-1"])
            args.extend(["-ss", f"{source_in:.3f}", "-t", f"{duration:.3f}", "-i", entry["path"]])
        label = f"v{segment_idx}"
        filters.append(
            f"[{input_index}:v]setpts=PTS-STARTPTS,"
            f"scale={preview_w}:{preview_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={preview_w}:{preview_h},setsar=1,fps={fps},"
            f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,format=yuv420p[{label}]"
        )
        labels.append(f"[{label}]")
        input_index += 1
        segment_idx += 1
        cursor = end
        if cursor >= target_duration - 0.02:
            break

    if target_duration > cursor + 0.02:
        add_gap(target_duration - cursor)
    if not labels:
        return []
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
    args.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[outv]",
        "-an",
        "-t", f"{target_duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(settings["crf"]),
        "-pix_fmt", "yuv420p",
        "-threads", "2",
        "-movflags", "+faststart",
        proxy_path,
    ])
    return args


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
