import copy
import re


REFERENCE_NARRATIVE_BLOCK_PRESET = "参考视频 · 左下累积叙事块"
COMPACT_NARRATIVE_BLOCK_PRESET = "参考视频 · 0516紧凑累积叙事块"
GOLD_METALLIC_TEXT_PRESET = "\u91d1\u8272\u8d28\u611f\u5b57 \u00b7 \u9ed1\u5e95\u7977\u544a"
FULL_TEXT_ROLL_PRESET = "全文滚动小窗 · 打轴"
REFERENCE_NARRATIVE_CHUNK_MODE = "累积叙事块 (14-18词清屏)"
COMPACT_NARRATIVE_CHUNK_MODE = "0516累积叙事块 (8-12词清屏)"
FULL_TEXT_CHUNK_MODE = "\u5168\u90e8\u6253\u8f74 (\u6574\u7bc7\u6587\u5b57)"

# Backward-compatible aliases for files that were already migrated.
LEGACY_NARRATIVE_BLOCK_PRESET = COMPACT_NARRATIVE_BLOCK_PRESET
LEGACY_NARRATIVE_CHUNK_MODE = COMPACT_NARRATIVE_CHUNK_MODE
NARRATIVE_CHUNK_MODES = (REFERENCE_NARRATIVE_CHUNK_MODE, COMPACT_NARRATIVE_CHUNK_MODE)

DEFAULT_CHUNK_MODES = (
    "\u56fa\u5b9a\u5b57\u6570 (1\u8bcd/\u53e5)",
    "\u56fa\u5b9a\u5b57\u6570 (2\u8bcd/\u53e5)",
    "\u667a\u80fd\u542c\u8bd1 (4-7\u8bcd\uff0c\u9002\u914d\u53cc\u884c\u6309\u8bcd)",
    FULL_TEXT_CHUNK_MODE,
    REFERENCE_NARRATIVE_CHUNK_MODE,
    COMPACT_NARRATIVE_CHUNK_MODE,
    "\u667a\u80fd\u91cd\u70b9\u77ed\u53e5 (3-4\u8bcd\u4e3a\u4e3b)",
    "\u81ea\u7136\u77ed\u53e5 (1-4\u8bcd)",
    "\u4e09\u8bcd\u77ed\u53e5 (3\u8bcd/\u53e5)",
    "\u56db\u8bcd\u77ed\u53e5 (4\u8bcd/\u53e5)",
    "\u53cc\u884c\u5927\u6bb5 (\u7ea610\u5b57\uff0c\u667a\u80fd\u6298\u884c)",
    "\u77ed\u53e5\u5feb\u901f (1-3\u5b57)",
    "\u77ed\u53e5\u5feb\u95ea (3-5\u5b57)",
    "\u5355\u5b57\u8f70\u70b8 (1\u5b57/\u53e5)",
)


def chunk_mode_options():
    return list(DEFAULT_CHUNK_MODES)


def make_fixed_chunk_mode_label(count):
    count = max(1, min(30, int(count or 1)))
    return f"\u56fa\u5b9a\u5b57\u6570 ({count}\u8bcd/\u53e5)"


def make_smart_chunk_mode_label(min_words, max_words, preset_mode=None):
    min_words = max(1, min(30, int(min_words or 1)))
    max_words = max(min_words, min(30, int(max_words or min_words)))
    text = _chunk_mode_text(preset_mode)
    if "\u7d2f\u79ef\u53d9\u4e8b" in text:
        prefix = "0516\u7d2f\u79ef\u53d9\u4e8b\u5757" if "0516" in text else "\u7d2f\u79ef\u53d9\u4e8b\u5757"
        return f"{prefix} ({min_words}-{max_words}\u8bcd\u6e05\u5c4f)"
    if any(token in text for token in ("\u53cc\u884c", "\u957f\u53e5", "\u7ea610", "\u5927\u6bb5")):
        return f"\u53cc\u884c\u5927\u6bb5 ({min_words}-{max_words}\u8bcd\uff0c\u667a\u80fd\u6298\u884c)"
    return f"\u667a\u80fd\u542c\u8bd1 ({min_words}-{max_words}\u8bcd\uff0c\u81ea\u5b9a\u4e49)"


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
        "scene_light_enable": True,
        "scene_light_trigger": "word",
        "scene_light_color": "#F6C76A",
        "scene_light_mask_color": "#000000",
        "scene_light_dim": 90,
        "scene_light_strength": 92,
        "scene_light_radius": 620,
        "scene_light_x_scale": 48,
        "scene_light_y_scale": 118,
        "scene_light_decay": 0.50,
        "scene_light_blur": 62,
        "scene_light_spill": 88,
        "scene_light_edge_lift": 46,
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



