import copy
import os


MIN_CLIP_DURATION = 0.001
SPLIT_MARGIN_SECONDS = 0.05
MIN_CLIP_SPEED = 0.05
MAX_CLIP_SPEED = 8.0


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clip_start(clip):
    return safe_float((clip or {}).get("start", 0.0), 0.0)


def clip_end(clip):
    start = clip_start(clip)
    return safe_float((clip or {}).get("end", start), start)


def clip_speed(clip):
    return min(MAX_CLIP_SPEED, max(MIN_CLIP_SPEED, safe_float((clip or {}).get("speed", 1.0), 1.0)))


def clip_source_duration(clip):
    if not isinstance(clip, dict):
        return 0.0
    source_in = safe_float(clip.get("source_in", 0.0), 0.0)
    fallback_dur = safe_float(clip.get("dur", clip_end(clip) - clip_start(clip)), 0.0)
    source_out = safe_float(clip.get("source_out", fallback_dur), fallback_dur)
    return max(MIN_CLIP_DURATION, source_out - source_in)


def sorted_video_clips(clips):
    return sorted(list(clips or []), key=lambda clip: (clip_start(clip), clip_end(clip)))


def timeline_end(items):
    ends = [clip_end(item) for item in items or [] if isinstance(item, dict)]
    return max(ends) if ends else 0.0


def find_clip_index_at_time(clips, time_sec, inclusive=False):
    t = safe_float(time_sec, 0.0)
    for idx, clip in enumerate(clips or []):
        start = clip_start(clip)
        end = clip_end(clip)
        if inclusive:
            if start <= t <= end:
                return idx
        elif start < t < end:
            return idx
    return -1


def clip_for_time(clips, time_sec):
    clips = clips or []
    if not clips:
        return -1, None
    idx = find_clip_index_at_time(clips, time_sec, inclusive=True)
    if idx >= 0:
        return idx, clips[idx]
    t = safe_float(time_sec, 0.0)
    first_start = clip_start(clips[0])
    if t < first_start:
        return 0, clips[0]
    return len(clips) - 1, clips[-1]


def video_local_time(clip, time_sec):
    if not isinstance(clip, dict):
        return 0.0
    start = clip_start(clip)
    source_in = safe_float(clip.get("source_in", 0.0), 0.0)
    fallback_dur = safe_float(clip.get("dur", 0.0), 0.0)
    source_out = safe_float(clip.get("source_out", fallback_dur), fallback_dur)
    source_len = max(MIN_CLIP_DURATION, source_out - source_in)
    offset = max(0.0, safe_float(time_sec, 0.0) - start) * clip_speed(clip)
    timeline_len = clip_end(clip) - start
    if offset > source_len and timeline_len > source_len:
        offset = offset % source_len
    return max(0.0, min(source_out, source_in + offset))


def create_video_clip(file_path, duration, start_t=0.0):
    duration = max(MIN_CLIP_DURATION, safe_float(duration, 0.0))
    start_t = max(0.0, safe_float(start_t, 0.0))
    return {
        "path": file_path,
        "start": start_t,
        "end": start_t + duration,
        "dur": duration,
        "source_in": 0.0,
        "source_out": duration,
        "speed": 1.0,
        "transition": {"type": "cut", "duration": 0.0},
    }


def append_video_clip_to_state(state, file_path, duration, start_t=None):
    state = state if isinstance(state, dict) else {}
    clips = list(state.get("video_clips", []) or [])
    if start_t is None:
        start_t = timeline_end(clips)
    clip = create_video_clip(file_path, duration, start_t=start_t)
    clips.append(clip)
    clips = sorted_video_clips(clips)
    state["video_clips"] = clips
    return clip, clips.index(clip)


def split_video_clip_at(clips, idx, time_sec, margin=SPLIT_MARGIN_SECONDS):
    clips = list(clips or [])
    if not (0 <= idx < len(clips)):
        return None
    clip = clips[idx]
    start = clip_start(clip)
    end = clip_end(clip)
    time_sec = safe_float(time_sec, 0.0)
    if not (start + margin < time_sec < end - margin):
        return None

    left = copy.deepcopy(clip)
    right = copy.deepcopy(clip)
    left["end"] = time_sec
    right["start"] = time_sec

    source_in = safe_float(clip.get("source_in", 0.0), 0.0)
    fallback_dur = safe_float(clip.get("dur", end - start), end - start)
    source_out = safe_float(clip.get("source_out", fallback_dur), fallback_dur)
    source_len = max(MIN_CLIP_DURATION, source_out - source_in)
    timeline_offset = max(0.0, time_sec - start) * clip_speed(clip)
    source_cut = min(source_out, source_in + (timeline_offset % source_len))

    left["source_in"] = source_in
    left["source_out"] = max(source_in, source_cut)
    right["source_in"] = source_cut
    right["source_out"] = source_out
    right.setdefault("transition", {"type": "cut", "duration": 0.0})

    clips[idx:idx + 1] = [left, right]
    clips = sorted_video_clips(clips)
    return clips, left, right


