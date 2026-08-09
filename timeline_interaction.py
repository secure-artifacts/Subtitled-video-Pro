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


def subtitle_should_stretch_words_on_resize(subtitle):
    if not isinstance(subtitle, dict):
        return True
    style = subtitle.get("style", {})
    if not isinstance(style, dict):
        style = {}
    reveal_mode = str(style.get("text_reveal_mode", "all") or "all").strip().lower()
    reveal_mode = {
        "word": "word_voice",
        "voice_word": "word_voice",
        "line": "line_voice",
        "voice_line": "line_voice",
        "none": "all",
        "": "all",
    }.get(reveal_mode, reveal_mode)
    return reveal_mode == "all"


def retime_subtitle_clip(subtitle, new_start, new_end, min_duration=0.001, move_epsilon=0.001, stretch_words_on_resize=None):
    """Update subtitle timing while respecting its reveal mode.

    Pure moves shift every word. Resizes stretch words only for all-at-once captions;
    voice-reveal captions keep word starts pinned to the audio timeline.
    """
    if not isinstance(subtitle, dict):
        return {"changed": False, "mode": "invalid", "delta": 0.0}
    old_start = safe_float(subtitle.get("start", 0.0), 0.0)
    old_end = safe_float(subtitle.get("end", old_start + min_duration), old_start + min_duration)
    old_start, old_end = clamp_timing(old_start, old_end, min_duration)
    new_start, new_end = clamp_timing(new_start, new_end, min_duration)
    old_dur = max(min_duration, old_end - old_start)
    new_dur = max(min_duration, new_end - new_start)
    start_delta = new_start - old_start
    end_delta = new_end - old_end
    is_pure_move = abs(new_dur - old_dur) <= move_epsilon and abs(start_delta - end_delta) <= move_epsilon
    if stretch_words_on_resize is None:
        stretch_words_on_resize = subtitle_should_stretch_words_on_resize(subtitle)

    if is_pure_move and abs(start_delta) > move_epsilon:
        for word in subtitle.get("words", []) or []:
            word_start = safe_float(word.get("start", old_start), old_start)
            word_end = safe_float(word.get("end", word_start), word_start)
            shifted_start = max(0.0, word_start + start_delta)
            shifted_end = max(shifted_start, word_end + start_delta)
            word["start"] = shifted_start
            word["end"] = shifted_end
    elif stretch_words_on_resize:
        for word in subtitle.get("words", []) or []:
            word_start = safe_float(word.get("start", old_start), old_start)
            word_end = safe_float(word.get("end", word_start), word_start)
            rel_start = (word_start - old_start) / old_dur
            rel_end = (word_end - old_start) / old_dur
            new_word_start = new_start + rel_start * new_dur
            new_word_end = new_start + rel_end * new_dur
            word["start"] = max(0.0, new_word_start)
            word["end"] = max(word["start"], new_word_end)

    subtitle["start"] = new_start
    subtitle["end"] = new_end
    return {
        "changed": abs(start_delta) > move_epsilon or abs(end_delta) > move_epsilon,
        "mode": "move" if is_pure_move else "stretch_resize" if stretch_words_on_resize else "resize",
        "delta": start_delta if is_pure_move else 0.0,
    }
