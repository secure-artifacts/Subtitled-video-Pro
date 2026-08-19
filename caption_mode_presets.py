import copy
import os

from app_storage import read_json_file, resolve_user_file, write_json_file
from caption_presets import (
    COMPACT_NARRATIVE_CHUNK_MODE,
    FULL_TEXT_CHUNK_MODE,
    REFERENCE_NARRATIVE_CHUNK_MODE,
    fixed_word_count_for_chunk_mode,
    is_exact_single_word_chunk_mode,
    is_full_text_chunk_mode,
    make_fixed_chunk_mode_label,
    make_smart_chunk_mode_label,
    narrative_chunk_word_bounds,
    smart_transcription_word_bounds,
)


CAPTION_MODE_PRESETS_FILE = resolve_user_file("caption_mode_presets.json", legacy_root=os.getcwd(), kind="config")


def _safe_int(value, default=0, minimum=None, maximum=None):
    try:
        result = int(round(float(value)))
    except Exception:
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def caption_mode_fixed_count(mode):
    if is_exact_single_word_chunk_mode(mode):
        return 1
    return fixed_word_count_for_chunk_mode(mode)


def caption_mode_word_range(mode):
    if is_full_text_chunk_mode(mode):
        return 0, 0
    min_words, max_words = narrative_chunk_word_bounds(mode)
    if max_words > 0:
        return min_words, max_words
    min_words, max_words = smart_transcription_word_bounds(mode)
    if max_words > 0:
        return min_words, max_words
    return 4, 7


def caption_mode_config_from_values(chunk_mode="", timing_mode="", strategy="", fixed_words=1, min_words=0, max_words=0):
    chunk_mode = str(chunk_mode or "").strip() or make_smart_chunk_mode_label(min_words or 4, max_words or 7)
    strategy = str(strategy or "").strip().lower()
    fixed_count = caption_mode_fixed_count(chunk_mode)
    if is_full_text_chunk_mode(chunk_mode) or strategy in {"full", "full_text", "flat", "tile", "tiled"}:
        return {
            "chunk_mode": FULL_TEXT_CHUNK_MODE,
            "timing_mode": str(timing_mode or "").strip(),
            "strategy": "full_text",
            "fixed_words": 0,
            "min_words": 0,
            "max_words": 0,
        }
    if strategy not in {"fixed", "smart"}:
        strategy = "fixed" if fixed_count > 0 else "smart"
    if strategy == "fixed":
        fixed_count = _safe_int(fixed_words or fixed_count or 1, 1, 1, 30)
        min_words = max_words = fixed_count
        chunk_mode = make_fixed_chunk_mode_label(fixed_count)
    else:
        inferred_min, inferred_max = caption_mode_word_range(chunk_mode)
        min_words = _safe_int(min_words or inferred_min or 4, 4, 1, 30)
        max_words = _safe_int(max_words or inferred_max or min_words, max(min_words, 7), 1, 30)
        if max_words < min_words:
            min_words, max_words = max_words, min_words
        if caption_mode_word_range(chunk_mode) != (min_words, max_words) or caption_mode_fixed_count(chunk_mode) > 0:
            chunk_mode = make_smart_chunk_mode_label(min_words, max_words, chunk_mode)
    return {
        "chunk_mode": chunk_mode,
        "timing_mode": str(timing_mode or "").strip(),
        "strategy": strategy,
        "fixed_words": fixed_count if strategy == "fixed" else 0,
        "min_words": min_words,
        "max_words": max_words,
    }


def normalize_caption_mode_preset(raw=None):
    if isinstance(raw, str):
        return caption_mode_config_from_values(raw)
    if not isinstance(raw, dict):
        raw = {}
    chunk_mode = str(raw.get("chunk_mode") or raw.get("mode") or "").strip()
    timing_mode = str(raw.get("timing_mode") or raw.get("timing") or "").strip()
    strategy = str(raw.get("strategy") or raw.get("kind") or "").strip().lower()
    fixed_words = raw.get("fixed_words", raw.get("fixed_count", 1))
    min_words = raw.get("min_words", raw.get("min", 0))
    max_words = raw.get("max_words", raw.get("max", 0))
    return caption_mode_config_from_values(chunk_mode, timing_mode, strategy, fixed_words, min_words, max_words)


