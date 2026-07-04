import copy


REFERENCE_NARRATIVE_BLOCK_PRESET = "参考视频 · 左下累积叙事块"
COMPACT_NARRATIVE_BLOCK_PRESET = "参考视频 · 0516紧凑累积叙事块"
GOLD_METALLIC_TEXT_PRESET = "\u91d1\u8272\u8d28\u611f\u5b57 \u00b7 \u9ed1\u5e95\u7977\u544a"
REFERENCE_NARRATIVE_CHUNK_MODE = "累积叙事块 (14-18词清屏)"
COMPACT_NARRATIVE_CHUNK_MODE = "0516累积叙事块 (8-12词清屏)"

# Backward-compatible aliases for files that were already migrated.
LEGACY_NARRATIVE_BLOCK_PRESET = COMPACT_NARRATIVE_BLOCK_PRESET
LEGACY_NARRATIVE_CHUNK_MODE = COMPACT_NARRATIVE_CHUNK_MODE
NARRATIVE_CHUNK_MODES = (REFERENCE_NARRATIVE_CHUNK_MODE, COMPACT_NARRATIVE_CHUNK_MODE)


def reference_narrative_block_style():
    return {
        "size": 54,
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
        "line_height": 0.86,
        "layout_row_gap": 108,
        "text_align": "left",
        "text_transform": "uppercase",
        "letter_spacing": 0,
        "word_spacing": 0,
        "layout_mode": "narrative_block",
        "layout_variant": "auto",
        "box_layout": "fixed",
        "box_width": 64.0,
        "box_height": 0.0,
        "max_lines": 4,
        "emphasis_scale": 168,
        "contrast_small_scale": 0.62,
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
        "caption_block_min_words": 14,
        "caption_block_max_words": 18,
    }


def compact_narrative_block_style():
    style = reference_narrative_block_style()
    style.update({
        "size": 58,
        "line_height": 0.92,
        "layout_row_gap": 106,
        "max_lines": 3,
        "emphasis_scale": 132,
        "contrast_small_scale": 0.78,
        "caption_block_min_words": 8,
        "caption_block_max_words": 12,
    })
    return style


def reference_narrative_block_preset():
    style = reference_narrative_block_style()
    style["__position__"] = {"pos_x": -23.0, "pos_y": 20.0}
    return style


def compact_narrative_block_preset():
    style = compact_narrative_block_style()
    style["__position__"] = {"pos_x": -23.0, "pos_y": 20.0}
    return style


def gold_metal_text_style():
    return {
        "size": 70,
        "font": "TikTok Sans",
        "font_weight": "900",
        "font_style": "normal",
        "color_txt": "#F6C14A",
        "color_hl": "#FFF0A6",
        "bg_mode": "none",
        "bg_color": "#000000",
        "bg_alpha": 0,
        "stroke_width": 2,
        "stroke_color": "#6F3A05",
        "stroke_o_width": 0,
        "stroke_o_color": "#000000",
        "stroke_softness": 20,
        "shadow_x": 0,
        "shadow_y": 8,
        "shadow_blur": 18,
        "shadow_color": "#000000",
        "shadow_alpha": 78,
        "global_glow_enable": True,
        "global_glow_mode": "soft",
        "global_glow_motion": "stable",
        "global_glow_color": "#F2B43A",
        "global_glow_size": 20,
        "global_glow_blur": 42,
        "global_glow_alpha": 46,
        "global_glow_x": 0,
        "global_glow_y": 4,
        "global_glow_z": 30,
        "line_height": 1.12,
        "layout_row_gap": 100,
        "text_align": "center",
        "text_transform": "uppercase",
        "letter_spacing": 0,
        "word_spacing": 0,
        "layout_mode": "standard",
        "layout_variant": "auto",
        "box_layout": "fixed",
        "box_width": 82.0,
        "box_height": 0.0,
        "max_lines": 6,
        "anim_type": "none",
        "font_motion": "none",
        "hl_motion": "stable",
        "hl_style": "none",
        "use_hl": False,
        "inactive_alpha": 0,
        "pop_speed": 0.12,
        "pop_bounce": 112,
        "text_texture": "gold_metal",
        "text_3d_enable": True,
        "text_3d_depth": 42,
        "text_3d_x": 2,
        "text_3d_y": 3,
        "text_3d_color": "#6F3A05",
    }


def gold_metal_text_preset():
    style = gold_metal_text_style()
    style["__position__"] = {"pos_x": 0.0, "pos_y": 42.0}
    return style


def legacy_narrative_block_style():
    return compact_narrative_block_style()


def legacy_narrative_block_preset():
    return compact_narrative_block_preset()


def built_in_style_presets():
    return {
        REFERENCE_NARRATIVE_BLOCK_PRESET: reference_narrative_block_preset(),
        COMPACT_NARRATIVE_BLOCK_PRESET: compact_narrative_block_preset(),
        GOLD_METALLIC_TEXT_PRESET: gold_metal_text_preset(),
    }


def merge_built_in_style_presets(saved_presets):
    presets = copy.deepcopy(built_in_style_presets())
    if isinstance(saved_presets, dict):
        presets.update(saved_presets)
    return presets


def is_compact_narrative_chunk_mode(mode):
    text = str(mode or "")
    return (
        COMPACT_NARRATIVE_CHUNK_MODE in text
        or "0516累积叙事" in text
        or "紧凑累积叙事" in text
        or "8-12词" in text
        or "8-12" in text
    )


def is_legacy_narrative_chunk_mode(mode):
    return is_compact_narrative_chunk_mode(mode)


def is_reference_narrative_chunk_mode(mode):
    text = str(mode or "")
    return (
        REFERENCE_NARRATIVE_CHUNK_MODE in text
        or is_compact_narrative_chunk_mode(text)
        or "14-18词" in text
        or "14-18" in text
        or "累积叙事块" in text
    )


def narrative_chunk_word_bounds(mode):
    if is_compact_narrative_chunk_mode(mode):
        return 8, 12
    if is_reference_narrative_chunk_mode(mode):
        return 14, 18
    return 0, 0


def narrative_chunk_merge_words(mode):
    return 14 if is_compact_narrative_chunk_mode(mode) else 18


def is_exact_single_word_chunk_mode(mode):
    text = str(mode or "")
    return (
        any(token in text for token in ("单字", "单词", "逐词", "1词"))
        or any(token in text for token in ("Ã¥Ââ€¢Ã¥Â­â€”", "Ã¥Ââ€¢Ã¨Â¯Â", "Ã©â‚¬ï¿½Ã¨Â¯Â", "1Ã¨Â¯Â"))
    ) and not any(token in text for token in ("1-3", "1-4"))


def fixed_word_count_for_chunk_mode(mode):
    text = str(mode or "")
    if (
        is_reference_narrative_chunk_mode(text)
        or "智能听译" in text
        or "4-7词" in text
        or "4-7" in text
        or "智能重点" in text
        or "3-4词为主" in text
        or "自然短句" in text
        or "1-4" in text
    ):
        return 0
    if "短句快速" in text or "短句快闪" in text or "1-3" in text or "3-5" in text:
        return 3
    if "双词" in text or "2词" in text:
        return 2
    if "三词" in text or "3词" in text:
        return 3
    if "四词" in text or "4词" in text:
        return 4
    return 0


def is_precise_chunk_mode(mode):
    return is_exact_single_word_chunk_mode(mode) or fixed_word_count_for_chunk_mode(mode) > 0
