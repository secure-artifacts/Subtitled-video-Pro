END_NEAR_THRESHOLD_SECONDS = 0.12

ACTION_RESTART_LOOP = "restart_loop"
ACTION_STOP_AT_END = "stop_at_end"
ACTION_RESYNC_VIDEO = "resync_video"
ACTION_PAUSE_AUDIO = "pause_audio"
ACTION_RESYNC_MUSIC = "resync_music"
ACTION_PAUSE_MUSIC = "pause_music"


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def is_near_timeline_end(current_time, duration, threshold=END_NEAR_THRESHOLD_SECONDS):
    duration = max(0.0, safe_float(duration, 0.0))
    current_time = max(0.0, safe_float(current_time, 0.0))
    return current_time >= duration - max(0.0, safe_float(threshold, 0.0))


def video_end_action(current_time, duration, loop_enabled):
    if not loop_enabled:
        return ACTION_STOP_AT_END
    if is_near_timeline_end(current_time, duration):
        return ACTION_RESTART_LOOP
    return ACTION_RESYNC_VIDEO


def audio_end_action(current_time, duration, loop_enabled):
    if not is_near_timeline_end(current_time, duration):
        return ACTION_PAUSE_AUDIO
    return ACTION_RESTART_LOOP if loop_enabled else ACTION_STOP_AT_END


def music_end_action(current_time, duration, preview_loop_enabled, music_loop_enabled):
    if not is_near_timeline_end(current_time, duration):
        return ACTION_RESYNC_MUSIC if music_loop_enabled else ACTION_PAUSE_MUSIC
    return ACTION_RESYNC_MUSIC if preview_loop_enabled else ACTION_STOP_AT_END