def caption_mode_final_chunk(config, base_mode=""):
    cfg = normalize_caption_mode_preset(config)
    if cfg["strategy"] == "full_text":
        return FULL_TEXT_CHUNK_MODE
    if cfg["strategy"] == "fixed":
        return make_fixed_chunk_mode_label(cfg["fixed_words"] or 1)
    base = str(base_mode or cfg.get("chunk_mode") or "").strip()
    if base and caption_mode_fixed_count(base) == 0 and caption_mode_word_range(base) == (cfg["min_words"], cfg["max_words"]):
        return base
    return make_smart_chunk_mode_label(cfg["min_words"], cfg["max_words"], base)


def built_in_caption_mode_presets():
    return {
        "单个词 · 精准逐词": caption_mode_config_from_values(make_fixed_chunk_mode_label(1), "对齐声音 (按停顿)", "fixed", 1),
        "2词 · 稳定短词": caption_mode_config_from_values(make_fixed_chunk_mode_label(2), "对齐声音 (按停顿)", "fixed", 2),
        "3词 · 快节奏": caption_mode_config_from_values(make_fixed_chunk_mode_label(3), "对齐声音 (按停顿)", "fixed", 3),
        "4词 · 短句": caption_mode_config_from_values(make_fixed_chunk_mode_label(4), "对齐声音 (按停顿)", "fixed", 4),
        "智能 4-7词 · 默认双行": caption_mode_config_from_values("智能听译 (4-7词，适配双行按词)", "对齐声音 (按停顿)", "smart", min_words=4, max_words=7),
        "智能 5-9词 · 稳定叙述": caption_mode_config_from_values("智能听译 (5-9词，自定义)", "对齐声音 (按停顿)", "smart", min_words=5, max_words=9),
        "平铺听译 · 全文显示": caption_mode_config_from_values(FULL_TEXT_CHUNK_MODE, "对齐声音 (按停顿)", "full_text"),
        "全部打轴 · 整篇文字": caption_mode_config_from_values(FULL_TEXT_CHUNK_MODE, "对齐声音 (按停顿)", "full_text"),
        "累计叙事 8-12词": caption_mode_config_from_values(COMPACT_NARRATIVE_CHUNK_MODE, "对齐声音 (按停顿)", "smart", min_words=8, max_words=12),
        "累计叙事 14-18词": caption_mode_config_from_values(REFERENCE_NARRATIVE_CHUNK_MODE, "对齐声音 (按停顿)", "smart", min_words=14, max_words=18),
    }


def load_saved_caption_mode_presets():
    saved = read_json_file(CAPTION_MODE_PRESETS_FILE, default={})
    if not isinstance(saved, dict):
        return {}
    return {str(name): normalize_caption_mode_preset(value) for name, value in saved.items() if str(name).strip()}


def load_caption_mode_presets():
    presets = built_in_caption_mode_presets()
    presets.update(load_saved_caption_mode_presets())
    return presets


def is_built_in_caption_mode_preset(name):
    return str(name or "") in built_in_caption_mode_presets()


def save_caption_mode_preset(name, config):
    name = str(name or "").strip()
    if not name:
        return False
    saved = load_saved_caption_mode_presets()
    saved[name] = normalize_caption_mode_preset(config)
    write_json_file(CAPTION_MODE_PRESETS_FILE, saved, indent=2)
    return True


def delete_caption_mode_preset(name):
    name = str(name or "").strip()
    if not name:
        return False
    saved = load_saved_caption_mode_presets()
    if name not in saved:
        return False
    del saved[name]
    write_json_file(CAPTION_MODE_PRESETS_FILE, saved, indent=2)
    return True


def ensure_caption_mode_options(combo, presets=None):
    if combo is None:
        return
    presets = presets or load_caption_mode_presets()
    for cfg in presets.values():
        mode = normalize_caption_mode_preset(copy.deepcopy(cfg)).get("chunk_mode", "")
        if mode and combo.findText(mode) < 0:
            combo.addItem(mode)
