from preview_overlay_sync import build_overlay_sync_script, stable_overlay_hash
from preview_overlay_visibility import overlay_has_content
from subtitle_activity import active_subtitle_indices, active_subtitle_payload


def build_preview_overlay_payload(
    subtitles,
    time_sec,
    project_width,
    project_height,
    selected_idx,
    active_cache,
    signature_config,
    design_state,
    render_subtitle_html,
    render_design_html,
    render_signature_html,
):
    active_subs = active_subtitle_payload(
        subtitles,
        time_sec,
        project_width,
        selected_idx=selected_idx,
        active_cache=active_cache,
        render_html=render_subtitle_html,
        project_height=project_height,
    )
    design_html = render_design_html(design_state or {}, time_sec, project_width, project_height)
    signature_html = render_signature_html(signature_config, time_sec, project_width, project_height)
    return {
        "active_subs": active_subs,
        "active_indices": active_subtitle_indices(active_subs),
        "design_html": design_html,
        "signature_html": signature_html,
        "has_content": overlay_has_content(active_subs, design_html, signature_html),
        "hash": stable_overlay_hash(active_subs, signature_html, design_html),
        "script": build_overlay_sync_script(active_subs, signature_html, design_html),
    }
