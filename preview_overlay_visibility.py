ACTION_SHOW_VIDEO = "show_video"
ACTION_REFRESH_TRANSPARENCY = "refresh_transparency"
ACTION_SHOW_OVERLAY = "show_overlay"
ACTION_RAISE_OVERLAY = "raise_overlay"
ACTION_HIDE_OVERLAY = "hide_overlay"
ACTION_RAISE_VIDEO = "raise_video"


def overlay_has_content(subtitles, design_html="", signature_html=""):
    return bool(subtitles or str(design_html or "").strip() or str(signature_html or "").strip())


def overlay_visibility_actions(wants_overlay, overlay_enabled, browser_visible, previous_wants_overlay):
    actions = [ACTION_SHOW_VIDEO]
    visible = bool(wants_overlay) and bool(overlay_enabled)
    if visible:
        if not browser_visible or not previous_wants_overlay:
            actions.extend([ACTION_REFRESH_TRANSPARENCY, ACTION_SHOW_OVERLAY, ACTION_RAISE_OVERLAY])
    else:
        if browser_visible:
            actions.extend([ACTION_HIDE_OVERLAY, ACTION_RAISE_VIDEO])
    return actions


def should_sync_overlay_visibility(has_content, previous_has_content, browser_visible):
    return bool(has_content) != bool(previous_has_content) or (not has_content and bool(browser_visible))
