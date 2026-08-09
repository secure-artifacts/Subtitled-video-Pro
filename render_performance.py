import copy
import json


EXPORT_RENDER_QUALITY_PROFILES = {
    "标准高清": {
        "render_scale_cap": None,
        "event_fps_cap": None,
        "continuous_fps_cap": None,
        "vp9_crf": "24",
        "vp9_deadline": "good",
        "vp9_cpu_used": "4",
        "vp9_tile_columns": "1",
    },
    "清晰快速": {
        "render_scale_cap": 1.0,
        "event_fps_cap": 8,
        "continuous_fps_cap": 10,
        "vp9_crf": "28",
        "vp9_deadline": "good",
        "vp9_cpu_used": "6",
        "vp9_tile_columns": "2",
    },
    "极速出片": {
        "render_scale_cap": 0.65,
        "event_fps_cap": 4,
        "continuous_fps_cap": 6,
        "vp9_crf": "34",
        "vp9_deadline": "realtime",
        "vp9_cpu_used": "8",
        "vp9_tile_columns": "3",
    },
}
DEFAULT_EXPORT_RENDER_QUALITY = "极速出片"


def normalize_export_render_quality(mode):
    text = str(mode or DEFAULT_EXPORT_RENDER_QUALITY).strip()
    return text if text in EXPORT_RENDER_QUALITY_PROFILES else DEFAULT_EXPORT_RENDER_QUALITY


def export_render_profile(mode, default_scale=1.0, default_event_fps=8, default_continuous_fps=12):
    mode = normalize_export_render_quality(mode)
    base = dict(EXPORT_RENDER_QUALITY_PROFILES[mode])
    try:
        scale = max(0.5, float(default_scale or 1.0))
    except Exception:
        scale = 1.0
    cap = base.get("render_scale_cap")
    render_scale = scale if cap is None else min(scale, float(cap))
    render_scale = max(0.5, float(render_scale or 1.0))

    def capped_fps(value, cap_value):
        try:
            fps = int(float(value))
        except Exception:
            fps = 8
        if cap_value is not None:
            fps = min(fps, int(cap_value))
        return max(4, min(30, fps))

    base["mode"] = mode
    base["render_scale"] = render_scale
    base["event_fps"] = capped_fps(default_event_fps, base.get("event_fps_cap"))
    base["continuous_fps"] = capped_fps(default_continuous_fps, base.get("continuous_fps_cap"))
    base["summary"] = f"{mode} / 字幕层 x{render_scale:g} / 词动画 {base['event_fps']}fps / 连续动画 {base['continuous_fps']}fps"
    return base

def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _to_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _enabled(value):
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "none")
    return bool(value)


