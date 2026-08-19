import os
import math
import copy

DEFAULT_RENDER_TAIL_PAD_SECONDS = 0.75
SUBTITLE_ACTIVE_EPSILON_SECONDS = 0.002
TIME_QUANTUM_SECONDS = 0.001
FAST_WORD_VISUAL_MIN_SECONDS = 0.14


def render_tail_padding_seconds():
    try:
        value = float(str(os.environ.get("SUBTITLE_RENDER_TAIL_PAD", DEFAULT_RENDER_TAIL_PAD_SECONDS)).strip())
    except Exception:
        value = DEFAULT_RENDER_TAIL_PAD_SECONDS
    return max(0.0, min(5.0, value))


PER_WORD_ANIMS = {
    "pop",
    "fade",
    "blur_fade",
    "grow_in",
    "scatter_in",
    "letter_scatter_in",
    "word_wipe",
}

CONTINUOUS_ANIMS = {
    "roll_up",
    "full_text_roll",
    "wipe_right",
    "camera_push",
    "depth_push",
    "holy_breath",
    "typewriter",
}

INTRO_ANIMS = {"slam_in"}


def _env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def subtitle_supersample():
    return _env_int("SUBTITLE_SUPERSAMPLE", 1, 1, 3)


def subtitle_event_fps():
    return _env_int("SUBTITLE_EVENT_FPS", 8, 6, 30)


def subtitle_continuous_fps():
    return _env_int("SUBTITLE_CONTINUOUS_FPS", 12, 8, 30)


def _bounded_fps(value, default, minimum=4, maximum=30):
    try:
        fps = int(float(value))
    except Exception:
        fps = int(default)
    return max(int(minimum), min(int(maximum), fps))


def _clean_word_text(word):
    return str(word.get("text") or word.get("word") or "").replace("\n", "").strip()


def _bounded_time(value, lower, upper):
    try:
        t = float(value)
    except Exception:
        t = lower
    return max(lower, min(upper, t))


def quantize_sample_time(value, quantum=TIME_QUANTUM_SECONDS):
    try:
        value = float(value)
    except Exception:
        value = 0.0
    quantum = max(0.000001, float(quantum or TIME_QUANTUM_SECONDS))
    return round(math.ceil(max(0.0, value) / quantum - 1e-9) * quantum, 3)


def subtitle_frame_sample_time(sub, frame_start, frame_duration=None, epsilon=SUBTITLE_ACTIVE_EPSILON_SECONDS):
    start = _bounded_time(sub.get("start", 0.0), 0.0, float("inf"))
    end = _bounded_time(sub.get("end", start), 0.0, float("inf"))
    frame_start = max(0.0, float(frame_start or 0.0))
    if end <= start:
        return None
    if start <= frame_start < end:
        return frame_start
    return None


SPLIT_SCREEN_LAYOUT_MODE = "split_screen"
SPLIT_SCREEN_DEFAULT_Y = {2: (28.0, 72.0), 3: (16.0, 48.0, 82.0), 4: (12.0, 36.0, 60.0, 84.0)}


def _subtitle_style(sub):
    style = (sub or {}).get("style", {}) if isinstance(sub, dict) else {}
    return style if isinstance(style, dict) else {}


def _split_screen_smart_pool_enabled(style):
    raw_pool = str((style or {}).get("smart_layout_pool", "") or "")
    pool = {item.strip().lower() for item in raw_pool.split(",") if item.strip()}
    return SPLIT_SCREEN_LAYOUT_MODE in pool


def _is_split_screen_subtitle(sub):
    style = _subtitle_style(sub)
    layout_mode = str(style.get("layout_mode", "standard") or "standard").strip().lower()
    if layout_mode == SPLIT_SCREEN_LAYOUT_MODE:
        return True
    if layout_mode in {"smart_caption", "mixed_reel"} and _split_screen_smart_pool_enabled(style):
        return True
    return False


def _split_screen_count(style):
    try:
        count = int(float((style or {}).get("split_screen_count", 3) or 3))
    except Exception:
        count = 3
    return max(2, min(4, count))


def _split_screen_slot_value(style, key, default):
    try:
        return float((style or {}).get(key, default))
    except Exception:
        return float(default)


