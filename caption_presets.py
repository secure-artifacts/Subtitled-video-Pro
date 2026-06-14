import copy


REFERENCE_NARRATIVE_BLOCK_PRESET = "参考视频 · 左下累积叙事块"
REFERENCE_NARRATIVE_CHUNK_MODE = "累积叙事块 (8-12词清屏)"


def reference_narrative_block_style():
    return {
        "size": 58,
        "font": "TikTok Sans",
        "font_weight": "900",
        "font_style": "normal",
        "color_txt": "#FFFFFF",
        "color_hl": "#FFFFFF",
        "bg_mode": "none",
        "bg_color": "#000000",
        "bg_alpha": 0,
        "stroke_width": 3,
        "stroke_color": "#000000",
        "stroke_o_width": 0,
        "stroke_softness": 18,
        "shadow_x": 0,
        "shadow_y": 4,
        "shadow_blur": 10,
        "shadow_color": "#000000",
        "shadow_alpha": 72,
        "line_height": 0.92,
        "text_align": "left",
        "text_transform": "uppercase",
        "letter_spacing": 0,
        "word_spacing": 0,
        "layout_mode": "narrative_block",
        "layout_variant": "auto",
        "box_layout": "fixed",
        "box_width": 64.0,
        "box_height": 0.0,
        "max_lines": 3,
        "emphasis_scale": 132,
        "contrast_small_scale": 0.78,
        "anim_type": "none",
        "font_motion": "none",
        "hl_motion": "stable",
        "hl_style": "text",
        "use_hl": False,
        "inactive_alpha": 0,
        "pop_speed": 0.12,
        "pop_bounce": 112,
        "text_texture": "none",
        "caption_build_mode": "cumulative_block",
        "caption_block_min_words": 8,
        "caption_block_max_words": 12,
    }


def reference_narrative_block_preset():
    style = reference_narrative_block_style()
    style["__position__"] = {"pos_x": -23.0, "pos_y": 20.0}
    return style


def built_in_style_presets():
    return {
        REFERENCE_NARRATIVE_BLOCK_PRESET: reference_narrative_block_preset(),
    }


def merge_built_in_style_presets(saved_presets):
    presets = copy.deepcopy(built_in_style_presets())
    if isinstance(saved_presets, dict):
        presets.update(saved_presets)
    return presets


def is_reference_narrative_chunk_mode(mode):
    text = str(mode or "")
    return (
        REFERENCE_NARRATIVE_CHUNK_MODE in text
        or "累积叙事块" in text
        or "8-12词" in text
        or "8-12" in text
    )