def _style_key(style):
    try:
        return json.dumps(style or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(style or {})


def style_render_cost_score(style):
    style = style if isinstance(style, dict) else {}
    score = 0
    if _enabled(style.get("global_glow_enable")):
        blur = _to_int(style.get("global_glow_blur"), 24)
        score += 2
        if blur >= 30:
            score += 2
        if str(style.get("global_glow_motion", "stable") or "stable") != "stable":
            score += 1
    if _enabled(style.get("text_3d_enable")) and _to_int(style.get("text_3d_depth"), 0) > 0:
        score += 2
    if str(style.get("text_texture", "none") or "none") != "none":
        score += 1
    if _to_int(style.get("shadow_blur"), 0) >= 18:
        score += 1
    if _to_int(style.get("stroke_soft"), 0) >= 10:
        score += 1
    if _to_int(style.get("hl_trail_words"), 1) > 1:
        score += 1
    if _enabled(style.get("chk_hl_glow")) or _to_int(style.get("glow_size"), 0) >= 28:
        score += 1
    if _enabled(style.get("mask_en")):
        score += 1
    if str(style.get("anim_type", "") or "") in {
        "full_text_roll",
        "letter_scatter_in",
        "scatter_in",
        "camera_push",
        "depth_push",
        "holy_breath",
    }:
        score += 1
    if str(style.get("text_reveal_mode", "all") or "all") in {"word_voice", "line_voice"}:
        score += 1
    return score


def style_render_cost_notes(style, label="字幕样式"):
    style = style if isinstance(style, dict) else {}
    notes = []
    if _enabled(style.get("global_glow_enable")):
        blur = _to_int(style.get("global_glow_blur"), 24)
        size = _to_int(style.get("global_glow_size"), 18)
        motion = str(style.get("global_glow_motion", "stable") or "stable")
        notes.append(f"{label}: 整体发光 blur={blur}, 强度={size}, 动画={motion}; blur 超过 30 会明显拖慢字幕截图。")
    if _enabled(style.get("text_3d_enable")) and _to_int(style.get("text_3d_depth"), 0) > 0:
        notes.append(f"{label}: 字体 3D 厚度={_to_int(style.get('text_3d_depth'), 0)}; 立体阴影层数越多，截图越慢。")
    texture = str(style.get("text_texture", "none") or "none")
    if texture != "none":
        notes.append(f"{label}: 字体质感={texture}; 金属/纹理字会增加浏览器绘制成本。")
    shadow_blur = _to_int(style.get("shadow_blur"), 0)
    if shadow_blur >= 18:
        notes.append(f"{label}: 阴影模糊={shadow_blur}; 大柔边阴影会拖慢预览。")
    if _to_int(style.get("hl_trail_words"), 1) > 1:
        notes.append(f"{label}: 高亮拖尾={_to_int(style.get('hl_trail_words'), 1)} 词; 每帧会多绘制多个高亮状态。")
    if str(style.get("anim_type", "") or "") == "full_text_roll":
        notes.append(f"{label}: 全文滚动会按连续帧采样，字幕截图段数通常比普通字幕更多。")
    return notes


def summarize_project_render_cost(project_state, design_state=None):
    project_state = project_state if isinstance(project_state, dict) else {}
    subs = project_state.get("subs_data") or []
    notes = []
    score = 0
    if len(subs) >= 80:
        score += 2
        notes.append(f"字幕数量 {len(subs)} 条，导出前半段会生成较多透明字幕帧。")
    elif len(subs) >= 35:
        score += 1
        notes.append(f"字幕数量 {len(subs)} 条，字幕截图阶段会有一定压力。")

    seen = set()
    styles = []
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        style = sub.get("style") if isinstance(sub.get("style"), dict) else {}
        key = _style_key(style)
        if key in seen:
            continue
        seen.add(key)
        styles.append(style)

    signature = project_state.get("signature") if isinstance(project_state.get("signature"), dict) else {}
    if signature.get("enabled") and isinstance(signature.get("style"), dict):
        styles.append(signature.get("style"))

    for idx, style in enumerate(styles[:6], start=1):
        label = "署名" if idx == len(styles) and signature.get("enabled") else f"样式{idx}"
        style_score = style_render_cost_score(style)
        score += style_score
        if style_score >= 2:
            notes.extend(style_render_cost_notes(style, label=label)[:3])

    if bool(project_state.get("video_mask_enabled")) and _to_float(project_state.get("video_mask_alpha"), 0.0) > 0:
        score += 1
        notes.append("画面蒙版已启用，FFmpeg 合成会多一层颜色叠加。")

    level = "轻"
    if score >= 8:
        level = "重"
    elif score >= 4:
        level = "中"
    return {
        "score": score,
        "level": level,
        "subtitle_count": len(subs),
        "style_count": len(styles),
        "notes": notes[:8],
    }


def _cap_int_field(style, key, default, cap):
    if key in style:
        style[key] = min(_to_int(style.get(key), default), int(cap))


def simplify_style_for_export(style, mode="清晰快速"):
    source = style if isinstance(style, dict) else {}
    simplified = copy.deepcopy(source)
    mode = normalize_export_render_quality(mode)
    if mode == "标准高清":
        return simplified

    force = mode == "极速出片"
    if not force and style_render_cost_score(simplified) < 3:
        return simplified

    if _enabled(simplified.get("global_glow_enable")):
        _cap_int_field(simplified, "global_glow_blur", 24, 14 if force else 22)
        _cap_int_field(simplified, "global_glow_size", 18, 22 if force else 34)
        _cap_int_field(simplified, "global_glow_alpha", 35, 42 if force else 58)
        _cap_int_field(simplified, "global_glow_z", 0, 45 if force else 90)
        if force:
            simplified["global_glow_motion"] = "stable"
    if _enabled(simplified.get("text_3d_enable")):
        _cap_int_field(simplified, "text_3d_depth", 0, 18 if force else 34)
    _cap_int_field(simplified, "shadow_blur", 0, 9 if force else 16)
    _cap_int_field(simplified, "stroke_softness", 0, 16 if force else 28)
    _cap_int_field(simplified, "glow_size", 20, 20 if force else 30)
    _cap_int_field(simplified, "hl_trail_words", 1, 1 if force else 2)

    texture = str(simplified.get("text_texture", "none") or "none")
    if force and texture in {"stacked_distress", "distressed", "roughen", "noise"}:
        simplified["text_texture"] = "grain"
    if force and str(simplified.get("font_motion", "none") or "none") in {"ripple3d", "drift", "wave"}:
        simplified["font_motion"] = "pulse"
    if force and str(simplified.get("bg_mode", "none") or "none") == "sweep":
        simplified["bg_mode"] = "canva_fit"
    return simplified


def simplify_subtitle_for_export(subtitle, mode="清晰快速"):
    if not isinstance(subtitle, dict):
        return subtitle
    rendered = copy.deepcopy(subtitle)
    rendered["style"] = simplify_style_for_export(subtitle.get("style"), mode)
    return rendered


def simplify_signature_for_export(signature, mode="清晰快速"):
    if not isinstance(signature, dict):
        return signature
    rendered = copy.deepcopy(signature)
    if isinstance(rendered.get("style"), dict):
        rendered["style"] = simplify_style_for_export(rendered.get("style"), mode)
    return rendered

def simplify_style_for_preview(style, mode="自动流畅"):
    source = style if isinstance(style, dict) else {}
    simplified = copy.deepcopy(source)
    text = str(mode or "自动流畅")
    if "完整" in text:
        return simplified
    force = "极速" in text
    if not force and style_render_cost_score(simplified) < 3:
        return simplified

    if _enabled(simplified.get("global_glow_enable")):
        simplified["global_glow_blur"] = min(_to_int(simplified.get("global_glow_blur"), 24), 10 if force else 18)
        simplified["global_glow_size"] = min(_to_int(simplified.get("global_glow_size"), 18), 16 if force else 28)
        simplified["global_glow_alpha"] = min(_to_int(simplified.get("global_glow_alpha"), 35), 28 if force else 42)
        simplified["global_glow_motion"] = "stable"
    if _enabled(simplified.get("text_3d_enable")):
        simplified["text_3d_depth"] = min(_to_int(simplified.get("text_3d_depth"), 0), 8 if force else 18)
    simplified["shadow_blur"] = min(_to_int(simplified.get("shadow_blur"), 0), 6 if force else 12)
    simplified["stroke_soft"] = min(_to_int(simplified.get("stroke_soft"), 0), 6 if force else 10)
    simplified["glow_size"] = min(_to_int(simplified.get("glow_size"), 0), 16 if force else 24)
    simplified["hl_trail_words"] = min(_to_int(simplified.get("hl_trail_words"), 1), 1 if force else 2)
    if force and str(simplified.get("text_texture", "none") or "none") not in ("none", "gold_metal"):
        simplified["text_texture"] = "none"
    return simplified


def simplify_subtitle_for_preview(subtitle, mode="自动流畅"):
    if not isinstance(subtitle, dict):
        return subtitle
    preview = dict(subtitle)
    preview["style"] = simplify_style_for_preview(subtitle.get("style"), mode)
    return preview


def simplify_signature_for_preview(signature, mode="自动流畅"):
    if not isinstance(signature, dict):
        return signature
    preview = copy.deepcopy(signature)
    if isinstance(preview.get("style"), dict):
        preview["style"] = simplify_style_for_preview(preview.get("style"), mode)
    return preview
