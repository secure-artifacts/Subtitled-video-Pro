import os
import math

DEFAULT_RENDER_TAIL_PAD_SECONDS = 0.75
SUBTITLE_ACTIVE_EPSILON_SECONDS = 0.002
TIME_QUANTUM_SECONDS = 0.001


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


def active_subtitles_at_time(subs_data, current_time, epsilon=SUBTITLE_ACTIVE_EPSILON_SECONDS):
    active = []
    for sub in subs_data or []:
        if not isinstance(sub, dict):
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
    short_window = frame_duration <= epsilon * 2.0
    if not short_window:
        return []
    for sub in subs_data or []:
        if not isinstance(sub, dict):
            continue
        start = _bounded_time(sub.get("start", 0.0), 0.0, float("inf"))
        end = _bounded_time(sub.get("end", start), 0.0, float("inf"))
        if end <= start:
            continue
        if frame_start < start < frame_end:
            active.append((sub, start))
    return active


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


def build_subtitle_frame_schedule(subs_data, total_duration, extra_styles=None, extra_times=None):
    total_duration = max(0.001, float(total_duration or 0.0))
    event_fps = subtitle_event_fps()
    continuous_fps = subtitle_continuous_fps()
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

    for sub in subs_data or []:
        start = _bounded_time(sub.get("start", 0.0), 0.0, total_duration)
        end = _bounded_time(sub.get("end", start + 0.05), 0.0, total_duration)
        if end <= start:
            continue

        style = sub.get("style", sub) or {}
        anim_type = style.get("anim_type", "pop")
        pop_speed = max(0.05, float(style.get("pop_speed", 0.18) or 0.18))
        hl_motion = style.get("hl_motion", "stable")
        use_hl = bool(style.get("use_hl", True))

        _add_time(times, start, total_duration)
        _add_time(times, end, total_duration)

        if _needs_continuous_sampling(style):
            _add_range_samples(times, start, end, continuous_fps, total_duration)
            continue

        if anim_type in INTRO_ANIMS:
            _add_range_samples(times, start, min(end, start + pop_speed * 1.8), event_fps, total_duration)

        for word in _subtitle_words(sub, start, end):
            w_start = _bounded_time(word.get("start", start), start, end)
            w_end = _bounded_time(word.get("end", w_start + 0.05), start, end)
            if w_end <= w_start:
                w_end = min(end, w_start + 0.05)

            _add_time(times, w_start, total_duration)
            _add_time(times, w_end, total_duration)

            if anim_type in PER_WORD_ANIMS:
                multiplier = 1.35 if anim_type == "letter_scatter_in" else 1.0
                _add_range_samples(times, w_start, min(w_end, w_start + pop_speed * multiplier), event_fps, total_duration)

            if use_hl and hl_motion in ("pop", "push"):
                _add_range_samples(times, w_start, min(w_end, w_start + 0.35), event_fps, total_duration)

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
