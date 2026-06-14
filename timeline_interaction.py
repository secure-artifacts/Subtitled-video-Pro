SNAP_STEP_SECONDS = 0.05
MIN_TIMELINE_DURATION = 0.001


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def snap_time(seconds, enabled=True, step=SNAP_STEP_SECONDS):
    value = safe_float(seconds, 0.0)
    if not enabled:
        return value
    step = max(MIN_TIMELINE_DURATION, safe_float(step, SNAP_STEP_SECONDS))
    return round(value / step) * step


def nearest_snap_point(seconds, points, max_distance):
    value = safe_float(seconds, 0.0)
    max_distance = max(0.0, safe_float(max_distance, 0.0))
    nearest = None
    nearest_distance = None
    for point in points or []:
        point = safe_float(point, 0.0)
        distance = abs(point - value)
        if distance <= max_distance and (nearest_distance is None or distance < nearest_distance):
            nearest = point
            nearest_distance = distance
    return nearest


def snap_time_with_points(seconds, enabled=True, step=SNAP_STEP_SECONDS, points=None, max_point_distance=0.0):
    value = safe_float(seconds, 0.0)
    if not enabled:
        return value
    point = nearest_snap_point(value, points or [], max_point_distance)
    if point is not None:
        return point
    return snap_time(value, enabled=True, step=step)


def clamp_timing(start, end, min_duration=MIN_TIMELINE_DURATION):
    start = max(0.0, safe_float(start, 0.0))
    min_duration = max(MIN_TIMELINE_DURATION, safe_float(min_duration, MIN_TIMELINE_DURATION))
    end = safe_float(end, start + min_duration)
    if end <= start:
        end = start + min_duration
    return start, end


def format_timecode(seconds):
    seconds = max(0.0, safe_float(seconds, 0.0))
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    if seconds < 60:
        return f"{secs:04.1f}s"
    return f"{minutes:02d}:{secs:04.1f}"


def format_timing_label(start, end, track_label=""):
    start, end = clamp_timing(start, end)
    prefix = f"{track_label} " if track_label else ""
    return f"{prefix}{format_timecode(start)} - {format_timecode(end)}  ({format_timecode(end - start)})"


def item_key(clip_type, idx):
    return f"{clip_type}:{int(idx)}"


def parse_item_key(key):
    text = str(key or "")
    if ":" not in text:
        return "", -1
    clip_type, idx = text.split(":", 1)
    try:
        return clip_type, int(idx)
    except Exception:
        return clip_type, -1


def update_selection(selection, clip_type, idx, additive=False):
    key = item_key(clip_type, idx)
    selection = set(selection or set())
    if additive:
        if key in selection:
            selection.remove(key)
        else:
            selection.add(key)
        return selection
    return {key}


def shift_timing(start, end, delta):
    start = safe_float(start, 0.0)
    end = safe_float(end, start)
    delta = safe_float(delta, 0.0)
    if start + delta < 0:
        delta = -start
    return start + delta, end + delta
