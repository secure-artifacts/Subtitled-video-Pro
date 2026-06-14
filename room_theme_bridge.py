from theme_tokens import apply_room_theme, role_qss, tokens_from_mapping


BUTTON_ROLE_NAMES = {
    "primary": (
        "btn_save",
        "btn_top_save",
        "btn_export",
        "btn_start",
        "btn_create",
        "btn_render",
        "btn_save_shortcuts",
        "btn_save_fonts",
        "btn_signature_save_template",
        "btn_media_pool_primary",
    ),
    "secondary": (
        "btn_preview",
        "btn_top_preview",
        "btn_scan_hardware",
        "btn_update_download",
        "btn_refresh_font_assets",
        "btn_select_batch_projects",
        "btn_select_batch_output",
        "btn_select_batch_music",
        "btn_media_pool_preview",
        "btn_refresh",
        "btn_scan",
    ),
    "success": (
        "btn_apply",
        "btn_apply_all",
        "btn_apply_preset",
        "btn_apply_all_y",
        "btn_apply_batch_music_all",
        "btn_signature_apply_template",
        "btn_batch_export",
        "btn_batch_render",
        "btn_all_queue_render",
        "btn_create_export",
    ),
    "warning": (
        "btn_pause",
        "btn_batch_pause",
        "btn_export_pause",
        "btn_music_match",
        "btn_apply_safe_font",
        "btn_media_pool_secondary",
    ),
    "danger": (
        "btn_cancel",
        "btn_batch_cancel",
        "btn_export_cancel",
        "btn_delete",
        "btn_remove",
        "btn_del_clip",
        "btn_del_preset",
        "btn_v_del",
        "btn_a_del",
        "btn_music_del",
        "btn_signature_delete_template",
        "btn_clear_batch_music",
        "btn_clear_batch_queue",
        "btn_delete_export_queue",
        "btn_media_pool_remove",
        "btn_design_clear",
    ),
}


def apply_room_theme_bridge(room, theme=None) -> None:
    tokens = tokens_from_mapping(theme)
    apply_room_theme(room, tokens)
    for role, names in BUTTON_ROLE_NAMES.items():
        qss = role_qss(role, tokens)
        for name in names:
            widget = getattr(room, name, None)
            if widget is not None and hasattr(widget, "setStyleSheet"):
                widget.setStyleSheet(qss)
    if hasattr(room, "_theme_tokens"):
        room._theme_tokens = tokens


def themed_panel_qss(theme=None) -> str:
    tokens = tokens_from_mapping(theme)
    return f"""
    QFrame {{
        background-color: {tokens.panel};
        color: {tokens.text};
        border: 1px solid {tokens.border};
        border-radius: 7px;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {tokens.text_soft};
    }}
    """
