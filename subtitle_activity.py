from render_timing import active_subtitles_at_time

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def is_active_subtitle(subtitle, time_sec):
    start = safe_float((subtitle or {}).get("start", 0.0), 0.0)
    end = safe_float((subtitle or {}).get("end", start), start)
    time_sec = safe_float(time_sec, 0.0)
    return start <= time_sec <= end


def active_subtitle_payload(
    subtitles,
    time_sec,
    project_width,
    selected_idx=-1,
    active_cache=None,
    render_html=None,
    project_height=None,
):
    active_cache = set(active_cache or set())
    payload = []
    source_index_by_id = {id(subtitle): idx for idx, subtitle in enumerate(subtitles or []) if isinstance(subtitle, dict)}
    for subtitle, sample_time in active_subtitles_at_time(subtitles or [], time_sec):
        if not isinstance(subtitle, dict):
            continue
        try:
            idx = int(subtitle.get("_source_idx", source_index_by_id.get(id(subtitle), -1)))
        except Exception:
            idx = source_index_by_id.get(id(subtitle), -1)
        style = subtitle.get("style", {}) if isinstance(subtitle.get("style", {}), dict) else {}
        html_text = ""
        if render_html:
            if project_height is None:
                html_text = render_html(subtitle, sample_time, project_width)
            else:
                try:
                    html_text = render_html(subtitle, sample_time, project_width, project_height)
                except TypeError:
                    html_text = render_html(subtitle, sample_time, project_width)
        payload.append(
            {
                "idx": idx,
                "htmlText": html_text,
                "isNew": idx not in active_cache,
                "pos_x": subtitle.get("pos_x", 0.0),
                "pos_y": subtitle.get("pos_y", 25.0),
                "box_width": style.get("box_width", 0),
                "track": subtitle.get("track", 1),
                "isSelected": idx == selected_idx,
            }
        )
    return payload


def active_subtitle_indices(payload):
    return {item["idx"] for item in payload or [] if isinstance(item, dict) and "idx" in item}