def full_text_roll_style():
    style = gold_metal_text_style()
    style.update({
        "size": 62,
        "font": "TikTok Sans",
        "color_txt": "#FFFFFF",
        "color_hl": "#FFE600",
        "bg_mode": "bottom_band",
        "bg_color": "#000000",
        "bg_alpha": 64,
        "stroke_width": 3,
        "stroke_color": "#000000",
        "stroke_softness": 14,
        "shadow_x": 0,
        "shadow_y": 4,
        "shadow_blur": 10,
        "shadow_alpha": 68,
        "line_height": 1.02,
        "layout_row_gap": 96,
        "text_align": "center",
        "text_transform": "none",
        "letter_spacing": 0,
        "word_spacing": 0,
        "layout_mode": "standard",
        "layout_variant": "auto",
        "box_layout": "fixed",
        "box_width": 92.0,
        "box_height": 0.0,
        "max_lines": 6,
        "anim_type": "full_text_roll",
        "font_motion": "none",
        "text_reveal_mode": "all",
        "full_roll_window_mode": "lines",
        "full_roll_visible_lines": 3,
        "full_roll_window_height": 28,
        "full_roll_start_y": 18,
        "full_roll_end_y": -16,
        "full_roll_feather": 8,
        "full_roll_lock_to_words": True,
        "global_glow_enable": False,
        "scene_light_enable": False,
        "text_texture": "none",
        "text_3d_enable": False,
        "caption_build_mode": "full_text",
    })
    return style


def full_text_roll_preset():
    style = full_text_roll_style()
    style["__position__"] = {"pos_x": 0.0, "pos_y": 34.0}
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
        FULL_TEXT_ROLL_PRESET: full_text_roll_preset(),
    }


def merge_built_in_style_presets(saved_presets):
    presets = copy.deepcopy(built_in_style_presets())
    if isinstance(saved_presets, dict):
        presets.update(saved_presets)
    return presets