def _split_screen_cycle_mode(style):
    value = (style or {}).get("split_screen_cycle_mode", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "cycle", "group", "group_cycle"}
    return bool(value)


def _split_screen_slot_position(style, slot_index, slot_count):
    slot_count = max(2, min(4, int(slot_count or 3)))
    slot_index = max(1, min(slot_count, int(slot_index or 1)))
    default_y = SPLIT_SCREEN_DEFAULT_Y.get(slot_count, SPLIT_SCREEN_DEFAULT_Y[3])[slot_index - 1]
    return (
        _split_screen_slot_value(style, f"split_screen_pos_x_{slot_index}", 0.0),
        _split_screen_slot_value(style, f"split_screen_pos_y_{slot_index}", default_y),
    )


def _split_screen_records(subs_data):
    groups = {}
    for idx, sub in enumerate(subs_data or []):
        if not isinstance(sub, dict) or not _is_split_screen_subtitle(sub):
            continue
        start = _bounded_time(sub.get("start", 0.0), 0.0, float("inf"))
        end = _bounded_time(sub.get("end", start), 0.0, float("inf"))
        if end <= start:
            continue
        groups.setdefault(sub.get("track", 1), []).append({"idx": idx, "sub": sub, "start": start, "end": end})
    for records in groups.values():
        records.sort(key=lambda item: (item["start"], item["end"], item["idx"]))
    return groups


