VIDEO_DRIFT_PLAYING_SECONDS = 0.75
VIDEO_DRIFT_PAUSED_SECONDS = 0.28
AUDIO_DRIFT_SECONDS = 0.35
MUSIC_DRIFT_SECONDS = 0.28


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def source_changed(current_path, target_path):
    return str(current_path or "") != str(target_path or "")


def should_seek_player(player_time, target_time, force_seek=False, source_changed=False, drift_limit=0.3):
    if force_seek or source_changed:
        return True
    return abs(safe_float(player_time, 0.0) - safe_float(target_time, 0.0)) > max(0.0, safe_float(drift_limit, 0.0))


def video_drift_limit(is_playing):
    return VIDEO_DRIFT_PLAYING_SECONDS if is_playing else VIDEO_DRIFT_PAUSED_SECONDS


def music_local_time(time_sec, source_duration, loop_enabled=True):
    time_sec = max(0.0, safe_float(time_sec, 0.0))
    source_duration = max(0.0, safe_float(source_duration, 0.0))
    if source_duration <= 0:
        return time_sec
    if loop_enabled:
        return time_sec % source_duration
    return min(time_sec, source_duration)


def sync_decision(
    current_path,
    target_path,
    player_time,
    target_time,
    force_seek=False,
    drift_limit=0.3,
):
    changed = source_changed(current_path, target_path)
    return {
        "source_changed": changed,
        "seek": should_seek_player(
            player_time,
            target_time,
            force_seek=force_seek,
            source_changed=changed,
            drift_limit=drift_limit,
        ),
        "target_time": max(0.0, safe_float(target_time, 0.0)),
    }
