import functools
import json
import os
import re
import subprocess

from core import get_ffmpeg_cmd, get_ffprobe_cmd
from media_duration_policy import choose_timeline_media_duration


DEFAULT_VIDEO_SIZE = (1080, 1920)


def _creation_flags():
    return 0x08000000 if os.name == "nt" else 0


def media_fingerprint(file_path):
    path = os.path.abspath(file_path or "")
    try:
        stat = os.stat(path)
    except OSError:
        return ("", 0, 0)
    return (
        path,
        int(stat.st_size),
        int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
    )


def _run_probe(cmd, timeout=8):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        creationflags=_creation_flags(),
    )


def _first_positive_float(text):
    for line in str(text or "").splitlines():
        try:
            value = float(line.strip())
            if value > 0:
                return value
        except Exception:
            continue
    return 0.0


def _parse_ffmpeg_duration(stderr):
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", str(stderr or ""))
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@functools.lru_cache(maxsize=512)
def _exact_duration_cached(path, size, mtime_ns):
    if not path:
        return 0.0
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            timeout=8,
        )
        value = _first_positive_float(result.stdout)
        if value > 0:
            return value
    except Exception:
        pass

    try:
        result = _run_probe([get_ffmpeg_cmd(), "-i", path], timeout=5)
        return _parse_ffmpeg_duration(result.stderr)
    except Exception:
        return 0.0


def get_exact_duration(file_path):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return 0.0
    return _exact_duration_cached(path, size, mtime_ns)


@functools.lru_cache(maxsize=512)
def _packet_duration_cached(path, size, mtime_ns):
    if not path:
        return 0.0
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets,avg_frame_rate,r_frame_rate",
                "-of",
                "default=noprint_wrappers=1",
                path,
            ],
            timeout=20,
        )
        data = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        packets = int(float(data.get("nb_read_packets", "0") or 0))
        rate = parse_rate(data.get("avg_frame_rate")) or parse_rate(data.get("r_frame_rate"))
        if packets > 0 and rate > 0:
            return packets / rate
    except Exception:
        pass
    return 0.0


def estimate_video_packet_duration(file_path):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return 0.0
    return _packet_duration_cached(path, size, mtime_ns)


@functools.lru_cache(maxsize=512)
def _stream_duration_cached(path, size, mtime_ns, stream_selector):
    if not path:
        return 0.0
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-select_streams",
                stream_selector,
                "-show_entries",
                "stream=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            timeout=8,
        )
        probed_duration = _first_positive_float(result.stdout)
        if probed_duration > 0:
            return probed_duration
    except Exception:
        pass
    return get_exact_duration(path)


def get_stream_duration(file_path, stream_selector="v:0"):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return 0.0
    return _stream_duration_cached(path, size, mtime_ns, str(stream_selector or "v:0"))


def get_video_stream_duration(file_path):
    return get_stream_duration(file_path, "v:0")


def get_audio_stream_duration(file_path):
    return get_stream_duration(file_path, "a:0")


def _positive_float(value):
    try:
        value = float(value)
        return value if value > 0 else 0.0
    except Exception:
        return 0.0


@functools.lru_cache(maxsize=512)
def _video_import_metadata_cached(path, size, mtime_ns):
    if not path:
        return {
            "duration": 0.0,
            "exact_duration": 0.0,
            "video_duration": 0.0,
            "audio_duration": 0.0,
            "has_audio": False,
            "width": DEFAULT_VIDEO_SIZE[0],
            "height": DEFAULT_VIDEO_SIZE[1],
            "duration_info": {},
        }
    durations = {
        "exact": 0.0,
        "video": 0.0,
        "audio": 0.0,
        "packet": 0.0,
    }
    width, height = DEFAULT_VIDEO_SIZE
    has_audio = False
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,width,height,duration",
                "-of",
                "json",
                path,
            ],
            timeout=10,
        )
        payload = json.loads(result.stdout or "{}")
        durations["exact"] = _positive_float((payload.get("format") or {}).get("duration"))
        for stream in payload.get("streams", []) or []:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and durations["video"] <= 0:
                durations["video"] = _positive_float(stream.get("duration"))
                try:
                    width = int(float(stream.get("width") or width))
                    height = int(float(stream.get("height") or height))
                except Exception:
                    width, height = DEFAULT_VIDEO_SIZE
            elif codec_type == "audio" and durations["audio"] <= 0:
                has_audio = True
                durations["audio"] = _positive_float(stream.get("duration"))
    except Exception:
        durations["exact"] = get_exact_duration(path)
        durations["video"] = get_video_stream_duration(path)
        has_audio = has_audio_stream(path)
        durations["audio"] = get_audio_stream_duration(path) if has_audio else 0.0
        width, height = get_video_dimensions(path)

    duration, info = choose_timeline_media_duration(**durations)
    return {
        "duration": duration,
        "exact_duration": durations["exact"],
        "video_duration": durations["video"],
        "audio_duration": durations["audio"],
        "has_audio": has_audio,
        "width": width,
        "height": height,
        "duration_info": info,
    }


def get_video_import_metadata(file_path):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return _video_import_metadata_cached("", 0, 0)
    return _video_import_metadata_cached(path, size, mtime_ns)


def get_timeline_media_duration(file_path, precise=False):
    durations = {
        "exact": get_exact_duration(file_path),
        "video": get_video_stream_duration(file_path),
        "audio": get_audio_stream_duration(file_path) if has_audio_stream(file_path) else 0.0,
        "packet": estimate_video_packet_duration(file_path) if precise else 0.0,
    }
    return choose_timeline_media_duration(**durations)


def parse_rate(value):
    text = str(value or "").strip()
    if not text or text in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_f = float(denominator)
            return float(numerator) / denominator_f if denominator_f else 0.0
        return float(text)
    except Exception:
        return 0.0


@functools.lru_cache(maxsize=512)
def _video_dimensions_cached(path, size, mtime_ns):
    if not path:
        return DEFAULT_VIDEO_SIZE
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                path,
            ],
            timeout=8,
        )
        match = re.search(r"(\d+)x(\d+)", result.stdout.strip())
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass

    try:
        result = _run_probe([get_ffmpeg_cmd(), "-i", path], timeout=5)
        match = re.search(r"Video:.*?,\s*(\d+)x(\d+)", result.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return DEFAULT_VIDEO_SIZE


def get_video_dimensions(file_path):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return DEFAULT_VIDEO_SIZE
    return _video_dimensions_cached(path, size, mtime_ns)


@functools.lru_cache(maxsize=512)
def _has_audio_stream_cached(path, size, mtime_ns):
    if not path:
        return False
    try:
        result = _run_probe(
            [
                get_ffprobe_cmd(),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                path,
            ],
            timeout=8,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def has_audio_stream(file_path):
    path, size, mtime_ns = media_fingerprint(file_path)
    if not path:
        return False
    return _has_audio_stream_cached(path, size, mtime_ns)


def clear_media_probe_cache():
    _exact_duration_cached.cache_clear()
    _packet_duration_cached.cache_clear()
    _stream_duration_cached.cache_clear()
    _video_import_metadata_cached.cache_clear()
    _video_dimensions_cached.cache_clear()
    _has_audio_stream_cached.cache_clear()
