def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def slider_value_to_time(value, duration, maximum):
    duration = max(0.0, safe_float(duration, 0.0))
    maximum = max(1.0, safe_float(maximum, 1.0))
    value = max(0.0, min(maximum, safe_float(value, 0.0)))
    if duration <= 0:
        return 0.0
    return max(0.0, min(duration, duration * value / maximum))


def time_to_slider_value(time_sec, duration, maximum):
    duration = max(0.0, safe_float(duration, 0.0))
    maximum = max(1, int(maximum or 1))
    if duration <= 0:
        return 0
    ratio = max(0.0, min(1.0, safe_float(time_sec, 0.0) / duration))
    return int(ratio * maximum)