def update_video_clip_timing_in_state(state, idx, start, end):
    clips = list((state or {}).get("video_clips", []) or [])
    if not (0 <= idx < len(clips)):
        return False, idx
    start = max(0.0, safe_float(start, 0.0))
    end = max(start + MIN_CLIP_DURATION, safe_float(end, start + MIN_CLIP_DURATION))
    clip = clips[idx]
    clip["start"] = start
    clip["end"] = end
    clips = sorted_video_clips(clips)
    state["video_clips"] = clips
    return True, clips.index(clip)


def set_video_clip_speed_in_state(state, idx, speed):
    clips = list((state or {}).get("video_clips", []) or [])
    if not (0 <= idx < len(clips)):
        return False, idx
    clip = clips[idx]
    old_speed = clip_speed(clip)
    new_speed = clip_speed({"speed": speed})
    start = clip_start(clip)
    timeline_len = max(MIN_CLIP_DURATION, clip_end(clip) - start)
    source_coverage = max(MIN_CLIP_DURATION, timeline_len * old_speed)
    clip["speed"] = new_speed
    clip["end"] = start + max(MIN_CLIP_DURATION, source_coverage / new_speed)
    clips = sorted_video_clips(clips)
    state["video_clips"] = clips
    return True, clips.index(clip)


def fit_video_clip_to_duration_in_state(state, idx, target_duration):
    clips = list((state or {}).get("video_clips", []) or [])
    if not (0 <= idx < len(clips)):
        return False, idx, 1.0, False
    target_duration = max(MIN_CLIP_DURATION, safe_float(target_duration, 0.0))
    clip = clips[idx]
    start = clip_start(clip)
    source_coverage = clip_source_duration(clip)
    desired_speed = source_coverage / target_duration
    new_speed = clip_speed({"speed": desired_speed})
    clamped = abs(new_speed - desired_speed) > 0.0001
    clip["speed"] = new_speed
    clip["start"] = start
    clip["end"] = start + max(MIN_CLIP_DURATION, source_coverage / new_speed)
    clips = sorted_video_clips(clips)
    state["video_clips"] = clips
    return True, clips.index(clip), new_speed, clamped


def media_basename(path, fallback=""):
    name = os.path.basename(str(path or ""))
    return name or fallback


def content_duration_for_state(state, exact_duration=None):
    state = state if isinstance(state, dict) else {}
    durations = [timeline_end(state.get("video_clips", []))]

    audio_path = state.get("audio_path", "")
    if audio_path:
        audio_trim = state.get("a_trim") or []
        if len(audio_trim) >= 2:
            durations.append(max(0.0, safe_float(audio_trim[1]) - safe_float(audio_trim[0])))
        elif exact_duration:
            durations.append(max(0.0, safe_float(exact_duration(audio_path), 0.0)))

    durations.append(timeline_end(state.get("subs_data", [])))

    if state.get("music_path"):
        music_target = safe_float(state.get("music_match_duration", 0.0), 0.0)
        if music_target <= 0:
            music_target = safe_float(state.get("music_dur", 0.0), 0.0)
        if music_target <= 0 and exact_duration:
            music_target = safe_float(exact_duration(state.get("music_path")), 0.0)
        if music_target > 0:
            durations.append(music_target)

    if any(duration > 0 for duration in durations):
        return max(0.0, max(durations))
    return max(0.0, safe_float(state.get("content_duration", 0.0), 0.0))


def playback_duration_for_state(state, exact_duration=None):
    content_duration = content_duration_for_state(state, exact_duration=exact_duration)
    if content_duration > 0:
        return max(MIN_CLIP_DURATION, content_duration)
    return max(MIN_CLIP_DURATION, safe_float((state or {}).get("duration", 0.0), 0.0))


def render_duration_for_state(state, exact_duration=None, tail_padding=0.0, min_duration=1.0):
    content_duration = content_duration_for_state(state, exact_duration=exact_duration)
    if content_duration <= 0:
        return max(min_duration, 0.0), 0.0
    return max(min_duration, content_duration + max(0.0, safe_float(tail_padding, 0.0))), content_duration
