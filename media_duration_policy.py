MISMATCH_ABSOLUTE_SECONDS = 1.5
MISMATCH_RATIO = 0.92


def _positive_float(value):
    try:
        value = float(value)
    except Exception:
        return 0.0
    return value if value > 0 else 0.0


def choose_timeline_media_duration(exact=0.0, video=0.0, audio=0.0, packet=0.0):
    durations = {
        "exact": _positive_float(exact),
        "video": _positive_float(video),
        "audio": _positive_float(audio),
        "packet": _positive_float(packet),
    }
    positives = {key: value for key, value in durations.items() if value > 0}
    if not positives:
        return 0.0, {
            "reason": "missing",
            "durations": durations,
        }

    longest = max(positives.values())
    audio_duration = durations["audio"]
    if (
        audio_duration > 0
        and longest - audio_duration >= MISMATCH_ABSOLUTE_SECONDS
        and audio_duration / longest <= MISMATCH_RATIO
    ):
        return audio_duration, {
            "reason": "audio_shorter_than_container",
            "durations": durations,
        }

    if durations["packet"] > 0 and durations["video"] > 0 and durations["packet"] < durations["video"]:
        return durations["packet"], {
            "reason": "packet_video_duration",
            "durations": durations,
        }

    for key in ("video", "exact", "packet", "audio"):
        if durations[key] > 0:
            return durations[key], {
                "reason": key,
                "durations": durations,
            }
    return longest, {
        "reason": "fallback_longest",
        "durations": durations,
    }
