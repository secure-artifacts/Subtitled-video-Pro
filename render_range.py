from timeline_model import safe_float


MIN_RENDER_RANGE_DURATION = 0.001


def _range_source(state):
    state = state if isinstance(state, dict) else {}
    render_range = state.get("render_range")
    if isinstance(render_range, dict):
        return render_range
    return {
        "enabled": bool(state.get("render_range_enabled", False)),
        "start": state.get("render_start", 0.0),
        "end": state.get("render_end", state.get("duration", 0.0)),
    }


def normalize_render_range(state, total_duration):
    total_duration = max(0.0, safe_float(total_duration, 0.0))
    source = _range_source(state)
    enabled = bool(source.get("enabled", False))
    start = max(0.0, safe_float(source.get("start", 0.0), 0.0))
    end_default = total_duration if total_duration > 0 else start + MIN_RENDER_RANGE_DURATION
    end = safe_float(source.get("end", end_default), end_default)
    if total_duration > 0:
        start = min(start, total_duration)
        end = min(max(end, 0.0), total_duration)
    if end <= start:
        end = start + MIN_RENDER_RANGE_DURATION
    if not enabled:
        start = 0.0
        end = max(total_duration, MIN_RENDER_RANGE_DURATION)
    return {
        "enabled": enabled,
        "start": start,
        "end": end,
        "duration": max(MIN_RENDER_RANGE_DURATION, end - start),
    }


def set_render_range(state, enabled=False, start=0.0, end=None, total_duration=0.0):
    state = state if isinstance(state, dict) else {}
    if end is None:
        end = total_duration
    draft = {
        "render_range": {
            "enabled": bool(enabled),
            "start": start,
            "end": end,
        }
    }
    normalized = normalize_render_range(draft, total_duration)
    state["render_range"] = {
        "enabled": normalized["enabled"],
        "start": normalized["start"],
        "end": normalized["end"],
    }
    return normalized