def _split_screen_visual_end(records, position):
    record = records[position]
    style = _subtitle_style(record["sub"])
    count = _split_screen_count(style)
    if _split_screen_cycle_mode(style):
        group_start = (position // count) * count
        group_end = min(len(records), group_start + count)
        if group_end < len(records):
            return records[group_end]["start"]
        return max(item["end"] for item in records[group_start:group_end])
    target_position = position + count
    own_end = record["end"]
    if target_position < len(records):
        return max(own_end, records[target_position]["start"])
    return max(own_end, max(item["end"] for item in records[position:]))


def _split_screen_sample_time(record, current_time, epsilon):
    if current_time < record["end"]:
        return current_time
    return max(record["start"], record["end"] - max(float(epsilon or 0.0), 0.001))


def _split_screen_active_subtitles_at_time(subs_data, current_time, epsilon):
    active = []
    active_source_indices = set()
    for records in _split_screen_records(subs_data).values():
        candidates = []
        for position, record in enumerate(records):
            if record["start"] <= current_time < _split_screen_visual_end(records, position):
                candidates.append((position, record))
        if not candidates:
            continue
        latest_record = max((record for _position, record in candidates), key=lambda item: (item["start"], item["idx"]))
        controller_style = _subtitle_style(latest_record["sub"])
        slot_count = _split_screen_count(controller_style)
        visible = sorted(candidates, key=lambda item: (item[1]["start"], item[1]["idx"]))[-slot_count:]
        for slot_index, (_position, record) in enumerate(visible, start=1):
            clone = copy.deepcopy(record["sub"])
            clone["_source_idx"] = record["idx"]
            clone["_split_screen_slot"] = slot_index
            clone["_split_screen_count"] = slot_count
            pos_x, pos_y = _split_screen_slot_position(controller_style, slot_index, slot_count)
            clone["pos_x"] = pos_x
            clone["pos_y"] = pos_y
            active.append((clone, _split_screen_sample_time(record, current_time, epsilon)))
            active_source_indices.add(record["idx"])
    return active, active_source_indices


def active_subtitles_at_time(subs_data, current_time, epsilon=SUBTITLE_ACTIVE_EPSILON_SECONDS):
    current_time = max(0.0, float(current_time or 0.0))
    active, split_source_indices = _split_screen_active_subtitles_at_time(subs_data, current_time, epsilon)
    for idx, sub in enumerate(subs_data or []):
        if not isinstance(sub, dict) or idx in split_source_indices or _is_split_screen_subtitle(sub):
            continue
        sample_time = subtitle_frame_sample_time(sub, current_time, epsilon=epsilon)
        if sample_time is not None:
            active.append((sub, sample_time))
    return active


def active_subtitles_for_frame(subs_data, frame_start, frame_duration, epsilon=SUBTITLE_ACTIVE_EPSILON_SECONDS):
    frame_start = max(0.0, float(frame_start or 0.0))
    frame_duration = max(0.0, float(frame_duration or 0.0))
    if frame_duration <= 0:
        return active_subtitles_at_time(subs_data, frame_start, epsilon=epsilon)
    active = active_subtitles_at_time(subs_data, frame_start, epsilon=epsilon)
    if active:
        return active
    frame_end = frame_start + frame_duration
    event_times = []
    for sub in subs_data or []:
        if not isinstance(sub, dict):
            continue
        start = _bounded_time(sub.get("start", 0.0), 0.0, float("inf"))
        end = _bounded_time(sub.get("end", start), 0.0, float("inf"))
        if end <= start:
            continue
        short_event = (end - start) <= max(frame_duration, FAST_WORD_VISUAL_MIN_SECONDS)
        if short_event and frame_start < start < frame_end:
            event_times.append(start)
    if event_times:
        return active_subtitles_at_time(subs_data, min(event_times), epsilon=epsilon)
    return []

def _add_time(times, value, total_duration):
    if value < 0 or value > total_duration:
        return None
    times.add(min(round(float(total_duration), 3), quantize_sample_time(value)))


def _add_range_samples(times, start, end, fps, total_duration):
    start = max(0.0, min(total_duration, float(start)))
    end = max(0.0, min(total_duration, float(end)))
    if end <= start:
        return
    step = 1.0 / max(1, int(fps))
    t = start
    while t < end - 0.001:
        _add_time(times, t, total_duration)
        t += step
    _add_time(times, end, total_duration)


def _subtitle_words(sub, start, end):
    words = sub.get("words", [])
    if not words:
        return [{"text": sub.get("text", ""), "start": start, "end": end}]
    return [w for w in words if _clean_word_text(w)]


def _scene_light_enabled(style):
    if not isinstance(style, dict):
        return False
    value = style.get("scene_light_enable", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
    return bool(value)


def _needs_continuous_sampling(style):
    anim_type = style.get("anim_type", "pop")
    font_motion = style.get("font_motion", "none")
    bg_mode = style.get("bg_mode", "none")
    return (
        anim_type in CONTINUOUS_ANIMS
        or font_motion not in ("none", "", None)
        or bg_mode == "sweep"
        or bg_mode == "cinematic_frame"
    )


def _text_reveal_mode(style):
    mode = str((style or {}).get("text_reveal_mode", "all") or "all").strip().lower()
    return {
        "word": "word_voice",
        "voice_word": "word_voice",
        "word_voice": "word_voice",
        "line": "line_voice",
        "voice_line": "line_voice",
        "line_voice": "line_voice",
        "all": "all",
        "none": "all",
    }.get(mode, mode)


def _needs_word_boundary_sampling(style):
    style = style if isinstance(style, dict) else {}
    anim_type = style.get("anim_type", "pop")
    if anim_type in PER_WORD_ANIMS:
        return True
    if _text_reveal_mode(style) in {"word_voice", "line_voice"}:
        return True
    if bool(style.get("use_hl", True)) and str(style.get("hl_style", "text") or "text").lower() != "none":
        return True
    if str(style.get("font_motion", "none") or "none") in {"typewriter_left", "dynamic_reflow"}:
        return True
    if _scene_light_enabled(style):
        return True
    return False


def build_subtitle_frame_schedule(subs_data, total_duration, extra_styles=None, extra_times=None, event_fps=None, continuous_fps=None):
    total_duration = max(0.001, float(total_duration or 0.0))
    event_fps = _bounded_fps(event_fps, subtitle_event_fps(), 4, 30)
    continuous_fps = _bounded_fps(continuous_fps, subtitle_continuous_fps(), 4, 30)
    times = {0.0, round(total_duration, 3)}

    for value in extra_times or []:
        try:
            _add_time(times, float(value), total_duration)
        except Exception:
            continue

    for style in extra_styles or []:
        if isinstance(style, dict) and _needs_continuous_sampling(style):
            _add_range_samples(times, 0.0, total_duration, continuous_fps, total_duration)
            break

    split_visual_ends = {}
    for records in _split_screen_records(subs_data).values():
        for position, record in enumerate(records):
            split_visual_ends[record["idx"]] = _split_screen_visual_end(records, position)

    for sub_index, sub in enumerate(subs_data or []):
        start = _bounded_time(sub.get("start", 0.0), 0.0, total_duration)
        end = _bounded_time(sub.get("end", start + 0.05), 0.0, total_duration)
        if end <= start:
            continue

        style = sub.get("style", sub) or {}
        anim_type = style.get("anim_type", "pop")
        pop_speed = max(0.05, float(style.get("pop_speed", 0.18) or 0.18))
        hl_motion = style.get("hl_motion", "stable")
        use_hl = bool(style.get("use_hl", True))
        scene_light = _scene_light_enabled(style)
        scene_light_decay = max(0.05, min(1.20, _bounded_time(style.get("scene_light_decay", 0.30), 0.0, 2.0)))
        scene_light_trigger = str(style.get("scene_light_trigger", "word") or "word").strip().lower()
        word_visual_min = max(0.04, min(0.40, _bounded_time(style.get("word_visual_min_seconds", FAST_WORD_VISUAL_MIN_SECONDS), 0.0, 1.0)))

        _add_time(times, start, total_duration)
        _add_time(times, end, total_duration)
        # Exported subtitle layers must have a minimum active sampling rate.
        # Some imported/batch captions do not carry reliable word timings, and
        # segment-only sampling can make the rendered subtitle layer disappear or
        # feel frozen even while preview looks correct.
        if _subtitle_words(sub, start, end):
            _add_range_samples(times, start, end, event_fps, total_duration)
        if sub_index in split_visual_ends:
            visual_end = _bounded_time(split_visual_ends[sub_index], 0.0, total_duration)
            if visual_end > end:
                _add_time(times, visual_end, total_duration)

        if anim_type == "full_text_roll":
            roll_start = start
            roll_end = end
            if bool(style.get("full_roll_lock_to_words", True)):
                roll_words = _subtitle_words(sub, start, end)
                if roll_words:
                    word_starts = [_bounded_time(word.get("start", start), start, end) for word in roll_words]
                    word_ends = [_bounded_time(word.get("end", start), start, end) for word in roll_words]
                    roll_start = max(start, min(word_starts or [start]))
                    roll_end = max(roll_start + 0.05, min(end, max(word_ends or [end])))
            _add_range_samples(times, roll_start, roll_end, continuous_fps, total_duration)
            _add_time(times, end, total_duration)
            continue

        if _needs_continuous_sampling(style):
            _add_range_samples(times, start, end, continuous_fps, total_duration)
            continue

        if anim_type in INTRO_ANIMS:
            _add_range_samples(times, start, min(end, start + pop_speed * 1.8), event_fps, total_duration)

        if not _needs_word_boundary_sampling(style):
            continue

        for word in _subtitle_words(sub, start, end):
            w_start = _bounded_time(word.get("start", start), start, end)
            w_end = _bounded_time(word.get("end", w_start + 0.05), start, end)
            if w_end <= w_start:
                w_end = min(end, w_start + 0.05)
            visual_end = min(end, max(w_end, w_start + word_visual_min))

            _add_time(times, w_start, total_duration)
            _add_time(times, w_end, total_duration)
            _add_time(times, visual_end, total_duration)

            if anim_type in PER_WORD_ANIMS:
                multiplier = 1.35 if anim_type == "letter_scatter_in" else 1.0
                _add_range_samples(times, w_start, min(visual_end, w_start + pop_speed * multiplier), event_fps, total_duration)

            if use_hl and hl_motion in ("pop", "push"):
                _add_range_samples(times, w_start, min(visual_end, w_start + 0.35), event_fps, total_duration)

            if scene_light:
                light_fps = max(event_fps, 14 if scene_light_trigger in {"char", "character", "letter"} else 10)
                _add_range_samples(times, w_start, min(end, w_start + scene_light_decay), light_fps, total_duration)

    ordered = sorted(t for t in times if 0.0 <= t <= total_duration)
    schedule = []
    last_time = None
    for current, nxt in zip(ordered, ordered[1:]):
        if last_time is not None and abs(current - last_time) < 0.001:
            continue
        duration = max(0.0, nxt - current)
        if duration >= 0.001:
            schedule.append((current, duration))
            last_time = current
    return schedule or [(0.0, total_duration)]