def _chunk_mode_text(mode):
    return (
        str(mode or "")
        .replace("\u7d2f\u8ba1", "\u7d2f\u79ef")
        .replace("\u7d2f\u8a08", "\u7d2f\u79ef")
        .replace("\uff0d", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )



def _extract_word_range(mode):
    text = _chunk_mode_text(mode)
    match = re.search(r"(\d+)\s*-\s*(\d+)\s*(?:\u8bcd|\u5b57)?", text)
    if not match:
        return 0, 0
    min_words = max(1, min(30, int(match.group(1))))
    max_words = max(1, min(30, int(match.group(2))))
    if max_words < min_words:
        min_words, max_words = max_words, min_words
    return min_words, max_words


def is_smart_transcription_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    if narrative_chunk_word_bounds(text)[1] > 0 or is_full_text_chunk_mode(text):
        return False
    return any(token in text for token in ("\u667a\u80fd\u542c\u8bd1", "AI\u542c\u8bd1", "4-7", "4-6"))


def smart_transcription_word_bounds(mode):
    if not is_smart_transcription_chunk_mode(mode):
        return 0, 0
    min_words, max_words = _extract_word_range(mode)
    if max_words > 0:
        return min_words, max_words
    return 4, 7


def is_full_text_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    return any(token in text for token in (FULL_TEXT_CHUNK_MODE, "\u5168\u90e8\u6253\u8f74", "\u6574\u7bc7\u6253\u8f74", "\u5168\u6587\u6253\u8f74", "\u6574\u7bc7\u6587\u5b57", "\u5168\u6587\u663e\u793a"))


def is_compact_narrative_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    return (
        COMPACT_NARRATIVE_CHUNK_MODE in text
        or "0516\u7d2f\u79ef\u53d9\u4e8b" in text
        or "\u7d27\u51d1\u7d2f\u79ef\u53d9\u4e8b" in text
        or "8-12\u8bcd" in text
        or "8-12\u5b57" in text
        or "8-12" in text
        or ("\u7d2f\u79ef\u53d9\u4e8b" in text and ("12\u8bcd" in text or "12\u5b57" in text))
    )


def is_legacy_narrative_chunk_mode(mode):
    return is_compact_narrative_chunk_mode(mode)


def is_reference_narrative_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    return (
        REFERENCE_NARRATIVE_CHUNK_MODE in text
        or is_compact_narrative_chunk_mode(text)
        or "14-18\u8bcd" in text
        or "14-18\u5b57" in text
        or "14-18" in text
        or "\u7d2f\u79ef\u53d9\u4e8b\u5757" in text
    )


def narrative_chunk_word_bounds(mode):
    text = _chunk_mode_text(mode)
    if "\u7d2f\u79ef\u53d9\u4e8b" in text:
        min_words, max_words = _extract_word_range(text)
        if max_words > 0:
            return min_words, max_words
    if is_compact_narrative_chunk_mode(text):
        return 8, 12
    if is_reference_narrative_chunk_mode(text):
        return 14, 18
    return 0, 0


def narrative_chunk_merge_words(mode):
    _, max_words = narrative_chunk_word_bounds(mode)
    return max_words if max_words > 0 else 18


def is_exact_single_word_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    return (
        any(token in text for token in ("单字", "单词", "逐词", "1词", "1字", "一词", "一字"))
        or any(token in text for token in ("Ã¥Ââ€¢Ã¥Â­â€”", "Ã¥Ââ€¢Ã¨Â¯Â", "Ã©â‚¬ï¿½Ã¨Â¯Â", "1Ã¨Â¯Â"))
    ) and not any(token in text for token in ("1-3", "1-4"))


def fixed_word_count_for_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    fixed_match = re.search(r"(?:\u56fa\u5b9a\u5b57\u6570|\u56fa\u5b9a\u8bcd\u6570)\D{0,12}(\d+)\s*(?:\u8bcd|\u5b57)", text)
    if fixed_match:
        return max(1, min(30, int(fixed_match.group(1))))
    if is_smart_transcription_chunk_mode(text):
        return 0
    if is_full_text_chunk_mode(text):
        return 0
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
    if any(token in text for token in ("双词", "双字", "2词", "2字", "二词", "二字")):
        return 2
    if any(token in text for token in ("三词", "三字", "3词", "3字")):
        return 3
    if any(token in text for token in ("四词", "四字", "4词", "4字")):
        return 4
    return 0


def pacing_merge_word_limit_for_chunk_mode(mode):
    text = _chunk_mode_text(mode)
    if is_full_text_chunk_mode(text) or is_exact_single_word_chunk_mode(text) or fixed_word_count_for_chunk_mode(text) > 0:
        return 0
    _, narrative_max_words = narrative_chunk_word_bounds(text)
    if narrative_max_words > 0:
        return narrative_max_words
    smart_min_words, smart_max_words = smart_transcription_word_bounds(text)
    if smart_max_words > 0:
        return smart_max_words
    if any(token in text for token in ("\u53cc\u884c", "\u957f\u53e5", "\u7ea610", "\u5927\u6bb5")):
        return 12
    if any(token in text for token in ("\u667a\u80fd\u91cd\u70b9", "3-4\u8bcd\u4e3a\u4e3b", "3-4")):
        return 4
    if any(token in text for token in ("\u81ea\u7136\u77ed\u53e5", "1-4")):
        return 4
    if any(token in text for token in ("\u667a\u80fd\u542c\u8bd1", "4-7\u8bcd", "4-7", "4-6")):
        return 7
    return 8

def chunk_mode_preserves_caption_blocks(mode):
    text = _chunk_mode_text(mode)
    if is_full_text_chunk_mode(text) or narrative_chunk_word_bounds(text)[1] > 0:
        return True
    return any(token in text for token in ("\u53cc\u884c", "\u957f\u53e5", "\u7ea610", "\u5927\u6bb5"))

def is_precise_chunk_mode(mode):
    return is_exact_single_word_chunk_mode(mode) or fixed_word_count_for_chunk_mode(mode) > 0
