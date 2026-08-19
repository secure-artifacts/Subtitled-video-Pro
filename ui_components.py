# ==========================================
# 文件名: ui_components.py (无缝融合 + 宽度拉伸 + 羽化蒙版滚动 + 平滑边缘抗锯齿修复)
# ==========================================
import math
import copy
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QObject, pyqtSlot

import media_probe
from subtitle_render_utils import css_font_stack, html_attr, html_multiline_text, html_text

FAITH_WORDS = {"god", "jesus", "amen", "lord", "christ", "holy", "bible"}
READABILITY_SUBJECT_GLUE_WORDS = {"god", "jesus", "lord", "christ", "holy"}
READABILITY_TRAILING_GLUE_WORDS = {
    "i", "a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "from",
    "by", "about", "as", "into", "like", "through", "after", "over", "between",
    "out", "against", "during", "without", "before", "under", "around", "among",
    "and", "but", "or", "so", "because", "if", "when", "while", "that", "this",
    "these", "those", "my", "your", "his", "her", "its", "our", "their", "is",
    "am", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "can", "could",
    "may", "might", "must", "not",
}

def _readability_word_key(value):
    return re.sub(r"[^a-zA-Z0-9']", "", str(value or "")).lower()

def should_defer_subtitle_break_for_readability(
    current_word,
    next_word,
    *,
    segment_word_count=0,
    silence_gap=0.0,
    has_punct=False,
    is_last_word=False,
):
    if is_last_word or not next_word:
        return False
    if has_punct:
        return False
    try:
        silence_gap = float(silence_gap)
    except Exception:
        silence_gap = 0.0
    if silence_gap > 1.15:
        return False

    current_key = _readability_word_key(current_word)
    next_key = _readability_word_key(next_word)
    if not current_key or not next_key:
        return False
    if current_key in READABILITY_SUBJECT_GLUE_WORDS:
        return silence_gap < 0.95
    if current_key in READABILITY_TRAILING_GLUE_WORDS:
        return True
    return int(segment_word_count or 0) <= 1 and silence_gap < 0.65
APOSTROPHES = {"'", "’", "‘", "`"}
ENGLISH_SUFFIX_TOKENS = {
    "'s", "'m", "'re", "'ve", "'ll", "'d", "'t",
    "n't", "n’t", "’s", "’m", "’re", "’ve", "’ll", "’d", "’t",
}


MIN_WORD_DURATION_SECONDS = 0.04
MIN_SUBTITLE_DURATION_SECONDS = 0.18
FAST_WORD_VISUAL_MIN_SECONDS = 0.14
FAST_SUBTITLE_READABLE_MIN_SECONDS = 0.42


def _safe_float(value, default=0.0):
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def default_signature_style(base_style=None, scale_from_subtitle=True):
    style = copy.deepcopy(base_style or {})
    try:
        base_size = int(style.get("size", 42) or 42)
    except Exception:
        base_size = 42
    style.update({
        "size": int(base_size * 0.42) if scale_from_subtitle and base_size > 70 else base_size,
        "font": style.get("font", "Noto Sans SC"),
        "font_weight": style.get("font_weight", "700"),
        "font_style": style.get("font_style", "normal"),
        "color_txt": style.get("color_txt", "#FFFFFF"),
        "color_hl": style.get("color_hl", "#FFFFFF"),
        "bg_mode": "cinematic_frame",
        "bg_color": style.get("bg_color", "#0B1020"),
        "bg_alpha": 45,
        "bg_radius": 26,
        "bg_padding": 10,
        "bg_pad_left": 18,
        "bg_pad_right": 18,
        "bg_pad_top": 5,
        "bg_pad_bottom": 6,
        "hl_bg_color": style.get("hl_bg_color", "#FFFFFF"),
        "hl_bg_alpha": 0,
        "stroke_width": min(2, int(style.get("stroke_width", 2) or 2)),
        "stroke_color": style.get("stroke_color", "#000000"),
        "stroke_o_width": 0,
        "shadow_x": 0,
        "shadow_y": 3,
        "shadow_blur": 10,
        "shadow_color": "#000000",
        "shadow_alpha": 55,
        "line_height": 1.0,
        "layout_row_gap": 100,
        "text_transform": "normal",
        "text_align": "right",
        "letter_spacing": 0,
        "word_spacing": 0,
        "layout_mode": "standard",
        "layout_variant": "auto",
        "box_layout": "auto",
        "box_width": 0,
        "box_height": 0,
        "max_lines": 1,
        "mask_en": False,
        "use_hl": False,
        "hl_glow": False,
        "anim_type": "none",
        "font_motion": "none",
        "hl_motion": "stable",
        "inactive_alpha": 100,
        "text_texture": "none",
    })
    return style


def default_signature_config(base_style=None):
    return {
        "enabled": False,
        "text": "",
        "placement": "top_right",
        "margin_x": 5.0,
        "margin_y": 4.0,
        "pos_x": 0.0,
        "pos_y": -42.0,
        "style": default_signature_style(base_style),
    }


def normalize_signature_config(signature, base_style=None):
    config = default_signature_config(base_style)
    if isinstance(signature, dict):
        sig_style = signature.get("style", {})
        config.update({k: v for k, v in signature.items() if k != "style"})
        style = default_signature_style(base_style)
        if isinstance(sig_style, dict):
            style.update(sig_style)
        config["style"] = style
    return config


def default_design_room_state():
    return {
        "version": 1,
        "width": 1080,
        "height": 1920,
        "pages": [
            {
                "id": "page-1",
                "name": "页面 1",
                "duration": 5.0,
                "layers": [],
            }
        ],
    }


def normalize_design_room_state(state):
    data = copy.deepcopy(state) if isinstance(state, dict) else default_design_room_state()
    data.setdefault("version", 1)
    data["width"] = max(1, int(data.get("width", 1080) or 1080))
    data["height"] = max(1, int(data.get("height", 1920) or 1920))
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        pages = default_design_room_state()["pages"]
    clean_pages = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        clean_page = {
            "id": str(page.get("id") or f"page-{i + 1}"),
            "name": str(page.get("name") or f"页面 {i + 1}"),
            "duration": max(0.1, float(page.get("duration", 5.0) or 5.0)),
            "layers": [],
        }
        for j, layer in enumerate(page.get("layers", []) or []):
            if not isinstance(layer, dict):
                continue
            item = copy.deepcopy(layer)
            item["id"] = str(item.get("id") or f"layer-{i + 1}-{j + 1}")
            item["type"] = str(item.get("type") or "text")
            if item["type"] not in {"text", "rect", "image"}:
                item["type"] = "text"
            item["name"] = str(item.get("name") or ("文字" if item["type"] == "text" else "图层"))
            item["x"] = float(item.get("x", 0) or 0)
            item["y"] = float(item.get("y", 0) or 0)
            item["width"] = max(1.0, float(item.get("width", 300) or 300))
            item["height"] = max(1.0, float(item.get("height", 80) or 80))
            item["rotation"] = float(item.get("rotation", 0) or 0)
            item["opacity"] = max(0.0, min(1.0, float(item.get("opacity", 1) or 0)))
            item["start"] = max(0.0, float(item.get("start", 0.0) or 0.0))
            item["end"] = max(0.0, float(item.get("end", 0.0) or 0.0))
            try:
                item["zIndex"] = int(float(item.get("zIndex", j) or 0))
            except Exception:
                item["zIndex"] = j
            if item["type"] == "image":
                item["src"] = str(item.get("src", "") or "")
                item["path"] = str(item.get("path", "") or "")
                item["source_path"] = str(item.get("source_path", "") or "")
                item["original_path"] = str(item.get("original_path", "") or "")
                item["proxy_path"] = str(item.get("proxy_path", "") or "")
                try:
                    item["proxy_max_side"] = int(float(item.get("proxy_max_side", 0) or 0))
                except Exception:
                    item["proxy_max_side"] = 0
                item["fit"] = str(item.get("fit", "cover") or "cover")
            clean_page["layers"].append(item)
        clean_pages.append(clean_page)
    if not clean_pages:
        clean_pages = default_design_room_state()["pages"]
    data["pages"] = clean_pages
    return data


def _file_path_to_url(path):
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith(("file://", "http://", "https://", "data:")):
        return text
    try:
        return Path(text).resolve().as_uri()
    except Exception:
        return text


def design_image_source(layer):
    if not isinstance(layer, dict):
        return ""
    for key in ("proxy_path", "path"):
        candidate = str(layer.get(key, "") or "").strip()
        if candidate and os.path.exists(candidate):
            return _file_path_to_url(candidate)
    for key in ("source_path", "original_path"):
        candidate = str(layer.get(key, "") or "").strip()
        if candidate and os.path.exists(candidate):
            return _file_path_to_url(candidate)
    return str(layer.get("src", "") or "").strip()


def design_frame_times(design_state):
    state = normalize_design_room_state(design_state)
    times = [0.0]
    cursor = 0.0
    for page in state.get("pages", []) or []:
        page_dur = max(0.1, float(page.get("duration", 5.0) or 5.0))
        times.append(cursor)
        for layer in page.get("layers", []) or []:
            if not isinstance(layer, dict):
                continue
            start = max(0.0, float(layer.get("start", 0.0) or 0.0))
            end = float(layer.get("end", 0.0) or 0.0)
            if end <= 0:
                end = page_dur
            times.append(cursor + min(page_dur, start))
            times.append(cursor + min(page_dur, max(start, end)))
        cursor += page_dur
        times.append(cursor)
    return sorted(set(round(t, 3) for t in times if t >= 0.0))


def _normalize_apostrophes(text):
    return str(text or "").replace("’", "'").replace("‘", "'").replace("`", "'")

SCRIPTURE_BOOK_WORDS = (
    "Gen|Genesis|Exod|Exodus|Lev|Leviticus|Num|Numbers|Deut|Deuteronomy|"
    "Josh|Joshua|Judg|Judges|Ruth|Sam|Samuel|Kings|Chron|Chronicles|Ezra|Neh|Nehemiah|"
    "Esth|Esther|Job|Ps|Psalm|Psalms|Prov|Proverbs|Eccl|Ecclesiastes|Song|Isa|Isaiah|"
    "Jer|Jeremiah|Lam|Lamentations|Ezek|Ezekiel|Dan|Daniel|Hos|Hosea|Joel|Amos|Obad|"
    "Jonah|Mic|Micah|Nah|Nahum|Hab|Habakkuk|Zeph|Zephaniah|Hag|Haggai|Zech|Zechariah|"
    "Mal|Malachi|Matt|Matthew|Mark|Luke|John|Jn|Jon|Acts|Rom|Romans|Cor|Corinthians|"
    "Gal|Galatians|Eph|Ephesians|Phil|Philippians|Col|Colossians|Thess|Thessalonians|"
    "Tim|Timothy|Titus|Philem|Philemon|Heb|Hebrews|James|Jas|Peter|Pet|Jude|Rev|Revelation"
)
SCRIPTURE_REF_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"((?:(?:[1-3]\s*)?(?:{SCRIPTURE_BOOK_WORDS})\.?,?\s+)?)"
    rf"(\d{{1,3}})\s*[,，]?\s*[:：]\s*[,，]?\s*(\d{{1,3}}(?:\s*[-–]\s*\d{{1,3}})?)"
    rf"(?!\d)",
    re.IGNORECASE,
)

def normalize_scripture_quote_text(raw_text):
    text = str(raw_text or "")
    if not text:
        return ""
    text = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("„", '"')
        .replace("‟", '"')
        .replace("＂", '"')
    )

    def repl_ref(match):
        book = re.sub(r"\s+", " ", match.group(1) or "").replace(",", "").strip()
        chapter = match.group(2)
        verse = re.sub(r"\s*[-–]\s*", "-", match.group(3))
        prefix = f"{book} " if book else ""
        return f"{prefix}{chapter}:{verse}"

    text = SCRIPTURE_REF_RE.sub(repl_ref, text)
    ref_core = r"(?:(?:(?:[1-3]\s*)?[A-Za-z]+\.?\s+)?\d{1,3}:\d{1,3}(?:-\d{1,3})?)"
    text = re.sub(rf"\b({ref_core})\s*\"", r'\1"', text)
    text = re.sub(rf"\b({ref_core}\")\s+", r"\1", text)
    text = re.sub(r'\s+"', '"', text)

    def wrap_line(match):
        lead, ref, body = match.group(1), match.group(2), match.group(3).strip()
        if not body or body.startswith('"'):
            return match.group(0)
        return f'{lead}{ref}"{body}"'

    text = re.sub(
        rf"(^|\n)\s*({ref_core})(?!\")\s+([^\"\n]+)",
        wrap_line,
        text,
    )
    return re.sub(r"[ \t]+", " ", text).strip()

def _merge_scripture_reference_tokens(tokens):
    merged = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        bare = token.lstrip("\n")
        newline = token[: len(token) - len(bare)]
        if re.fullmatch(r"\d{1,3}:", bare) and i + 1 < len(tokens):
            next_token = tokens[i + 1]
            next_bare = next_token.lstrip("\n")
            if re.fullmatch(r'\d{1,3}(?:-\d{1,3})?"?', next_bare):
                combined = f"{newline}{bare}{next_bare}"
                i += 2
                if combined.endswith('"') and i < len(tokens):
                    after = tokens[i]
                    after_bare = after.lstrip("\n")
                    if after_bare and not after.startswith("\n"):
                        combined += after_bare
                        i += 1
                merged.append(combined)
                continue
        merged.append(token)
        i += 1
    return merged

def format_subtitle_text_spacing(text):
    clean = normalize_scripture_quote_text(text)

    def repl_ref(match):
        verse = re.sub(r"\s*[-–]\s*", "-", match.group(2))
        return f"{match.group(1)}:{verse}"

    clean = re.sub(r"(\d{1,3})\s*[,，]?\s*[:：]\s*[,，]?\s*(\d{1,3}(?:\s*[-–]\s*\d{1,3})?)", repl_ref, clean)
    clean = re.sub(r'(\b\d{1,3}:\d{1,3}(?:-\d{1,3})?")\s+', r"\1", clean)
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r'\s+"', '"', clean)
    clean = re.sub(r'"\s+([,.;:!?])', r'"\1', clean)
    return clean.replace(" \n", "\n").replace("\n ", "\n").strip()

def _visual_text_units(text):
    units = 0.0
    for ch in str(text or ""):
        if ch.isspace():
            units += 0.32
        elif re.match(r"[\u4e00-\u9fff]", ch):
            units += 1.0
        elif re.match(r"[A-Za-z0-9]", ch):
            units += 0.58
        else:
            units += 0.38
    return max(0.2, units)

def tokenize_display_text(raw_text):
    tokens = []
    buf = ""
    pending_newline = False
    raw_text = normalize_scripture_quote_text(raw_text)

    def flush_buf():
        nonlocal buf, pending_newline
        if not buf:
            return
        token = buf
        if pending_newline:
            token = "\n" + token.lstrip()
            pending_newline = False
        tokens.append(token)
        buf = ""

    for ch in _normalize_apostrophes(raw_text).replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            flush_buf()
            pending_newline = True
        elif ch.isspace():
            flush_buf()
        elif re.match(r"[\u4e00-\u9fff]", ch):
            flush_buf()
            token = ch
            if pending_newline:
                token = "\n" + token
                pending_newline = False
            tokens.append(token)
        elif re.match(r"[A-Za-z0-9']", ch):
            buf += ch
        else:
            flush_buf()
            if tokens:
                tokens[-1] += ch
            else:
                token = ch
                if pending_newline:
                    token = "\n" + token
                    pending_newline = False
                tokens.append(token)
    flush_buf()
    return _merge_scripture_reference_tokens([t for t in tokens if t.replace("\n", "").strip()])


def _token_match_key(token):
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff']+", "", _normalize_apostrophes(token).replace("\n", "")).lower()


def _merge_english_suffix_tokens(words):
    merged = []
    for item in words or []:
        word = _normalize_apostrophes(item.get("word", item.get("text", ""))).strip()
        if not word:
            continue
        if not merged:
            fixed = copy.deepcopy(item)
            fixed["word"] = word
            merged.append(fixed)
            continue

        prev = merged[-1]
        prev_word = _normalize_apostrophes(prev.get("word", prev.get("text", ""))).strip()
        suffix_key = word.lower()
        should_merge = (
            suffix_key in ENGLISH_SUFFIX_TOKENS
            or (len(word) <= 3 and word.startswith("'") and re.match(r"^[A-Za-z]+$", word[1:]))
            or (suffix_key in {"s", "m", "re", "ve", "ll", "d", "t"} and prev_word.endswith("'"))
        )
        if should_merge and re.search(r"[A-Za-z]'?$", prev_word):
            fixed_prev = copy.deepcopy(prev)
            if word.startswith("'") or prev_word.endswith("'"):
                suffix_text = word
            else:
                suffix_text = "'" + word
            fixed_prev["word"] = prev_word + suffix_text
            fixed_prev["end"] = max(
                _safe_float(prev.get("end", 0.0), 0.0),
                _safe_float(item.get("end", prev.get("end", 0.0)), _safe_float(prev.get("end", 0.0), 0.0)),
            )
            merged[-1] = fixed_prev
            continue

        fixed = copy.deepcopy(item)
        fixed["word"] = word
        merged.append(fixed)
    return merged


def _distribute_tokens_over_span(tokens, start_time, end_time):
    if not tokens:
        return []
    start_time = float(start_time)
    end_time = max(start_time + 0.01, float(end_time))
    weights = [_visual_text_units(token.replace("\n", "")) for token in tokens]
    total = max(0.01, sum(weights))
    span = end_time - start_time
    aligned = []
    cursor_units = 0.0
    for idx, token in enumerate(tokens):
        token_start = start_time + span * cursor_units / total
        cursor_units += weights[idx]
        token_end = end_time if idx == len(tokens) - 1 else start_time + span * cursor_units / total
        if token_end <= token_start:
            token_end = token_start + 0.02
        aligned.append({"word": token, "start": token_start, "end": token_end})
    return aligned


def _valid_fallback_bounds(fallback_start=None, fallback_end=None):
    if fallback_end is None:
        return None
    start = 0.0 if fallback_start is None else max(0.0, _safe_float(fallback_start, 0.0))
    end = _safe_float(fallback_end, 0.0)
    if end > start + 0.05:
        return start, end
    return None


def _repair_word_timeline(words, fallback_start=None, fallback_end=None):
    if not words:
        return []

    fixed = []
    overlap_count = 0
    duplicate_start_count = 0
    backwards_count = 0
    zero_duration_count = 0
    prev_start = None
    prev_end = None

    for item in words:
        start = max(0.0, _safe_float(item.get("start"), prev_end if prev_end is not None else 0.0))
        end = _safe_float(item.get("end"), start + MIN_WORD_DURATION_SECONDS)
        if end <= start:
            zero_duration_count += 1
            end = start + MIN_WORD_DURATION_SECONDS
        if prev_start is not None:
            if start < prev_start - 0.01:
                backwards_count += 1
            if prev_end is not None and start < prev_end - 0.01:
                overlap_count += 1
            if abs(start - prev_start) < 0.01:
                duplicate_start_count += 1
        fixed_item = copy.deepcopy(item)
        fixed_item["start"] = start
        fixed_item["end"] = end
        fixed.append(fixed_item)
        prev_start = start
        prev_end = end

    observed_start = fixed[0]["start"]
    observed_end = max(fixed[-1]["end"], max(item["end"] for item in fixed))
    observed_span = max(0.0, observed_end - observed_start)
    issue_count = overlap_count + duplicate_start_count + backwards_count
    compressed = len(fixed) >= 6 and (
        observed_span < len(fixed) * 0.055
        or (zero_duration_count >= len(fixed) * 0.5 and observed_span < len(fixed) * 0.12)
    )
    crowded = len(fixed) >= 4 and issue_count >= max(2, int(len(fixed) * 0.25))
    fallback_bounds = _valid_fallback_bounds(fallback_start, fallback_end)

    if compressed or crowded:
        span_start = observed_start
        span_end = max(observed_end, observed_start + len(fixed) * 0.12)
        if fallback_bounds:
            fb_start, fb_end = fallback_bounds
            fallback_span = fb_end - fb_start
            if fallback_span > max(span_end - span_start, len(fixed) * 0.08):
                span_start, span_end = fb_start, fb_end
        return _normalize_aligned_word_times(
            _distribute_tokens_over_span([item.get("word", "") for item in fixed], span_start, span_end),
            span_start,
            span_end,
        )

    repaired = []
    cursor = fixed[0]["start"]
    for item in fixed:
        start = max(cursor, _safe_float(item.get("start"), cursor))
        end = max(_safe_float(item.get("end"), start + MIN_WORD_DURATION_SECONDS), start + MIN_WORD_DURATION_SECONDS)
        repaired_item = copy.deepcopy(item)
        repaired_item["start"] = start
        repaired_item["end"] = end
        repaired.append(repaired_item)
        cursor = end
    return repaired


def _normalize_aligned_word_times(aligned, total_start=None, total_end=None):
    if not aligned:
        return []
    total_start = _safe_float(total_start if total_start is not None else aligned[0].get("start", 0.0), 0.0)
    total_end = _safe_float(total_end if total_end is not None else aligned[-1].get("end", total_start + 1.0), total_start + 1.0)
    span = max(0.01, total_end - total_start)
    min_dur = min(0.06, max(0.012, span / max(1, len(aligned)) * 0.22))
    cursor = total_start
    normalized = []
    for idx, item in enumerate(aligned):
        remaining = len(aligned) - idx - 1
        latest_start = max(total_start, total_end - max(0.0, remaining + 1) * min_dur)
        raw_start = _safe_float(item.get("start", cursor), cursor)
        raw_end = _safe_float(item.get("end", raw_start + min_dur), raw_start + min_dur)
        start = max(cursor, min(raw_start, latest_start))
        end = max(raw_end, start + min_dur)
        if remaining > 0:
            end = min(end, total_end - remaining * min_dur)
            if end <= start:
                end = start + min_dur
        normalized.append({"word": item.get("word", ""), "start": start, "end": end})
        cursor = end
    return normalized


def normalize_word_timestamps(words, text_key="word", fallback_start=None, fallback_end=None):
    normalized = []
    for word in words or []:
        raw_text = _normalize_apostrophes(word.get(text_key) or word.get("word") or word.get("text") or "").strip()
        if not raw_text:
            continue
        pieces = tokenize_display_text(raw_text)
        if not pieces:
            continue
        start = max(0.0, _safe_float(word.get("start", 0.0), 0.0))
        end = _safe_float(word.get("end", start + MIN_WORD_DURATION_SECONDS), start + MIN_WORD_DURATION_SECONDS)
        if end <= start:
            end = start + MIN_WORD_DURATION_SECONDS
        if len(pieces) == 1:
            normalized.append({"word": pieces[0], "start": start, "end": end})
            continue
        weights = [_visual_text_units(piece.replace("\n", "")) for piece in pieces]
        total = max(0.01, sum(weights))
        cursor = start
        dur = end - start
        for idx, piece in enumerate(pieces):
            part_dur = dur * weights[idx] / total
            part_end = end if idx == len(pieces) - 1 else min(end, cursor + max(0.01, part_dur))
            normalized.append({"word": piece, "start": cursor, "end": max(cursor + 0.01, part_end)})
            cursor = part_end
    return _repair_word_timeline(_merge_english_suffix_tokens(normalized), fallback_start, fallback_end)

def align_reference_text_to_timestamps(ai_words, raw_text, fallback_start=None, fallback_end=None):
    raw_text = normalize_scripture_quote_text(raw_text)
    user_tokens = tokenize_display_text(raw_text)
    ai_words = normalize_word_timestamps(ai_words or [], fallback_start=fallback_start, fallback_end=fallback_end)
    if not ai_words or not user_tokens:
        return ai_words

    total_start = float(ai_words[0].get("start", 0.0))
    total_end = float(ai_words[-1].get("end", total_start + 1.0))
    if total_end <= total_start:
        total_end = total_start + max(1.0, len(user_tokens) * 0.18)

    user_keys = [_token_match_key(token) for token in user_tokens]
    ai_keys = [_token_match_key(w.get("word", "")) for w in ai_words]
    matcher = SequenceMatcher(None, user_keys, ai_keys, autojunk=False)
    pairs = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            u_idx = block.a + offset
            a_idx = block.b + offset
            if u_idx < len(user_keys) and a_idx < len(ai_keys) and user_keys[u_idx] and user_keys[u_idx] == ai_keys[a_idx]:
                pairs.append((u_idx, a_idx))

    # If Whisper and the pasted script barely overlap, keep every pasted word and
    # distribute it over the detected audio duration instead of trusting bad anchors.
    if len(pairs) < max(3, int(len(user_tokens) * 0.18)):
        return _normalize_aligned_word_times(
            _distribute_tokens_over_span(user_tokens, total_start, total_end),
            total_start,
            total_end,
        )

    aligned = [None] * len(user_tokens)
    cursor_user = 0
    cursor_time = total_start

    for u_idx, a_idx in pairs:
        anchor_start = float(ai_words[a_idx].get("start", cursor_time))
        anchor_end = float(ai_words[a_idx].get("end", anchor_start + 0.05))
        if anchor_end <= anchor_start:
            anchor_end = anchor_start + 0.05

        if u_idx > cursor_user:
            gap_tokens = user_tokens[cursor_user:u_idx]
            gap_start = cursor_time
            gap_end = max(gap_start + 0.01, anchor_start)
            gap_aligned = _distribute_tokens_over_span(gap_tokens, gap_start, gap_end)
            for offset, item in enumerate(gap_aligned):
                aligned[cursor_user + offset] = item

        aligned[u_idx] = {"word": user_tokens[u_idx], "start": anchor_start, "end": anchor_end}
        cursor_user = u_idx + 1
        cursor_time = max(cursor_time, anchor_end)

    if cursor_user < len(user_tokens):
        gap_aligned = _distribute_tokens_over_span(user_tokens[cursor_user:], cursor_time, max(cursor_time + 0.01, total_end))
        for offset, item in enumerate(gap_aligned):
            aligned[cursor_user + offset] = item

    # Fill any holes caused by repeated words or skipped anchors.
    for idx, item in enumerate(aligned):
        if item is None:
            prev_time = aligned[idx - 1]["end"] if idx > 0 and aligned[idx - 1] else total_start
            next_time = total_end
            for later in aligned[idx + 1:]:
                if later is not None:
                    next_time = later["start"]
                    break
            aligned[idx] = _distribute_tokens_over_span([user_tokens[idx]], prev_time, max(prev_time + 0.01, next_time))[0]

    return _normalize_aligned_word_times(aligned, total_start, total_end)

def _clean_word_text(word):
    return str(word.get("text", word.get("word", ""))).replace("\n", "").strip()

def _subtitle_plain_text(words):
    parts = []
    for word in words:
        raw = str(word.get("text", word.get("word", ""))).strip()
        if raw:
            parts.append(raw)
    return " ".join(parts).replace(" \n", "\n").replace("\n ", "\n")

def _subtitle_word_count(subtitle):
    words = subtitle.get("words", []) if isinstance(subtitle, dict) else []
    if words:
        return len([w for w in words if _clean_word_text(w)])
    return len(str(subtitle.get("text", "") if isinstance(subtitle, dict) else "").split())

def _merge_subtitle_segments(left, right):
    merged = copy.deepcopy(left)
    left_words = [copy.deepcopy(w) for w in (left.get("words", []) or []) if _clean_word_text(w)]
    right_words = [copy.deepcopy(w) for w in (right.get("words", []) or []) if _clean_word_text(w)]
    if not left_words:
        left_words = [{"text": left.get("text", ""), "start": left.get("start", 0.0), "end": left.get("end", 0.0)}]
    if not right_words:
        right_words = [{"text": right.get("text", ""), "start": right.get("start", left.get("end", 0.0)), "end": right.get("end", left.get("end", 0.0))}]
    merged_words = left_words + right_words
    merged["words"] = merged_words
    merged["text"] = format_subtitle_text_spacing(_subtitle_plain_text(merged_words))
    merged["start"] = min(_safe_float(left.get("start", 0.0), 0.0), _safe_float(right.get("start", 0.0), 0.0))
    merged["end"] = max(_safe_float(left.get("end", merged["start"] + 0.05), merged["start"] + 0.05), _safe_float(right.get("end", merged["start"] + 0.05), merged["start"] + 0.05))
    merged["track"] = left.get("track", right.get("track", 1))
    return merged

def merge_single_word_subtitle_segments(subtitles, *, max_merged_words=14):
    items = [copy.deepcopy(s) for s in (subtitles or []) if isinstance(s, dict)]
    if len(items) <= 1:
        return items
    result = []
    idx = 0
    while idx < len(items):
        current = items[idx]
        if _subtitle_word_count(current) > 1:
            result.append(current)
            idx += 1
            continue

        current_track = int(current.get("track", 1))
        if result and int(result[-1].get("track", 1)) == current_track and _subtitle_word_count(result[-1]) < max_merged_words:
            result[-1] = _merge_subtitle_segments(result[-1], current)
            idx += 1
            continue

        if idx + 1 < len(items) and int(items[idx + 1].get("track", 1)) == current_track:
            merged = _merge_subtitle_segments(current, items[idx + 1])
            if _subtitle_word_count(merged) <= max_merged_words:
                result.append(merged)
                idx += 2
                continue

        result.append(current)
        idx += 1
    return result

def _style_display_text(text, style):
    clean = str(text or "")
    trans = (style or {}).get("text_transform", "capitalize")
    if trans == "uppercase":
        return clean.upper()
    if trans == "lowercase":
        return clean.lower()
    if trans == "capitalize":
        return " ".join(word[0].upper() + word[1:] if word else "" for word in clean.split(" "))

    sub_words = clean.split(" ")
    for s_idx, sub_w in enumerate(sub_words):
        letters = re.sub(r"[^a-zA-Z]", "", sub_w)
        pure_w = letters.lower()
        if letters and pure_w in FAITH_WORDS:
            sub_words[s_idx] = sub_w.replace(letters, pure_w.capitalize(), 1)
    return " ".join(sub_words)

def _css_font_stack(family):
    return css_font_stack(family)

def _apply_balanced_breaks(words, line_capacity, max_lines, style=None):
    cleaned = []
    for word in words:
        item = copy.deepcopy(word)
        item["text"] = _clean_word_text(item)
        cleaned.append(item)
    if max_lines <= 1 or len(cleaned) <= 1:
        return cleaned

    def measure_units(word):
        return _visual_text_units(_style_display_text(_clean_word_text(word), style))

    total_units = sum(measure_units(w) + 0.32 for w in cleaned)
    if total_units <= line_capacity * 1.05:
        return cleaned

    lines = [[]]
    line_units = 0.0
    for word in cleaned:
        word_units = measure_units(word) + (0.32 if lines[-1] else 0.0)
        if lines[-1] and line_units + word_units > line_capacity and len(lines) < max_lines:
            lines.append([])
            line_units = 0.0
            word_units = measure_units(word)
        lines[-1].append(word)
        line_units += word_units

    rebuilt = []
    for line_idx, line in enumerate(lines):
        for word_idx, word in enumerate(line):
            item = copy.deepcopy(word)
            if line_idx > 0 and word_idx == 0:
                item["text"] = "\n" + _clean_word_text(item).lstrip()
            rebuilt.append(item)
    return rebuilt


def merge_short_subtitle_segments(subtitles, *, min_words=4, max_merged_words=7):
    items = [copy.deepcopy(s) for s in (subtitles or []) if isinstance(s, dict)]
    if len(items) <= 1:
        return items
    min_words = max(1, int(min_words or 1))
    max_merged_words = max(min_words, int(max_merged_words or min_words))

    changed = True
    while changed:
        changed = False
        result = []
        idx = 0
        while idx < len(items):
            current = items[idx]
            current_words = _subtitle_word_count(current)
            if current_words >= min_words:
                result.append(current)
                idx += 1
                continue

            if idx + 1 < len(items) and int(current.get("track", 1)) == int(items[idx + 1].get("track", 1)):
                combined_words = current_words + _subtitle_word_count(items[idx + 1])
                if combined_words <= max_merged_words:
                    result.append(_merge_subtitle_segments(current, items[idx + 1]))
                    idx += 2
                    changed = True
                    continue

            if result and int(result[-1].get("track", 1)) == int(current.get("track", 1)):
                combined_words = _subtitle_word_count(result[-1]) + current_words
                if combined_words <= max_merged_words:
                    result[-1] = _merge_subtitle_segments(result[-1], current)
                    idx += 1
                    changed = True
                    continue

            result.append(current)
            idx += 1
        items = result
    return items


def _readable_subtitle_duration(subtitle, min_seconds=FAST_SUBTITLE_READABLE_MIN_SECONDS, min_word_seconds=FAST_WORD_VISUAL_MIN_SECONDS):
    word_count = max(1, _subtitle_word_count(subtitle))
    return max(float(min_seconds), min(1.15, word_count * float(min_word_seconds) + 0.16))


def protect_fast_subtitle_pacing(
    subtitles,
    *,
    min_seconds=FAST_SUBTITLE_READABLE_MIN_SECONDS,
    min_word_seconds=FAST_WORD_VISUAL_MIN_SECONDS,
    max_merged_words=8,
    gap_tolerance=0.18,
    allow_merge=True,
):
    items = [copy.deepcopy(s) for s in (subtitles or []) if isinstance(s, dict)]
    if not items:
        return []

    result = []
    idx = 0
    while idx < len(items):
        current = items[idx]
        if allow_merge:
            while idx + 1 < len(items):
                next_item = items[idx + 1]
                if int(current.get("track", 1)) != int(next_item.get("track", 1)):
                    break
                combined_words = _subtitle_word_count(current) + _subtitle_word_count(next_item)
                if combined_words > max_merged_words:
                    break
                start = _safe_float(current.get("start", 0.0), 0.0)
                end = _safe_float(current.get("end", start), start)
                next_start = _safe_float(next_item.get("start", end), end)
                gap = next_start - end
                if gap > gap_tolerance:
                    break
                desired = _readable_subtitle_duration(current, min_seconds, min_word_seconds)
                if end - start >= desired and gap >= 0.04:
                    break
                current = _merge_subtitle_segments(current, next_item)
                idx += 1
        result.append(current)
        idx += 1

    for pos, sub in enumerate(result):
        start = _safe_float(sub.get("start", 0.0), 0.0)
        end = _safe_float(sub.get("end", start + MIN_SUBTITLE_DURATION_SECONDS), start + MIN_SUBTITLE_DURATION_SECONDS)
        desired_end = start + _readable_subtitle_duration(sub, min_seconds, min_word_seconds)
        next_start = None
        for later in result[pos + 1:]:
            if int(later.get("track", 1)) == int(sub.get("track", 1)):
                next_start = _safe_float(later.get("start", end), end)
                break
        if next_start is not None:
            end = min(max(end, desired_end), max(start + MIN_SUBTITLE_DURATION_SECONDS, next_start - 0.01))
        else:
            end = max(end, desired_end)
        sub["start"] = start
        sub["end"] = max(start + MIN_SUBTITLE_DURATION_SECONDS, end)
    return result

def subtitle_layout_capacity(style, proj_w=1080):
    style = style or {}
    size = max(12.0, float(style.get("size", 100)))
    width_pct = float(style.get("box_width", 0) or 0)
    if width_pct <= 0:
        width_pct = 74.0
    width_pct = max(28.0, min(120.0, width_pct))
    max_lines = max(1, min(5, int(style.get("max_lines", 2) or 2)))
    line_capacity = max(3.5, (float(proj_w) * width_pct / 100.0) / size * 0.92)
    layout_mode = style.get("layout_mode", "standard")
    if layout_mode == "split_screen":
        layout_mode = "standard"
    if layout_mode == "contrast":
        try:
            emphasis_scale = max(100.0, float(style.get("emphasis_scale", 145) or 145)) / 100.0
        except Exception:
            emphasis_scale = 1.45
        try:
            small_scale = max(0.58, min(1.0, float(style.get("contrast_small_scale", 0.74) or 0.74)))
        except Exception:
            small_scale = 0.74
        contrast_guard = 1.0 + max(0.0, emphasis_scale - 1.0) * 0.42 + max(0.0, 1.0 - small_scale) * 0.16
        line_capacity = max(3.5, line_capacity / min(1.32, contrast_guard))
    return line_capacity, max_lines, max(4.0, line_capacity * max_lines)

def rebalance_subtitle_layout(subs, fallback_style=None, default_pos=(0.0, 25.0), proj_w=1080, min_gap=0.01, force_standard_box=False, allow_split=True):
    balanced = []
    stats = {"before": len(subs or []), "after": 0, "split": 0, "overlaps_fixed": 0}
    fallback_style = fallback_style or {}

    for sub in subs or []:
        base = copy.deepcopy(sub)
        style = copy.deepcopy(fallback_style)
        style.update(copy.deepcopy(base.get("style", {})))
        layout_mode = style.get("layout_mode", "standard")
        if layout_mode == "split_screen":
            layout_mode = "standard"
        is_standard = layout_mode == "standard"
        is_reflowable = layout_mode in ("standard", "contrast")
        if force_standard_box and is_standard:
            style["box_layout"] = "fixed"
            if float(style.get("box_width", 0) or 0) <= 0:
                style["box_width"] = 74.0
            style["max_lines"] = max(1, min(4, int(style.get("max_lines", 2) or 2)))
        elif force_standard_box and is_reflowable:
            if float(style.get("box_width", 0) or 0) <= 0:
                style["box_width"] = 74.0
            style["max_lines"] = max(1, min(4, int(style.get("max_lines", 2) or 2)))

        words = base.get("words", [])
        if not words:
            words = [{"text": base.get("text", ""), "start": base.get("start", 0.0), "end": base.get("end", 1.0)}]
        words = normalize_word_timestamps(words, text_key="text")
        words = [copy.deepcopy(w) for w in words if _clean_word_text(w)]
        if not words:
            balanced.append(base)
            continue

        if not is_reflowable:
            base["style"] = style
            base["text"] = _subtitle_plain_text(words)
            base["words"] = words
            balanced.append(base)
            continue

        line_capacity, max_lines, capacity = subtitle_layout_capacity(style, proj_w)
        chunks = []
        current = []
        current_units = 0.0
        for word in words:
            clean = _clean_word_text(word)
            display_clean = _style_display_text(clean, style)
            word_units = _visual_text_units(display_clean) + (0.32 if current else 0.0)
            if allow_split and current and current_units + word_units > capacity:
                chunks.append(current)
                current = []
                current_units = 0.0
                word_units = _visual_text_units(display_clean)
            item = copy.deepcopy(word)
            item["text"] = clean
            current.append(item)
            current_units += word_units
        if current:
            chunks.append(current)

        if len(chunks) > 1:
            stats["split"] += len(chunks) - 1

        for chunk in chunks:
            chunk_words = _apply_balanced_breaks(chunk, line_capacity, max_lines, style)
            new_sub = copy.deepcopy(base)
            new_sub["style"] = copy.deepcopy(style)
            new_sub["words"] = chunk_words
            new_sub["text"] = _subtitle_plain_text(chunk_words)
            new_sub["start"] = float(chunk_words[0].get("start", base.get("start", 0.0)))
            new_sub["end"] = float(chunk_words[-1].get("end", max(new_sub["start"] + 0.05, base.get("end", 1.0))))
            if new_sub["end"] <= new_sub["start"]:
                new_sub["end"] = new_sub["start"] + 0.05
            new_sub["pos_x"] = float(new_sub.get("pos_x", default_pos[0]))
            new_sub["pos_y"] = float(new_sub.get("pos_y", default_pos[1]))
            new_sub["track"] = new_sub.get("track", 1)
            balanced.append(new_sub)

    def shift_sub_words(sub, delta):
        if abs(delta) < 0.000001:
            return
        for word in sub.get("words", []) or []:
            old_start = _safe_float(word.get("start", 0.0), 0.0)
            old_end = _safe_float(word.get("end", old_start + MIN_WORD_DURATION_SECONDS), old_start + MIN_WORD_DURATION_SECONDS)
            word["start"] = max(0.0, old_start + delta)
            word["end"] = max(word["start"] + MIN_WORD_DURATION_SECONDS, old_end + delta)

    balanced.sort(key=lambda s: (int(s.get("track", 1)), _safe_float(s.get("start", 0.0), 0.0), _safe_float(s.get("end", 1.0), 1.0)))
    last_by_track = {}
    for sub in balanced:
        track = int(sub.get("track", 1))
        start = max(0.0, _safe_float(sub.get("start", 0.0), 0.0))
        end = _safe_float(sub.get("end", start + MIN_SUBTITLE_DURATION_SECONDS), start + MIN_SUBTITLE_DURATION_SECONDS)
        if end < start + MIN_SUBTITLE_DURATION_SECONDS:
            end = start + MIN_SUBTITLE_DURATION_SECONDS
        sub["start"] = start
        sub["end"] = end
        prev = last_by_track.get(track)
        if prev is not None and _safe_float(prev.get("end", 0.0), 0.0) > start - min_gap:
            prev_start = _safe_float(prev.get("start", 0.0), 0.0)
            prev_end = _safe_float(prev.get("end", prev_start), prev_start)
            trimmed_prev_end = start - min_gap
            if trimmed_prev_end >= prev_start + MIN_SUBTITLE_DURATION_SECONDS:
                prev["end"] = trimmed_prev_end
            else:
                new_start = prev_end + min_gap
                delta = new_start - start
                start = new_start
                end = max(start + MIN_SUBTITLE_DURATION_SECONDS, end + delta)
                sub["start"] = start
                sub["end"] = end
                shift_sub_words(sub, delta)
            stats["overlaps_fixed"] += 1
        last_by_track[track] = sub

    balanced.sort(key=lambda s: (_safe_float(s.get("start", 0.0), 0.0), int(s.get("track", 1)), _safe_float(s.get("end", 1.0), 1.0)))
    stats["after"] = len(balanced)
    return balanced, stats

def hex_to_rgb(hex_color):
    hex_color = str(hex_color).lstrip('#')
    if len(hex_color) == 6:
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (255, 255, 255)


DEFAULT_RANDOM_TEXT_COLORS = (
    "#FF3B30",
    "#FF9F0A",
    "#FFD60A",
    "#32D74B",
    "#00E5FF",
    "#4D96FF",
    "#FF4FD8",
)


def _normalize_render_hex_color(value, fallback=None):
    raw = str(value or "").strip()
    if raw and not raw.startswith("#"):
        raw = f"#{raw}"
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return raw.upper()
    return fallback


def normalize_random_text_palette(palette):
    if palette is None:
        raw_items = [{"color": color, "enabled": True} for color in DEFAULT_RANDOM_TEXT_COLORS]
    elif isinstance(palette, str):
        raw_items = [item.strip() for item in palette.split(",") if item.strip()]
    elif isinstance(palette, dict):
        raw_items = palette.get("colors") or palette.get("palette") or []
    else:
        try:
            raw_items = list(palette)
        except TypeError:
            raw_items = []

    normalized = []
    for item in raw_items:
        enabled = True
        color_value = item
        if isinstance(item, dict):
            color_value = item.get("color") or item.get("value") or item.get("hex")
            enabled = bool(item.get("enabled", True))
        color = _normalize_render_hex_color(color_value)
        if color:
            normalized.append({"color": color, "enabled": enabled})
    return normalized


def stable_random_text_color(palette, word_idx, word_text, subtitle_text="", fallback="#FFFFFF"):
    colors = [item["color"] for item in normalize_random_text_palette(palette) if item.get("enabled")]
    if not colors:
        return _normalize_render_hex_color(fallback, "#FFFFFF") or "#FFFFFF"
    seed = f"{subtitle_text}|{word_idx}|{word_text}"
    score = 0
    for pos, char in enumerate(seed):
        score = (score * 131 + ord(char) + pos + 17) % 2147483647
    return colors[score % len(colors)]



DEFAULT_RANDOM_TEXT_FONTS = (
    "Noto Sans SC",
    "Inter",
    "Montserrat",
    "Poppins",
    "Oswald",
    "Playfair Display",
    "Bebas Neue",
)


def normalize_random_text_font_pool(pool):
    if pool is None:
        raw_items = [{"font": font, "enabled": True} for font in DEFAULT_RANDOM_TEXT_FONTS]
    elif isinstance(pool, str):
        raw_items = [item.strip() for item in pool.split(",") if item.strip()]
    elif isinstance(pool, dict):
        raw_items = pool.get("fonts") or pool.get("pool") or []
    else:
        try:
            raw_items = list(pool)
        except TypeError:
            raw_items = []

    normalized = []
    seen = set()
    for item in raw_items:
        enabled = True
        font_value = item
        if isinstance(item, dict):
            font_value = item.get("font") or item.get("family") or item.get("name")
            enabled = bool(item.get("enabled", True))
        font_name = str(font_value or "").strip()
        if not font_name:
            continue
        key = font_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"font": font_name, "enabled": enabled})
    return normalized


def stable_random_text_font(pool, word_idx, word_text, subtitle_text="", fallback="Arial", strategy="rotate"):
    fonts = [item["font"] for item in normalize_random_text_font_pool(pool) if item.get("enabled")]
    if not fonts:
        return str(fallback or "Arial").strip() or "Arial"
    strategy = str(strategy or "rotate").strip().lower()
    if strategy in ("rotate", "cycle", "alternate", "headline_rotate"):
        return fonts[int(word_idx or 0) % len(fonts)]
    seed = f"{subtitle_text}|{word_idx}|{word_text}"
    score = 0
    for pos, char in enumerate(seed):
        score = (score * 131 + ord(char) + pos + 17) % 2147483647
    return fonts[score % len(fonts)]

def get_exact_duration(file_path):
    return media_probe.get_exact_duration(file_path)

def get_stream_duration(file_path, stream_selector="v:0"):
    return media_probe.get_stream_duration(file_path, stream_selector)

def get_video_stream_duration(file_path):
    return get_stream_duration(file_path, "v:0")

def get_audio_stream_duration(file_path):
    return get_stream_duration(file_path, "a:0")

def get_timeline_media_duration(file_path, precise=False):
    return media_probe.get_timeline_media_duration(file_path, precise=precise)

def get_video_import_metadata(file_path):
    return media_probe.get_video_import_metadata(file_path)

def _parse_rate(value):
    return media_probe.parse_rate(value)

def _estimate_video_packet_duration(file_path):
    return media_probe.estimate_video_packet_duration(file_path)

def get_video_dimensions(file_path):
    return media_probe.get_video_dimensions(file_path)

class AspectRatioContainer(QWidget):
    def __init__(self, child_widget, parent=None):
        super().__init__(parent)
        self.child_widget = child_widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(child_widget, alignment=Qt.AlignmentFlag.AlignCenter)
        self.ratio = 1080 / 1920

    def set_ratio(self, w, h):
        if h == 0: return
        self.ratio = w / h
        self.updateGeometry()
        if self.parentWidget():
            self.parentWidget().update()

    def resizeEvent(self, event):
        w = event.size().width()
        h = event.size().height()
        if h > 0 and (w / h) > self.ratio:
            new_w = int(h * self.ratio)
            self.child_widget.setFixedSize(new_w, h)
        else:
            new_h = int(w / self.ratio) if self.ratio > 0 else h
            self.child_widget.setFixedSize(w, new_h)
        super().resizeEvent(event)

class WebBridge(QObject):
    def __init__(self, parent_controller):
        super().__init__()
        self.controller = parent_controller

    @pyqtSlot(int, float, float)
    def update_coordinates(self, idx, x, y):
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                c["pos_x"] = x; c["pos_y"] = y

            if self.controller.current_selected_idx == idx:
                self.controller.pos_x_spin.blockSignals(True); self.controller.pos_x_slider.blockSignals(True)
                self.controller.pos_y_spin.blockSignals(True); self.controller.pos_y_slider.blockSignals(True)

                self.controller.pos_x_spin.setValue(float(x)); self.controller.pos_x_slider.setValue(int(float(x) * 100))
                self.controller.pos_y_spin.setValue(float(y)); self.controller.pos_y_slider.setValue(int(float(y) * 100))

                self.controller.pos_x_spin.blockSignals(False); self.controller.pos_x_slider.blockSignals(False)
                self.controller.pos_y_spin.blockSignals(False); self.controller.pos_y_slider.blockSignals(False)

            self.controller.update_floating_subtitle()
            self.controller.auto_save_cache()

    @pyqtSlot(int, float)
    def update_box_width(self, idx, width):
        width = max(0.0, min(120.0, float(width or 0.0)))
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                if "style" not in c: c["style"] = self.controller.default_style.copy()
                c["style"]["box_width"] = width

            if self.controller.current_selected_idx == idx:
                self.controller.box_width_spin.blockSignals(True); self.controller.box_width_slider.blockSignals(True)
                self.controller.box_width_spin.setValue(float(width)); self.controller.box_width_slider.setValue(int(float(width) * 100))
                self.controller.box_width_spin.blockSignals(False); self.controller.box_width_slider.blockSignals(False)

            self.controller.update_floating_subtitle()
            self.controller.auto_save_cache()

    @pyqtSlot(int)
    def notify_selected(self, idx):
        self.controller.current_selected_idx = idx
        self.controller.switch_inspector("sub")

    @pyqtSlot(int, str)
    def update_text_from_screen(self, idx, new_text):
        pass

    @pyqtSlot(int, int)
    def adjust_font_size(self, idx, delta):
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            st = current_clip.get("style", current_clip)
            new_size = max(10, min(300, st.get("size", 100) + delta))

            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                if "style" not in c: c["style"] = {}
                c["style"]["size"] = new_size
            if self.controller.current_selected_idx == idx:
                self.controller.size_slider.blockSignals(True); self.controller.size_spin.blockSignals(True)
                self.controller.size_slider.setValue(new_size); self.controller.size_spin.setValue(new_size)
                self.controller.size_slider.blockSignals(False); self.controller.size_spin.blockSignals(False)
            self.controller.update_floating_subtitle(); self.controller.auto_save_cache()




def _scene_light_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
    return bool(value)


def _scene_light_word_items(sub, clip_start, clip_end):
    words = sub.get("words", []) if isinstance(sub, dict) else []
    if not words:
        text = str((sub or {}).get("text", "") if isinstance(sub, dict) else "").strip()
        return [{"text": text, "start": clip_start, "end": clip_end}] if text else []
    items = []
    for word in words:
        label = _clean_word_text(word)
        if not label:
            continue
        start = max(clip_start, min(clip_end, _safe_float(word.get("start", clip_start), clip_start)))
        end = max(start + 0.02, min(clip_end, _safe_float(word.get("end", start + 0.16), start + 0.16)))
        items.append({"text": label, "start": start, "end": end})
    return items


def _scene_light_pulse_for_sub(sub, current_time):
    if not isinstance(sub, dict):
        return 0.0
    style = sub.get("style", sub)
    if not isinstance(style, dict) or not _scene_light_bool(style.get("scene_light_enable", False)):
        return 0.0
    current_time = _safe_float(current_time, 0.0)
    clip_start = _safe_float(sub.get("start", current_time), current_time)
    clip_end = _safe_float(sub.get("end", clip_start + 1.0), clip_start + 1.0)
    if current_time < clip_start - 0.001 or current_time > clip_end + 0.001:
        return 0.0
    trigger = str(style.get("scene_light_trigger", "word") or "word").strip().lower()
    decay = max(0.05, min(1.20, _safe_float(style.get("scene_light_decay", 0.30), 0.30)))
    hold = max(0.0, min(0.35, _safe_float(style.get("scene_light_hold", 0.02), 0.02)))
    pulse = 0.0
    for word in _scene_light_word_items(sub, clip_start, clip_end):
        starts = [word["start"]]
        if trigger in {"char", "character", "letter"}:
            clean = re.sub(r"\s+", "", str(word.get("text", "")))
            char_count = max(1, min(24, len(clean)))
            word_span = max(0.04, min(max(0.04, word["end"] - word["start"]), decay * 1.6))
            interval = max(0.035, word_span / char_count)
            starts = [word["start"] + i * interval for i in range(char_count)]
        for start in starts:
            age = current_time - start
            if age < -0.002 or age > decay + hold:
                continue
            if age <= hold:
                local = 1.0
            else:
                p = max(0.0, min(1.0, (age - hold) / decay))
                local = (1.0 - p) ** 2.15
            if local > pulse:
                pulse = local
    return max(0.0, min(1.0, pulse))


def render_scene_light_html(active_subs, current_time, proj_w=1080, proj_h=1920):
    """Render the reference-style dark scene light effect.

    The layer keeps a persistent dark mask over the video, then cuts soft holes
    through that mask using only words that are actually visible at this time.
    Word positions are estimated from the full subtitle layout so hidden future
    words can still reserve space without creating ghost text in the light mask.
    """
    canvas_w = float(proj_w or 1080)
    canvas_h = float(proj_h or 1920)
    dim_alpha = 0.0
    mask_rgb = (0, 0, 0)
    mask_color = "#000000"
    mask_items = []
    spot_items = []
    bloom_items = []
    ambient_lift_alpha = 0.0

    def _reveal_mode(style):
        mode = str(style.get("text_reveal_mode", "all") or "all").strip().lower()
        mapped = {
            "word": "word_voice",
            "voice_word": "word_voice",
            "word_voice": "word_voice",
            "line": "line_voice",
            "voice_line": "line_voice",
            "line_voice": "line_voice",
            "none": "all",
        }.get(mode, mode if mode in {"all", "word_voice", "line_voice"} else "all")
        # Scene light is an "appearing word lights the scene" effect.  Even when
        # the subtitle display mode is all-at-once, old projects can still hide
        # future words via word timestamps/inactive alpha, so the light must not
        # pre-cut holes for words that have not reached their audio time yet.
        if mapped == "all":
            return "word_voice"
        return mapped

    def _text_width_px(label, size_px, letter_spacing_px=0.0):
        clean = str(label or "")
        if not clean:
            return 0.0
        spacing = max(-24.0, min(60.0, _safe_float(letter_spacing_px, 0.0))) * max(0, len(clean) - 1)
        return max(size_px * 0.24, _visual_text_units(clean) * size_px * 0.96 + spacing)

    def _layout_scene_words(sub, style, clip_start, clip_end, size_px):
        words = _scene_light_word_items(sub, clip_start, clip_end)
        if not words:
            raw = str(sub.get("text", "") or "").strip()
            if raw:
                span = max(0.05, clip_end - clip_start)
                tokens = [part for part in re.split(r"\s+", raw) if part]
                if tokens:
                    step = span / max(1, len(tokens))
                    words = [
                        {"text": token, "start": clip_start + i * step, "end": min(clip_end, clip_start + (i + 1) * step)}
                        for i, token in enumerate(tokens)
                    ]
        if not words:
            return []

        max_lines = max(1, min(6, int(style.get("max_lines", 2) or 2)))
        width_pct = _safe_float(style.get("box_width", 0), 0)
        if width_pct <= 0:
            width_pct = 74.0
        width_pct = max(24.0, min(120.0, width_pct))
        line_capacity = max(3.5, (canvas_w * width_pct / 100.0) / max(8.0, size_px) * 0.92)
        arranged = words
        if len(words) > 1 and max_lines > 1:
            try:
                arranged = _apply_balanced_breaks(words, line_capacity, max_lines, style)
            except Exception:
                arranged = words

        rows = [[]]
        for item in arranged:
            raw = str(item.get("text") or item.get("word") or "")
            if "\n" in raw and rows[-1]:
                rows.append([])
            clean = raw.replace("\n", "").strip()
            if not clean:
                continue
            display = _style_display_text(clean, style)
            rows[-1].append({
                "text": display,
                "start": _safe_float(item.get("start", clip_start), clip_start),
                "end": _safe_float(item.get("end", clip_end), clip_end),
            })
        return [row for row in rows if row]

    def _svg_word_group(label, x, y, size_px, anchor, family, weight, font_style, rotation):
        return (
            f"<g transform='translate({x:.3f} {y:.3f}) rotate({rotation:.3f})'>"
            f"<text text-anchor='{anchor}' dominant-baseline='middle' font-family='{html_attr(family)}' "
            f"font-size='{size_px:.3f}' font-weight='{html_attr(weight)}' font-style='{html_attr(font_style)}' "
            f"letter-spacing='0' paint-order='stroke fill'>{html_text(label)}</text></g>"
        )

    for entry in active_subs or []:
        if isinstance(entry, tuple) and len(entry) >= 2:
            sub, sub_time = entry[0], entry[1]
        else:
            sub, sub_time = entry, current_time
        if not isinstance(sub, dict):
            continue
        style = sub.get("style", sub)
        if not isinstance(style, dict) or not _scene_light_bool(style.get("scene_light_enable", False)):
            continue

        current_mask_color = _normalize_render_hex_color(style.get("scene_light_mask_color", "#000000"), "#000000") or "#000000"
        current_dim = max(0.0, min(100.0, _safe_float(style.get("scene_light_dim", 92), 92))) / 100.0
        if current_dim >= dim_alpha:
            dim_alpha = current_dim
            mask_color = current_mask_color
            mask_rgb = hex_to_rgb(mask_color)

        clip_start = _safe_float(sub.get("start", sub_time), sub_time)
        clip_end = _safe_float(sub.get("end", clip_start + 1.0), clip_start + 1.0)
        if sub_time < clip_start - 0.001 or sub_time > clip_end + 0.001:
            continue

        mode = _reveal_mode(style)
        color = _normalize_render_hex_color(style.get("scene_light_color", "#F6C76A"), "#F6C76A") or "#F6C76A"
        strength = max(0.0, min(100.0, _safe_float(style.get("scene_light_strength", 76), 76))) / 100.0
        radius = max(35.0, min(1200.0, _safe_float(style.get("scene_light_radius", 360), 360)))
        x_scale = max(0.15, min(2.40, _safe_float(style.get("scene_light_x_scale", 56), 56) / 100.0))
        y_scale = max(0.15, min(2.80, _safe_float(style.get("scene_light_y_scale", 112), 112) / 100.0))
        blur = max(0.0, min(180.0, _safe_float(style.get("scene_light_blur", 38), 38)))
        spill = max(0.0, min(100.0, _safe_float(style.get("scene_light_spill", 68), 68))) / 100.0
        edge_lift = max(0.0, min(100.0, _safe_float(style.get("scene_light_edge_lift", 34), 34))) / 100.0
        decay = max(0.05, min(1.50, _safe_float(style.get("scene_light_decay", 0.38), 0.38)))
        hold = max(0.0, min(0.35, _safe_float(style.get("scene_light_hold", 0.02), 0.02)))
        fade_time = max(0.0, min(0.60, _safe_float(style.get("voice_reveal_fade", 0.06), 0.06)))

        px = max(-80.0, min(180.0, _safe_float(sub.get("pos_x", 0.0), 0.0)))
        py = max(-80.0, min(180.0, _safe_float(sub.get("pos_y", 25.0), 25.0)))
        rot = max(-180.0, min(180.0, _safe_float(style.get("rotation", 0), 0)))
        size_px = max(8.0, min(520.0, _safe_float(style.get("size", 100), 100)))
        line_height = max(0.72, min(2.4, _safe_float(style.get("line_height", 1.1), 1.1)))
        layout_mode = str(style.get("layout_mode", "standard") or "standard").strip().lower()
        if layout_mode in ("contrast", "triple", "reel_stack", "random_focus", "side_steps", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block"):
            line_height = max(0.60, min(3.0, line_height * max(0.35, min(3.0, _safe_float(style.get("layout_row_gap", 100), 100) / 100.0))))
        line_gap_px = size_px * line_height
        x_px = canvas_w * (0.5 + px / 100.0)
        y_px = canvas_h * (0.5 + py / 100.0)
        align = str(style.get("text_align", "center") or "center").lower()
        family = str(style.get("font", "Arial") or "Arial")
        weight = str(style.get("font_weight", "800") or "800")
        font_style = str(style.get("font_style", "normal") or "normal")
        letter_spacing = _safe_float(style.get("letter_spacing", 0), 0)
        word_spacing = _safe_float(style.get("word_spacing", 0), 0)
        gap_px = max(size_px * 0.22, size_px * 0.32 + word_spacing)
        width_pct = _safe_float(style.get("box_width", 0), 0)
        if width_pct <= 0:
            width_pct = 74.0
        box_width_px = canvas_w * max(24.0, min(120.0, width_pct)) / 100.0

        rows = _layout_scene_words(sub, style, clip_start, clip_end, size_px)
        if not rows:
            continue
        line_starts = [min(_safe_float(w.get("start", clip_start), clip_start) for w in row) for row in rows]
        start_y = -((len(rows) - 1) * line_gap_px) / 2.0
        trigger = str(style.get("scene_light_trigger", "word") or "word").strip().lower()

        total_light_words = max(1, sum(len(row) for row in rows))
        light_slot_i = 0
        for row_i, row in enumerate(rows):
            widths = [_text_width_px(word["text"], size_px, letter_spacing) for word in row]
            row_width = sum(widths) + max(0, len(row) - 1) * gap_px
            if align == "left":
                cursor_x = x_px - box_width_px / 2.0
            elif align == "right":
                cursor_x = x_px + box_width_px / 2.0 - row_width
            else:
                cursor_x = x_px - row_width / 2.0
            row_y = y_px + start_y + row_i * line_gap_px
            for word_i, word in enumerate(row):
                light_slot_i += 1
                label = word["text"]
                word_width = widths[word_i]
                word_x = cursor_x + word_width / 2.0
                cursor_x += word_width + gap_px
                reveal_start = line_starts[row_i] if mode == "line_voice" else _safe_float(word.get("start", clip_start), clip_start)
                if mode != "all" and sub_time < reveal_start:
                    continue
                fade = 1.0
                if mode != "all" and fade_time > 0:
                    fade = max(0.0, min(1.0, (sub_time - reveal_start) / max(0.01, fade_time)))
                    fade = 1.0 - (1.0 - fade) ** 3
                if fade <= 0.01:
                    continue
                starts = [reveal_start]
                if trigger in {"char", "character", "letter"}:
                    clean = re.sub(r"\s+", "", label)
                    char_count = max(1, min(24, len(clean)))
                    span = max(0.04, min(max(0.04, _safe_float(word.get("end", reveal_start + 0.16), reveal_start + 0.16) - reveal_start), decay * 1.6))
                    interval = max(0.035, span / char_count)
                    starts = [reveal_start + i * interval for i in range(char_count)]
                pulse = 0.0
                for local_start in starts:
                    age = sub_time - local_start
                    if age < -0.002 or age > decay + hold:
                        continue
                    if age <= hold:
                        local = 1.0
                    else:
                        p = max(0.0, min(1.0, (age - hold) / decay))
                        local = (1.0 - p) ** 2.15
                    pulse = max(pulse, local)
                base_opacity = 0.62 + strength * 0.30 + spill * 0.10
                reveal_opacity = max(0.0, min(1.0, (base_opacity + pulse * 0.12) * fade))
                if reveal_opacity <= 0.01:
                    continue
                progress_raw = max(0.0, min(1.0, (light_slot_i - 1 + fade) / total_light_words))
                progress_curve = progress_raw ** 1.45
                radius_mix = max(0.0, min(1.0, radius / 1200.0))
                spread_curve = 0.50 + 0.50 * progress_curve
                base_rx = max(
                    word_width * (0.62 + spill * 0.42),
                    size_px * (1.35 + spill * 0.92),
                    canvas_w * (0.050 + radius_mix * 0.34) * spread_curve,
                )
                base_ry = max(
                    size_px * (1.95 + spill * 1.18),
                    canvas_h * (0.070 + radius_mix * 0.40) * spread_curve,
                )
                spot_rx = max(size_px * 0.70, base_rx * x_scale)
                spot_ry = max(size_px * 0.90, base_ry * y_scale)
                outer_rx = max(
                    spot_rx * (1.10 + edge_lift * 0.82),
                    canvas_w * (0.070 + radius_mix * 0.30) * spread_curve * x_scale,
                )
                outer_ry = max(
                    spot_ry * (1.14 + edge_lift * 0.92),
                    canvas_h * (0.12 + radius_mix * 0.46) * spread_curve * y_scale,
                )
                spot_center_alpha = max(0.0, min(0.96, (0.34 + strength * 0.44 + spill * 0.16 + pulse * 0.22) * fade))
                spot_mid_alpha = max(0.0, min(0.66, (0.13 + strength * 0.22 + spill * 0.18 + pulse * 0.13) * fade * (0.55 + 0.45 * progress_curve)))
                spot_edge_alpha = max(0.0, min(0.46, (0.020 + edge_lift * 0.30 + strength * 0.065 + spill * 0.052 + pulse * 0.05) * fade * progress_curve))
                spot_bloom_alpha = max(0.0, min(0.56, (0.055 + strength * 0.18 + spill * 0.20 + edge_lift * 0.12 + pulse * 0.15) * fade * (0.50 + 0.50 * progress_curve)))
                if spot_center_alpha > 0.02:
                    spot_items.append((word_x, row_y, spot_rx, spot_ry, outer_rx, outer_ry, spot_center_alpha, spot_mid_alpha, spot_edge_alpha, spot_bloom_alpha, color))
                    ambient_lift_alpha = max(ambient_lift_alpha, min(0.22, (0.010 + edge_lift * 0.17 + strength * 0.030 + spill * 0.024) * fade * progress_curve))

    if dim_alpha <= 0.004:
        return ""

    mr, mg, mb = mask_rgb
    defs = []
    mask_layers = ["<rect width='100%' height='100%' fill='white'/>"]
    bloom_layers = []
    if ambient_lift_alpha > 0.001:
        mask_layers.append(f"<rect width='100%' height='100%' fill='black' opacity='{ambient_lift_alpha:.4f}'/>")
    for idx, (cx, cy, rx, ry, outer_rx, outer_ry, center_alpha, mid_alpha, edge_alpha, bloom_alpha, color) in enumerate(spot_items):
        if edge_alpha > 0.01:
            defs.append(
                f"<radialGradient id='scene-edge-mask-{idx}' cx='50%' cy='50%' r='50%'>"
                f"<stop offset='0%' stop-color='black' stop-opacity='{edge_alpha:.4f}'/>"
                f"<stop offset='42%' stop-color='black' stop-opacity='{edge_alpha * 0.66:.4f}'/>"
                f"<stop offset='78%' stop-color='black' stop-opacity='{edge_alpha * 0.22:.4f}'/>"
                f"<stop offset='100%' stop-color='black' stop-opacity='0'/>"
                f"</radialGradient>"
            )
            mask_layers.append(f"<ellipse cx='{cx:.3f}' cy='{cy:.3f}' rx='{outer_rx:.3f}' ry='{outer_ry:.3f}' fill='url(#scene-edge-mask-{idx})'/>")
        defs.append(
            f"<radialGradient id='scene-spot-mask-{idx}' cx='50%' cy='50%' r='50%'>"
            f"<stop offset='0%' stop-color='black' stop-opacity='{center_alpha:.4f}'/>"
            f"<stop offset='35%' stop-color='black' stop-opacity='{center_alpha * 0.82:.4f}'/>"
            f"<stop offset='70%' stop-color='black' stop-opacity='{mid_alpha:.4f}'/>"
            f"<stop offset='100%' stop-color='black' stop-opacity='0'/>"
            f"</radialGradient>"
        )
        mask_layers.append(f"<ellipse cx='{cx:.3f}' cy='{cy:.3f}' rx='{rx:.3f}' ry='{ry:.3f}' fill='url(#scene-spot-mask-{idx})'/>")
        if bloom_alpha > 0.01:
            defs.append(
                f"<radialGradient id='scene-spot-bloom-{idx}' cx='50%' cy='50%' r='50%'>"
                f"<stop offset='0%' stop-color='{html_attr(color)}' stop-opacity='{bloom_alpha:.4f}'/>"
                f"<stop offset='38%' stop-color='{html_attr(color)}' stop-opacity='{bloom_alpha * 0.48:.4f}'/>"
                f"<stop offset='100%' stop-color='{html_attr(color)}' stop-opacity='0'/>"
                f"</radialGradient>"
            )
            bloom_layers.append(f"<ellipse cx='{cx:.3f}' cy='{cy:.3f}' rx='{outer_rx:.3f}' ry='{outer_ry:.3f}' fill='url(#scene-spot-bloom-{idx})' opacity='0.55'/>")
            bloom_layers.append(f"<ellipse cx='{cx:.3f}' cy='{cy:.3f}' rx='{rx:.3f}' ry='{ry:.3f}' fill='url(#scene-spot-bloom-{idx})'/>")

    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {canvas_w:.3f} {canvas_h:.3f}' "
        f"preserveAspectRatio='none' style='position:absolute; inset:0; width:100%; height:100%; pointer-events:none; z-index:1; overflow:visible;'>"
        f"<defs>{''.join(defs)}<mask id='scene-light-cutout' maskUnits='userSpaceOnUse' mask-type='luminance'>{''.join(mask_layers)}</mask></defs>"
        f"<rect x='0' y='0' width='{canvas_w:.3f}' height='{canvas_h:.3f}' fill='rgba({mr},{mg},{mb},{dim_alpha:.4f})' mask='url(#scene-light-cutout)'/>"
        f"<g style='mix-blend-mode:screen; pointer-events:none;'>{''.join(bloom_layers)}</g>"
        f"</svg>"
    )
    return svg

def render_subtitle_html(sub, current_time, proj_w=1080, proj_h=None):
    def vw(val):
        return f"{float(val) * 100 / proj_w:.4f}vw"

    def clamp01(value):
        return max(0.0, min(1.0, float(value)))

    def ease_out_cubic(value):
        p = clamp01(value)
        return 1.0 - pow(1.0 - p, 3)

    def ease_in_out(value):
        p = clamp01(value)
        return p * p * (3.0 - 2.0 * p)

    style = sub.get("style", sub)
    c_txt = style.get("color_txt", "#FFFFFF")
    c_hl = style.get("color_hl", "#FFFFFF")
    text_color_mode = str(style.get("text_color_mode", "single") or "single").strip().lower()
    text_random_palette = normalize_random_text_palette(style.get("text_random_palette"))
    text_random_enabled = text_color_mode in ("smart_random", "smart", "random") and any(item.get("enabled") for item in text_random_palette)
    text_random_seed = str(sub.get("text") or "")
    f_fam = style.get("font", "Arial")
    font_random_mode = str(style.get("font_random_mode", "single") or "single").strip().lower()
    font_random_pool = normalize_random_text_font_pool(style.get("font_random_pool"))
    font_random_strategy = str(style.get("font_random_strategy", "rotate") or "rotate").strip().lower()
    font_random_enabled = font_random_mode in ("smart_random", "smart", "random") and any(item.get("enabled") for item in font_random_pool)
    f_weight = str(style.get("font_weight", "700") or "700")
    f_style = str(style.get("font_style", "normal") or "normal")

    size = int(style.get("size", 100))
    bg_mode = style.get("bg_mode", "none")
    bg_col = style.get("bg_color", "#000000")
    bg_a = style.get("bg_alpha", 80) / 100.0
    rad = style.get("bg_radius", 15)
    pad = style.get("bg_padding", 20)
    pad_left = style.get("bg_pad_left", pad)
    pad_right = style.get("bg_pad_right", pad)
    pad_top = style.get("bg_pad_top", pad / 2.5)
    pad_bottom = style.get("bg_pad_bottom", pad / 2.5)

    hl_bg_col = style.get("hl_bg_color", "#FF0050")
    hl_bg_a = style.get("hl_bg_alpha", 100) / 100.0
    hl_rad = style.get("hl_bg_radius", 8)
    hl_pad = style.get("hl_bg_padding", 8)
    hl_pad_left = style.get("hl_pad_left", hl_pad)
    hl_pad_right = style.get("hl_pad_right", hl_pad)
    hl_pad_top = style.get("hl_pad_top", max(0, hl_pad / 3))
    hl_pad_bottom = style.get("hl_pad_bottom", max(0, hl_pad / 3))
    hl_bg_skew = max(-35.0, min(35.0, float(style.get("hl_bg_skew", 0) or 0)))
    hl_trail_words = max(1, min(8, int(style.get("hl_trail_words", 1) or 1)))
    hl_trail_min_alpha = max(0.0, min(1.0, float(style.get("hl_trail_min_alpha", 35) or 0) / 100.0))
    word_visual_min = max(0.02, min(0.40, _safe_float(style.get("word_visual_min_seconds", FAST_WORD_VISUAL_MIN_SECONDS), FAST_WORD_VISUAL_MIN_SECONDS)))

    lh = style.get("line_height", 1.1)
    layout_row_gap = max(0.6, min(2.2, _safe_float(style.get("layout_row_gap", 100), 100) / 100.0))
    rot = style.get("rotation", 0)

    stroke_w = style.get("stroke_width", 4)
    stroke_c = style.get("stroke_color", "#000000")
    stroke_o_w = style.get("stroke_o_width", 0)
    stroke_o_c = style.get("stroke_o_color", "#000000")
    stroke_softness = max(0, min(100, int(style.get("stroke_softness", 0))))
    sh_x = style.get("shadow_x", 5)
    sh_y = style.get("shadow_y", 5)
    sh_blur = style.get("shadow_blur", 0)
    sh_c = style.get("shadow_color", "#000000")
    sh_a = style.get("shadow_alpha", 100) / 100.0

    trans = style.get("text_transform", "capitalize")
    align = style.get("text_align", "center")
    letter_spacing = style.get("letter_spacing", 0)
    word_spacing = style.get("word_spacing", 0)
    layout_mode = style.get("layout_mode", "standard")
    if layout_mode == "split_screen":
        layout_mode = style.get("split_screen_inner_layout", "standard") or "standard"
    layout_variant = style.get("layout_variant", "auto")
    layout_pattern_raw = str(style.get("layout_pattern", "auto") or "auto")
    layout_layer_pattern_raw = str(style.get("layout_layer_pattern", "auto") or "auto")
    layout_layer_words_raw = str(style.get("layout_layer_words", "auto") or "auto")
    layout_layer_count = max(0, min(5, int(style.get("layout_layer_count", 0) or 0)))
    axis_spread = max(0.0, min(2.0, float(style.get("axis_spread", 100) or 100) / 100.0))
    axis_gap = max(0.5, min(1.8, float(style.get("axis_gap", 100) or 100) / 100.0))
    emphasis_scale = max(100, int(style.get("emphasis_scale", 145)))
    contrast_small_scale = max(0.58, min(1.0, float(style.get("contrast_small_scale", 0.74) or 0.74)))
    box_layout = style.get("box_layout", "auto")
    use_hl = style.get("use_hl", True)
    hl_style = str(style.get("hl_style", "text") or "text").lower()
    hl_glow = style.get("hl_glow", False)
    glow_size = int(style.get("glow_size", 20))
    global_glow_enable = bool(style.get("global_glow_enable", False))
    global_glow_mode = str(style.get("global_glow_mode", "soft") or "soft")
    global_glow_motion = str(style.get("global_glow_motion", "stable") or "stable")
    global_glow_color = style.get("global_glow_color", "#FFFFFF")
    global_glow_size = max(0, int(style.get("global_glow_size", 18) or 0))
    global_glow_blur = max(0, int(style.get("global_glow_blur", 24) or 0))
    global_glow_alpha = max(0.0, min(1.0, float(style.get("global_glow_alpha", 35) or 0) / 100.0))
    global_glow_x = float(style.get("global_glow_x", 0) or 0)
    global_glow_y = float(style.get("global_glow_y", 0) or 0)
    global_glow_z = max(0.0, float(style.get("global_glow_z", 0) or 0))
    text_texture = style.get("text_texture", "none")
    text_3d_enable = bool(style.get("text_3d_enable", False))
    text_3d_depth = max(0, min(120, int(style.get("text_3d_depth", 0) or 0)))
    text_3d_x = float(style.get("text_3d_x", 2) or 0)
    text_3d_y = float(style.get("text_3d_y", 3) or 0)
    text_3d_color = style.get("text_3d_color", "#6F3A05")

    anim_type = style.get("anim_type", "pop")
    font_motion = style.get("font_motion", "none")
    dynamic_reflow_motion = font_motion == "dynamic_reflow" or layout_mode == "prayer_reflow"
    typewriter_motion = anim_type == "typewriter" or font_motion in ("typewriter_left", "dynamic_reflow")
    if dynamic_reflow_motion:
        layout_mode = "prayer_reflow"
    if anim_type == "typewriter":
        anim_type = "none"
    hl_motion = style.get("hl_motion", "stable")
    pop_speed = max(0.05, float(style.get("pop_speed", 0.18)))
    pop_bounce = max(100, int(style.get("pop_bounce", 128)))
    inactive_alpha = int(style.get("inactive_alpha", 100)) / 100.0
    text_reveal_mode = str(style.get("text_reveal_mode", "all") or "all").strip().lower()
    text_reveal_mode = {
        "word": "word_voice",
        "voice_word": "word_voice",
        "word_voice": "word_voice",
        "line": "line_voice",
        "voice_line": "line_voice",
        "line_voice": "line_voice",
        "all": "all",
        "none": "all",
    }.get(text_reveal_mode, text_reveal_mode)
    if text_reveal_mode not in ("all", "word_voice", "line_voice"):
        text_reveal_mode = "all"
    voice_reveal_active = text_reveal_mode in ("word_voice", "line_voice")
    voice_reveal_fade = max(0.0, min(0.60, _safe_float(style.get("voice_reveal_fade", 0.06), 0.06)))
    if voice_reveal_active:
        inactive_alpha = 0.0
    full_roll_window_height = max(8.0, min(100.0, _safe_float(style.get("full_roll_window_height", 42), 42)))
    full_roll_start_y = max(-120.0, min(120.0, _safe_float(style.get("full_roll_start_y", 28), 28)))
    full_roll_end_y = max(-120.0, min(120.0, _safe_float(style.get("full_roll_end_y", -18), -18)))
    full_roll_feather = max(0.0, min(45.0, _safe_float(style.get("full_roll_feather", 10), 10)))
    full_roll_lock_to_words = bool(style.get("full_roll_lock_to_words", True))

    box_width = float(style.get("box_width", 0))
    box_height = float(style.get("box_height", 0) or 0)
    max_lines = max(1, min(4, int(style.get("max_lines", 2) or 2)))
    mask_en = style.get("mask_en", False)
    mask_top = style.get("mask_top", 20)
    mask_bot = style.get("mask_bottom", 20)

    canvas_h = float(proj_h if proj_h is not None else style.get("_proj_h", 1920) or 1920)
    bg_auto_resolution = bool(style.get("bg_auto_resolution", True))
    bg_resolution_scale = max(0.35, min(4.0, min(float(proj_w or 1080), canvas_h) / 1080.0)) if bg_auto_resolution else 1.0

    def bg_vw(val):
        return vw(float(val) * bg_resolution_scale)

    size_vw = vw(size)
    rad_vw = bg_vw(rad)
    pad_y = bg_vw(pad / 2.5)
    pad_x = bg_vw(pad)
    pad_top_vw = bg_vw(pad_top)
    pad_right_vw = bg_vw(pad_right)
    pad_bottom_vw = bg_vw(pad_bottom)
    pad_left_vw = bg_vw(pad_left)
    ls_vw = vw(letter_spacing)
    ws_vw = vw(word_spacing)

    hl_rad_vw = bg_vw(hl_rad)
    hl_pad_y = bg_vw(max(0, hl_pad / 3))
    hl_pad_x = bg_vw(hl_pad)
    hl_pad_top_vw = bg_vw(hl_pad_top)
    hl_pad_right_vw = bg_vw(hl_pad_right)
    hl_pad_bottom_vw = bg_vw(hl_pad_bottom)
    hl_pad_left_vw = bg_vw(hl_pad_left)
    hl_spread_vw = bg_vw(max(0, hl_pad, hl_pad_left, hl_pad_right, hl_pad_top, hl_pad_bottom))

    r, g, b = hex_to_rgb(bg_col)
    hl_r, hl_g, hl_b = hex_to_rgb(hl_bg_col)
    stable_word_boxes = bg_mode in ("tape", "canva_fit", "canva_joined", "block", "full_frame", "sweep", "cinematic_frame") and hl_motion == "stable"

    words = sub.get("words", [])
    if not words:
        words = [{"text": sub.get("text", ""), "start": sub.get("start", 0), "end": sub.get("end", 1)}]

    if layout_mode == "standard" and box_width > 0 and max_lines > 1 and not typewriter_motion:
        has_manual_breaks = any("\n" in str(w.get("text") or w.get("word") or "") for w in words)
        if not has_manual_breaks:
            line_capacity, line_limit, _ = subtitle_layout_capacity(style, proj_w)
            words = _apply_balanced_breaks(words, line_capacity, line_limit, style)

    clip_start = float(sub.get("start", 0))
    clip_end = float(sub.get("end", 1))
    clip_dur = max(0.1, clip_end - clip_start)
    clip_progress = max(0.0, min(1.0, (current_time - clip_start) / clip_dur))
    whole_sub_progress = clip_progress * 100 if bg_mode == "sweep" else 0

    content_indices = [i for i, ww in enumerate(words) if _clean_word_text(ww)]
    content_center = (content_indices[0] + content_indices[-1]) / 2.0 if content_indices else (len(words) - 1) / 2.0
    full_roll_progress = clip_progress
    if anim_type == "full_text_roll" and full_roll_lock_to_words and content_indices:
        word_starts = [_safe_float(words[i].get("start", clip_start), clip_start) for i in content_indices]
        word_ends = [_safe_float(words[i].get("end", clip_start), clip_start) for i in content_indices]
        roll_start_time = max(clip_start, min(word_starts or [clip_start]))
        roll_end_time = min(clip_end, max(word_ends or [clip_end]))
        roll_end_time = max(roll_start_time + 0.05, roll_end_time)
        full_roll_progress = clamp01((current_time - roll_start_time) / max(0.05, roll_end_time - roll_start_time))
    typewriter_word_order = {}
    typewriter_reveal_starts = {}
    typewriter_word_interval = pop_speed
    typewriter_intro_duration = max(0.05, min(0.22, pop_speed * 0.9))
    typewriter_active_order = 0
    typewriter_active_p = 1.0
    if typewriter_motion:
        word_count = max(1, len(content_indices))
        available_span = max(0.18, min(clip_dur * 0.88, pop_speed * word_count))
        typewriter_word_interval = max(0.045, min(pop_speed, available_span / word_count))
        typewriter_intro_duration = max(0.05, min(0.20, typewriter_word_interval * 1.15))
        typewriter_word_order = {word_idx: order for order, word_idx in enumerate(content_indices)}
        for word_idx, order in typewriter_word_order.items():
            timed_start = words[word_idx].get("start", None)
            try:
                timed_start = float(timed_start)
            except Exception:
                timed_start = None
            if dynamic_reflow_motion and timed_start is not None and clip_start - 0.001 <= timed_start <= clip_end + 0.001:
                typewriter_reveal_starts[word_idx] = max(clip_start, min(clip_end, timed_start))
            else:
                typewriter_reveal_starts[word_idx] = clip_start + order * typewriter_word_interval

    def _typewriter_reveal_start_for(word_idx):
        if word_idx not in typewriter_word_order:
            return clip_end + 999.0
        return typewriter_reveal_starts.get(
            word_idx,
            clip_start + typewriter_word_order.get(word_idx, 0) * typewriter_word_interval,
        )

    typewriter_group_shift_em = 0.0
    if typewriter_motion and content_indices:
        if dynamic_reflow_motion:
            active_indices = [word_idx for word_idx in content_indices if current_time >= _typewriter_reveal_start_for(word_idx)]
            typewriter_active_order = len(active_indices) - 1 if active_indices else 0
        else:
            elapsed = max(0.0, current_time - clip_start)
            typewriter_active_order = max(0, min(len(content_indices) - 1, int(elapsed / max(0.001, typewriter_word_interval))))
        active_word_idx = content_indices[max(0, min(len(content_indices) - 1, typewriter_active_order))]
        active_start = _typewriter_reveal_start_for(active_word_idx)
        typewriter_active_p = ease_out_cubic((current_time - active_start) / typewriter_intro_duration)
        typewriter_group_shift_em = 0.28 * (1.0 - typewriter_active_p)
    head_letter_large_variant = layout_mode == "reel_stack" and layout_variant in ("head-letter-large", "initial-large")
    head_large_variant = layout_mode == "reel_stack" and layout_variant in ("head-large", "head-emphasis", "head-only", "head-uppercase")
    tail_large_variant = layout_mode == "reel_stack" and layout_variant in ("tail-large", "tail-emphasis", "tail-only", "tail-uppercase")
    if align in ("free_mix", "left_mix"):
        align_seed_text = "".join(_clean_word_text(words[i]) for i in content_indices) if content_indices else str(sub.get("text", ""))
        align_seed = int(clip_start * 1000) + sum(ord(ch) for ch in align_seed_text)
        if align == "left_mix":
            align = "center" if align_seed % 5 == 0 else "left"
        else:
            align = "left" if align_seed % 2 == 0 else "center"
    center_left_mode = align == "center_left"
    stable_left_box_mode = center_left_mode or (typewriter_motion and align == "left")
    typewriter_center_push_em = 0.0
    if typewriter_motion and stable_left_box_mode and content_indices:
        def _typewriter_char_width_em(ch):
            code = ord(ch)
            if ch.isspace():
                return 0.32
            if (
                0x3400 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF
                or 0x3040 <= code <= 0x30FF
                or 0xAC00 <= code <= 0xD7AF
            ):
                return 1.0
            if ch in "ilI.,'`|!:":
                return 0.30
            if ch in "mwMW@#%&":
                return 0.82
            if ch.isalpha() or ch.isdigit():
                return 0.56
            return 0.44

        def _typewriter_word_width_em(word_idx):
            text = _clean_word_text(words[word_idx])
            if not text:
                return 0.0
            return max(0.34, sum(_typewriter_char_width_em(ch) for ch in text))

        def _typewriter_span_width_em(order):
            if order < 0:
                return 0.0
            visible_indices = content_indices[:order + 1]
            word_width = sum(_typewriter_word_width_em(word_idx) for word_idx in visible_indices)
            gap_width = max(0, len(visible_indices) - 1) * (0.34 + max(0.0, float(word_spacing or 0)) / max(28.0, float(size or 100)))
            return word_width + gap_width

        prev_width_em = _typewriter_span_width_em(typewriter_active_order - 1)
        curr_width_em = _typewriter_span_width_em(typewriter_active_order)
        revealed_width_em = prev_width_em + (curr_width_em - prev_width_em) * typewriter_active_p
        font_size_vw = max(0.001, float(size or 100) * 100.0 / max(1.0, float(proj_w or 1080)))
        box_width_em = (box_width if box_width > 0 else 74.0) / font_size_vw
        max_push_em = max(1.4, min(4.8, box_width_em * 0.48))
        typewriter_center_push_em = min(max_push_em, max(0.0, (box_width_em - revealed_width_em) * 0.5))
    if layout_mode in ("mixed_reel", "smart_caption") and content_indices:
        mix_seed_text = "".join(_clean_word_text(words[i]) for i in content_indices)
        mix_seed = int(clip_start * 1000) + sum(ord(ch) for ch in mix_seed_text)
        count = len(content_indices)
        raw_pool = str(style.get("smart_layout_pool", "contrast,narrative_block,reel_stack,random_focus,axis_stack") or "")
        pool = [item.strip() for item in raw_pool.split(",") if item.strip()]
        pool = [item for item in pool if item in {"standard", "contrast", "narrative_block", "reel_stack", "random_focus", "side_steps", "axis_stack", "triple"}]
        if not pool:
            pool = ["standard"]
        if len(pool) == 1:
            layout_mode = pool[0]
        else:
            preferred = []
            if count >= 12:
                preferred = ["narrative_block", "reel_stack", "contrast", "axis_stack", "random_focus"]
            elif count >= 7:
                preferred = ["narrative_block", "contrast", "reel_stack", "random_focus", "axis_stack"]
            elif count >= 4:
                preferred = ["contrast", "random_focus", "reel_stack", "axis_stack", "side_steps"]
            else:
                preferred = ["axis_stack", "side_steps", "contrast", "reel_stack", "random_focus"]
            candidates = [item for item in preferred if item in pool] or pool
            layout_mode = candidates[mix_seed % len(candidates)]
    if layout_mode == "axis_stack" and layout_variant in ("axis-random", "axis_random", "random-axis"):
        axis_seed_text = "|".join(_clean_word_text(words[i]).lower() for i in content_indices) if content_indices else str(sub.get("text", ""))
        axis_seed = int(clip_start * 1000) + sum((pos + 1) * ord(ch) for pos, ch in enumerate(axis_seed_text))
        axis_choices = ("axis-123", "axis-split-tail", "axis-diagonal")
        layout_variant = axis_choices[axis_seed % len(axis_choices)]
        axis_spread = max(0.0, min(2.0, axis_spread * (0.86 + ((axis_seed // 3) % 5) * 0.07)))
        axis_gap = max(0.5, min(1.8, axis_gap * (0.92 + ((axis_seed // 17) % 4) * 0.05)))

    layout_content_indices = content_indices
    if dynamic_reflow_motion and content_indices:
        layout_content_indices = [i for i in content_indices if current_time >= _typewriter_reveal_start_for(i)]
        if not layout_content_indices and current_time >= clip_start:
            layout_content_indices = [content_indices[0]]
    emphasis_idx = set()
    small_idx = set()
    word_visual_windows = {}
    for order, i in enumerate(content_indices):
        ww = words[i]
        w_start = _safe_float(ww.get("start", clip_start), clip_start)
        w_end = _safe_float(ww.get("end", w_start + MIN_WORD_DURATION_SECONDS), w_start + MIN_WORD_DURATION_SECONDS)
        if w_end <= w_start:
            w_end = w_start + MIN_WORD_DURATION_SECONDS
        visual_end = max(w_end, w_start + word_visual_min)
        if order + 1 < len(content_indices):
            next_word = words[content_indices[order + 1]]
            next_start = _safe_float(next_word.get("start", w_end), w_end)
            if next_start > w_start:
                visual_end = min(visual_end, max(w_start + 0.02, next_start - 0.001))
        word_visual_windows[i] = (w_start, max(w_start + 0.02, visual_end))

    current_word_idx = None
    if use_hl and hl_style != "none":
        active_candidates = []
        for i in content_indices:
            w_start, w_end = word_visual_windows.get(i, (clip_start, clip_end))
            if w_start <= current_time < w_end:
                active_candidates.append((w_start, i))
        if active_candidates:
            current_word_idx = max(active_candidates, key=lambda item: (item[0], item[1]))[1]

    hl_trail_alpha_by_idx = {}
    if current_word_idx is not None:
        hl_trail_alpha_by_idx[current_word_idx] = 1.0
        if hl_trail_words > 1:
            try:
                current_order = content_indices.index(current_word_idx)
            except ValueError:
                current_order = -1
            if current_order > 0:
                tail_indices = content_indices[max(0, current_order - (hl_trail_words - 1)):current_order]
                tail_total = len(tail_indices)
                for rank, word_idx in enumerate(reversed(tail_indices), start=1):
                    fade = 1.0 - (1.0 - hl_trail_min_alpha) * (rank / max(1, tail_total))
                    hl_trail_alpha_by_idx[word_idx] = max(0.0, min(1.0, fade))

    def _token_score(token):
        t = re.sub(r"[^A-Za-z0-9一-鿿]", "", token or "")
        if not t:
            return -999
        stop = {
            "i", "me", "my", "you", "your", "we", "our", "to", "the", "a", "an", "and", "or",
            "but", "if", "of", "in", "on", "for", "is", "am", "are", "be", "with", "that",
            "this", "it", "he", "she", "they", "them", "him", "her", "so"
        }
        lower = t.lower()
        score = len(t) * 1.4
        if lower in stop:
            score -= 3.2
        if len(t) <= 2:
            score -= 1.6
        if t.isupper() and len(t) > 1:
            score += 1.2
        if lower in FAITH_WORDS:
            score += 1.5
        return score

    def _layout_pattern_slots(raw):
        raw_text = str(raw or "").strip().lower()
        if not raw_text or raw_text == "auto":
            return []
        slots = []
        for ch in raw_text:
            if ch in "大lbh":
                slots.append("large")
            elif ch in "中m":
                slots.append("mid")
            elif ch in "小s":
                slots.append("small")
        return slots

    def _scale_from_slot(slot, small_base, mid_base, large_base):
        if slot == "large":
            return large_base
        if slot == "mid":
            return mid_base
        return small_base

    def _split_rows_even(items, row_count):
        row_count = max(1, min(len(items), int(row_count or 1)))
        rows = []
        start = 0
        for row_i in range(row_count):
            remaining_items = len(items) - start
            remaining_rows = row_count - row_i
            take = max(1, int(math.ceil(remaining_items / remaining_rows)))
            rows.append(items[start:start + take])
            start += take
        return [row for row in rows if row]

    def _split_rows_from_spec(items, spec):
        spec_text = str(spec or "").strip().lower()
        if not spec_text or spec_text == "auto":
            return []
        counts = [int(n) for n in re.findall(r"\d+", spec_text) if int(n) > 0]
        if not counts:
            return []
        rows = []
        start = 0
        for count_value in counts:
            if start >= len(items):
                break
            rows.append(items[start:min(len(items), start + count_value)])
            start += count_value
        if start < len(items):
            rows.append(items[start:])
        return [row for row in rows if row]

    if layout_mode in ("contrast", "triple", "reel_stack", "random_focus", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block") and layout_content_indices:
        variant = layout_variant
        if variant == "auto":
            m = len(layout_content_indices) % 3
            variant = "small-big-small" if m == 1 else "big-small-mix" if m == 2 else "mix-big-small"

        ranked = sorted(
            layout_content_indices,
            key=lambda i: (_token_score(_clean_word_text(words[i])), -abs(i - len(words) / 2)),
            reverse=True,
        )

        if layout_mode == "contrast":
            pattern_slots = _layout_pattern_slots(layout_pattern_raw)
            if pattern_slots:
                for order, word_idx in enumerate(layout_content_indices):
                    slot = pattern_slots[order % len(pattern_slots)]
                    if slot == "large":
                        emphasis_idx.add(word_idx)
                    elif slot == "small":
                        small_idx.add(word_idx)
            else:
                focus_count = 1 if len(content_indices) <= 4 else 2
                emphasis_idx.update(sorted(ranked[:focus_count]))
                if not emphasis_idx:
                    emphasis_idx.add(content_indices[max(0, len(content_indices) // 2)])
                small_idx.update([i for i in content_indices if i not in emphasis_idx])
        elif layout_mode == "triple":
            pattern_slots = _layout_pattern_slots(layout_pattern_raw)
            if pattern_slots:
                for order, word_idx in enumerate(layout_content_indices):
                    slot = pattern_slots[order % len(pattern_slots)]
                    if slot == "large":
                        emphasis_idx.add(word_idx)
                    elif slot == "small":
                        small_idx.add(word_idx)
            elif variant == "small-big-small":
                emphasis_idx.update(ranked[:1] or [content_indices[min(1, len(content_indices) - 1)]])
                small_idx.update([i for i in content_indices if i not in emphasis_idx])
            elif variant == "big-small-mix":
                emphasis_idx.update(ranked[:2] if len(content_indices) > 4 else ranked[:1])
                small_idx.update([i for i in content_indices if i not in emphasis_idx])
            else:
                focus = ranked[:1] or [content_indices[min(len(content_indices) // 2, len(content_indices) - 1)]]
                emphasis_idx.update(focus)
                if len(content_indices) > 5:
                    emphasis_idx.add(content_indices[0])
                small_idx.update([i for i in content_indices if i not in emphasis_idx])
        elif layout_mode == "reel_stack":
            if head_large_variant:
                emphasis_idx.add(content_indices[0])
            elif tail_large_variant:
                emphasis_idx.add(content_indices[-1])
            elif not head_letter_large_variant:
                emphasis_idx.add(content_indices[0])
                if len(content_indices) > 1:
                    emphasis_idx.add(content_indices[-1])
            small_idx.update([i for i in content_indices if i not in emphasis_idx])
        elif layout_mode == "random_focus":
            focus_count = 2 if len(content_indices) <= 5 else 3
            emphasis_idx.update(sorted(ranked[:focus_count]))
            small_idx.update([i for i in content_indices if i not in emphasis_idx])
        elif layout_mode == "axis_stack":
            if content_indices:
                emphasis_idx.add(content_indices[0])
                if len(content_indices) >= 3:
                    emphasis_idx.add(content_indices[-1])
            small_idx.update([i for i in content_indices if i not in emphasis_idx and len(content_indices) > 2])
        elif layout_mode == "quote_stack":
            emphasis_idx.add(layout_content_indices[0])
            if len(layout_content_indices) > 1:
                emphasis_idx.add(layout_content_indices[-1])
            small_idx.update([i for i in layout_content_indices if i not in emphasis_idx])
        elif layout_mode == "narrative_block":
            pass
        elif layout_mode == "prayer_reflow":
            stop_anchor = {"is", "am", "are", "the", "this", "that", "with", "for", "your", "my", "in", "to"}
            rows_probe = []
            n_probe = len(layout_content_indices)
            if n_probe <= 4:
                rows_probe = [[item] for item in layout_content_indices]
            elif n_probe == 5:
                rows_probe = [layout_content_indices[:3], layout_content_indices[3:]]
            elif n_probe <= 7:
                rows_probe = [[layout_content_indices[0]], layout_content_indices[1:3], layout_content_indices[3:-1], [layout_content_indices[-1]]]
            else:
                rows_probe = [[layout_content_indices[0]], layout_content_indices[1:3], layout_content_indices[3:6], layout_content_indices[6:]]
            for row_i, row in enumerate(rows_probe):
                if not row:
                    continue
                anchor = row[0]
                anchor_txt = _clean_word_text(words[anchor]).lower()
                if row_i == 0 or anchor_txt not in stop_anchor or len(row) == 1:
                    emphasis_idx.add(anchor)
                if len(row) == 1 and anchor_txt in ("is", "the", "this", "that", "with", "for", "in", "to"):
                    emphasis_idx.discard(anchor)
            for idx in ranked[:1]:
                if _token_score(_clean_word_text(words[idx])) >= 6.0:
                    emphasis_idx.add(idx)
            small_idx.update([i for i in layout_content_indices if i not in emphasis_idx])

    content_order = {word_idx: order for order, word_idx in enumerate(content_indices)}

    def _build_layout_rows():
        items = layout_content_indices
        n = len(items)
        if not items or layout_mode not in ("reel_stack", "random_focus", "side_steps", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block"):
            return []
        if layout_mode == "narrative_block":
            spec_rows = _split_rows_from_spec(items, layout_layer_words_raw)
            if spec_rows:
                return spec_rows
            if layout_layer_count > 0 and n > layout_layer_count:
                return _split_rows_even(items, layout_layer_count)
            if n <= 4:
                return [items]
            if n <= 7:
                return [items[:4], items[4:]]
            if n <= 11:
                return [items[:4], items[4:7], items[7:]]
            first_count = 4 if n <= 14 else 5
            second_count = 3
            third_count = 4 if n <= 15 else 5
            first_end = min(n, first_count)
            second_end = min(n, first_end + second_count)
            third_end = min(n, second_end + third_count)
            return [items[:first_end], items[first_end:second_end], items[second_end:third_end], items[third_end:]]
        if layout_mode == "prayer_reflow":
            if n <= 4:
                return [[item] for item in items]
            if n == 5:
                return [items[:3], items[3:]]
            if n == 6:
                return [[items[0]], items[1:3], items[3:5], [items[5]]]
            if n == 7:
                return [[items[0]], items[1:3], items[3:6], [items[6]]]
            if n <= 10:
                return [[items[0]], items[1:3], items[3:6], items[6:]]
            return [[items[0]], items[1:3], items[3:6], items[6:9], items[9:]]
        if layout_mode == "quote_stack":
            if n <= 2:
                return [[item] for item in items]
            if n <= 4:
                return [[items[0]], items[1:-1], [items[-1]]]
            if n <= 7:
                mid = 1 + max(2, min(3, n - 2))
                return [[items[0]], items[1:mid], items[mid:-1], [items[-1]]]
            left_mid = 1 + max(3, min(4, (n - 2) // 2 + 1))
            return [[items[0]], items[1:left_mid], items[left_mid:-1], [items[-1]]]
        if layout_mode == "side_steps":
            return [[item] for item in items]
        if layout_mode == "axis_stack":
            if layout_variant == "axis-diagonal" and n >= 3:
                first_end = max(1, n // 3)
                second_end = max(first_end + 1, min(n - 1, first_end + max(1, n // 3)))
                return [items[:first_end], items[first_end:second_end], items[second_end:]]
            if layout_variant == "axis-123" or n <= 3:
                return [[item] for item in items]
            if n == 4:
                return [[items[0]], [items[1]], [items[2], items[3]]]
            return [[item] for item in items[:-2]] + [items[-2:]]
        if layout_mode == "reel_stack":
            if head_letter_large_variant or head_large_variant:
                if n <= 2:
                    return [[item] for item in items]
                if n <= 4:
                    return [[items[0]], items[1:]]
                mid = max(2, min(n - 1, n // 2 + 1))
                return [[items[0]], items[1:mid], items[mid:]]
            if tail_large_variant:
                if n <= 2:
                    return [[item] for item in items]
                if n <= 4:
                    return [items[:-1], [items[-1]]]
                mid = max(2, min(n - 1, n // 2))
                return [items[:mid], items[mid:-1], [items[-1]]]
            if n <= 3:
                return [[item] for item in items]
            if n == 4:
                return [[items[0]], [items[1], items[2]], [items[3]]]
            mid = max(2, min(n - 2, n // 2))
            return [[items[0]], items[1:mid], items[mid:-1], [items[-1]]]
        if layout_mode == "random_focus":
            if n <= 3:
                return [[item] for item in items]
            if n == 4:
                return [items[:2], [items[2]], [items[3]]]
            return [items[:3], [items[3]], items[4:]]
        return []

    layout_rows = _build_layout_rows()
    layout_break_before = {row[0] for row in layout_rows[1:] if row}
    layout_row_lookup = {
        word_idx: (row_i, pos_i, len(row))
        for row_i, row in enumerate(layout_rows)
        for pos_i, word_idx in enumerate(row)
    }

    def _layout_breaks_before(word_idx):
        return word_idx in layout_break_before

    first_content_idx = content_indices[0] if content_indices else None
    final_content_idx = content_indices[-1] if content_indices else None
    word_line_indices = {}
    line_i = 0
    for word_idx, word in enumerate(words):
        raw_line_txt = str(word.get("text") or word.get("word") or "")
        if word_idx > 0 and ("\n" in raw_line_txt or _layout_breaks_before(word_idx)):
            line_i += 1
        word_line_indices[word_idx] = line_i
    holy_line_count = line_i + 1
    line_reveal_starts = {}
    if text_reveal_mode == "line_voice":
        for word_idx in content_indices:
            line_idx = word_line_indices.get(word_idx, 0)
            w_start = _safe_float(words[word_idx].get("start", clip_start), clip_start)
            if line_idx not in line_reveal_starts:
                line_reveal_starts[line_idx] = w_start
            else:
                line_reveal_starts[line_idx] = min(line_reveal_starts[line_idx], w_start)

    def _voice_reveal_start_for(word_idx):
        if text_reveal_mode == "line_voice":
            return line_reveal_starts.get(word_line_indices.get(word_idx, 0), clip_start)
        if text_reveal_mode == "word_voice":
            return _safe_float(words[word_idx].get("start", clip_start), clip_start)
        return clip_start

    html_words_fg = []
    html_words_bg = []

    for idx, w in enumerate(words):
        raw_txt = str(w.get("text") or w.get("word") or "")
        has_newline = "\n" in raw_txt
        clean_txt = raw_txt.replace("\n", "").strip()

        if not clean_txt:
            if has_newline:
                html_words_fg.append("<br>")
                if bg_mode in ("tape", "canva_fit", "canva_joined", "block", "sweep"):
                    html_words_bg.append("<br>")
            continue

        typewriter_reveal_start = clip_start
        typewriter_local_p = 1.0
        if typewriter_motion:
            typewriter_reveal_start = _typewriter_reveal_start_for(idx)
            if current_time < typewriter_reveal_start and not voice_reveal_active:
                continue
            if current_time >= typewriter_reveal_start:
                typewriter_local_p = ease_out_cubic((current_time - typewriter_reveal_start) / typewriter_intro_duration)

        inserted_break = False
        if has_newline and idx > 0:
            html_words_fg.append("<br>")
            inserted_break = True
            if bg_mode in ("tape", "canva_fit", "canva_joined", "block", "sweep"):
                html_words_bg.append("<br>")

        if not inserted_break and _layout_breaks_before(idx):
            html_words_fg.append("<br>")
            if bg_mode in ("tape", "canva_fit", "canva_joined", "block", "sweep"):
                html_words_bg.append("<br>")

        clean_txt = _style_display_text(clean_txt, style)
        word_text_color = c_txt
        if text_random_enabled:
            word_text_color = stable_random_text_color(text_random_palette, idx, clean_txt, text_random_seed, c_txt)
        word_font_family = f_fam
        if font_random_enabled:
            word_font_family = stable_random_text_font(font_random_pool, idx, clean_txt, text_random_seed, f_fam, font_random_strategy)

        w_start = float(w.get("start", 0))
        w_end = float(w.get("end", w_start + 0.5))

        holy_line_idx = word_line_indices.get(idx, 0)
        is_holy_final_word = anim_type == "holy_breath" and idx == final_content_idx
        holy_speed = max(1.15, pop_speed * 4.8)
        if is_holy_final_word:
            holy_speed *= 1.35
            holy_speed = min(holy_speed, max(0.65, clip_dur * 0.72))
        else:
            holy_speed = min(holy_speed, max(0.55, clip_dur * 0.62))

        if voice_reveal_active:
            voice_reveal_start = _voice_reveal_start_for(idx)
            word_started = current_time >= voice_reveal_start
            t = current_time - voice_reveal_start
        elif anim_type == "holy_breath":
            line_delay = min(0.70, 0.38 + max(0.0, pop_speed - 0.18) * 0.18)
            if holy_line_count > 1:
                line_delay = min(line_delay, max(0.12, clip_dur * 0.32 / max(1, holy_line_count - 1)))
            holy_reveal_start = max(clip_start, min(w_start, clip_end - 0.05))
            holy_reveal_start = max(holy_reveal_start, clip_start + holy_line_idx * line_delay)
            if is_holy_final_word:
                holy_reveal_start += min(0.55, max(0.22, clip_dur * 0.12))
            min_visible = min(0.80 if is_holy_final_word else 0.42, max(0.34 if is_holy_final_word else 0.20, clip_dur * (0.26 if is_holy_final_word else 0.14)))
            latest_reveal_start = max(clip_start, clip_end - 0.12 - min_visible)
            holy_reveal_start = min(holy_reveal_start, latest_reveal_start)
            word_started = current_time >= holy_reveal_start
            t = current_time - holy_reveal_start
        elif typewriter_motion:
            word_started = current_time >= typewriter_reveal_start
            t = current_time - typewriter_reveal_start
        else:
            word_started = current_time >= w_start
            t = current_time - w_start
        is_active = word_started
        hl_trail_alpha = hl_trail_alpha_by_idx.get(idx, 0.0)
        is_current = use_hl and hl_style != "none" and idx == current_word_idx
        is_hl_marked = use_hl and hl_style != "none" and hl_trail_alpha > 0.0

        current_scale = 1.0
        current_opacity = inactive_alpha
        current_translate_em = 0.0
        current_translate_x_em = 0.0
        current_rotate_x_deg = 0.0
        current_rotate_y_deg = 0.0
        current_filter_css = "filter: none;"
        current_clip_css = ""
        word_reveal_pct = 100.0
        current_letter_extra = 0.0

        if is_active:
            current_opacity = 1.0
            if anim_type == "pop" and t >= 0:
                if t <= pop_speed and pop_speed > 0:
                    p = clamp01(t / pop_speed)
                    overshoot = 0.08 + max(0, pop_bounce - 100) / 100.0 * 0.08
                    damp = math.sin(p * math.pi)
                    current_scale = 1.0 + (0.18 + overshoot) * damp
            elif anim_type == "fade" and t >= 0:
                if t <= pop_speed and pop_speed > 0:
                    current_opacity = inactive_alpha + (1.0 - inactive_alpha) * ease_out_cubic(t / pop_speed)
            elif anim_type == "blur_fade" and t >= 0:
                p = ease_out_cubic(t / pop_speed)
                current_opacity = p
                current_translate_em += (1.0 - p) * 0.16
                current_scale *= 0.96 + 0.04 * p
                current_filter_css = f"filter: blur({vw(8 * (1.0 - p))});"
            elif anim_type == "grow_in" and t >= 0:
                p = ease_out_cubic(t / pop_speed)
                current_opacity = p
                current_translate_em += (1.0 - p) * 0.08
                current_scale *= 0.28 + 0.72 * p
                current_filter_css = f"filter: blur({vw(3 * (1.0 - p))});"
            elif anim_type == "scatter_in" and t >= 0:
                p = ease_out_cubic(t / pop_speed)
                spread = idx - content_center
                current_opacity = p
                current_translate_x_em += spread * 0.34 * (1.0 - p)
                current_translate_em += math.sin(idx * 1.71) * 0.20 * (1.0 - p)
                current_scale *= 0.82 + 0.18 * p
                current_filter_css = f"filter: blur({vw(5 * (1.0 - p))});"
            elif anim_type == "letter_scatter_in" and t >= 0:
                p = ease_out_cubic(t / max(0.05, pop_speed * 1.35))
                spread = idx - content_center
                current_opacity = p
                current_letter_extra = 14.0 * (1.0 - p)
                current_translate_x_em += spread * 0.18 * (1.0 - p)
                current_scale *= 0.90 + 0.10 * p
                current_filter_css = f"filter: blur({vw(4 * (1.0 - p))});"
            elif anim_type == "holy_breath" and t >= 0:
                p = ease_out_cubic(t / holy_speed)
                breath = math.sin(max(0.0, current_time - clip_start) * 1.35 + holy_line_idx * 0.45)
                final_weight = 1.0 if is_holy_final_word else 0.0
                current_opacity = p
                current_translate_em += (1.0 - p) * 0.22 - breath * (0.010 + final_weight * 0.004)
                current_scale *= (0.965 + 0.035 * p) * (1.0 + breath * (0.010 + final_weight * 0.008))
                current_filter_css = f"filter: blur({vw((10.0 + final_weight * 3.0) * (1.0 - p))});"
            elif anim_type == "word_wipe" and t >= 0:
                word_reveal_pct = ease_out_cubic(t / pop_speed) * 100.0
        elif anim_type == "blur_fade":
            current_opacity = 0.0
            current_translate_em += 0.16
            current_scale *= 0.96
            current_filter_css = f"filter: blur({vw(8)});"
        elif anim_type == "grow_in":
            current_opacity = 0.0
            current_translate_em += 0.08
            current_scale *= 0.28
            current_filter_css = f"filter: blur({vw(3)});"
        elif anim_type == "scatter_in":
            spread = idx - content_center
            current_opacity = 0.0
            current_translate_x_em += spread * 0.34
            current_translate_em += math.sin(idx * 1.71) * 0.20
            current_scale *= 0.82
            current_filter_css = f"filter: blur({vw(5)});"
        elif anim_type == "letter_scatter_in":
            spread = idx - content_center
            current_opacity = 0.0
            current_letter_extra = 14.0
            current_translate_x_em += spread * 0.18
            current_scale *= 0.90
            current_filter_css = f"filter: blur({vw(4)});"
        elif anim_type == "holy_breath":
            current_opacity = 0.0
            current_translate_em += 0.22
            current_scale *= 0.965
            current_filter_css = f"filter: blur({vw(13 if is_holy_final_word else 10)});"
        elif anim_type == "word_wipe":
            current_opacity = 1.0
            word_reveal_pct = 0.0

        if anim_type in ("wipe_right", "word_wipe"):
            current_opacity = 1.0
        if voice_reveal_active:
            if not is_active:
                current_opacity = 0.0
            elif voice_reveal_fade > 0:
                reveal_opacity = ease_out_cubic(t / voice_reveal_fade)
                current_opacity = min(current_opacity, reveal_opacity)
        if anim_type == "word_wipe":
            hidden_pct = max(0.0, 100.0 - word_reveal_pct)
            current_clip_css = f"-webkit-clip-path: inset(0 {hidden_pct:.3f}% 0 0); clip-path: inset(0 {hidden_pct:.3f}% 0 0);"

        layout_row_i, layout_pos_i, layout_row_len = layout_row_lookup.get(idx, (0, 0, 0))

        shadows = []
        stroke_r, stroke_g, stroke_b = hex_to_rgb(stroke_c)
        if stroke_o_w > 0:
            total_w = stroke_w + stroke_o_w
            outer_blur = total_w * (stroke_softness / 100.0) * 0.28
            for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
                sx = total_w * math.cos(math.radians(angle))
                sy = total_w * math.sin(math.radians(angle))
                shadows.append(f"{vw(sx)} {vw(sy)} {vw(outer_blur)} {stroke_o_c}")
        if stroke_w > 0 and stroke_softness > 0:
            soft_p = stroke_softness / 100.0
            soft_blur = stroke_w * (0.22 + 0.78 * soft_p)
            soft_spread = stroke_w * (0.18 + 0.20 * soft_p)
            soft_alpha = 0.24 + 0.30 * soft_p
            for angle in [0, 60, 120, 180, 240, 300]:
                sx = soft_spread * math.cos(math.radians(angle))
                sy = soft_spread * math.sin(math.radians(angle))
                shadows.append(f"{vw(sx)} {vw(sy)} {vw(soft_blur)} rgba({stroke_r}, {stroke_g}, {stroke_b}, {soft_alpha:.2f})")
            shadows.append(f"0 0 {vw(soft_blur * 1.35)} rgba({stroke_r}, {stroke_g}, {stroke_b}, {max(0.18, soft_alpha - 0.12):.2f})")
        if sh_x != 0 or sh_y != 0 or sh_blur != 0:
            sr, sg, sb = hex_to_rgb(sh_c)
            shadows.append(f"{vw(sh_x)} {vw(sh_y)} {vw(sh_blur)} rgba({sr}, {sg}, {sb}, {sh_a})")
        if global_glow_enable and global_glow_alpha > 0:
            gr, gg, gb = hex_to_rgb(global_glow_color)
            glow_phase = 1.0
            sweep_offset = 0.0
            if global_glow_motion == "breath":
                glow_phase = 0.72 + 0.28 * math.sin(current_time * 2.0 + idx * 0.07)
            elif global_glow_motion == "sweep" or global_glow_mode == "sweep":
                sweep_offset = math.sin(current_time * 2.7 + layout_row_i * 0.8) * (10.0 + global_glow_z * 0.08)
                glow_phase = 0.70 + 0.30 * abs(math.sin(current_time * 2.7 + idx * 0.15))
            depth_boost = 1.0 + global_glow_z / 100.0
            core_blur = max(global_glow_blur, global_glow_size) * depth_boost
            aura_blur = max(global_glow_blur * 1.55, global_glow_size * 1.85) * depth_boost
            alpha = max(0.0, min(1.0, global_glow_alpha * glow_phase))
            shadows.append(f"{vw(global_glow_x + sweep_offset)} {vw(global_glow_y)} {vw(core_blur)} rgba({gr}, {gg}, {gb}, {alpha:.3f})")
            shadows.append(f"{vw(global_glow_x * 0.5 + sweep_offset * 1.35)} {vw(global_glow_y * 0.5)} {vw(aura_blur)} rgba({gr}, {gg}, {gb}, {alpha * 0.45:.3f})")
            if global_glow_mode == "neon":
                shadows.append(f"0 0 {vw(max(global_glow_size * 2.6, global_glow_blur * 2.0))} rgba({gr}, {gg}, {gb}, {alpha * 0.32:.3f})")
        if is_current and hl_glow:
            shadows.extend([f"0 0 {vw(glow_size)} {c_hl}", f"0 0 {vw(glow_size*1.5)} {c_hl}", f"0 0 {vw(glow_size*2)} {c_hl}"])
        if anim_type == "holy_breath" and is_holy_final_word and current_opacity > 0.02:
            glow_hex = c_hl if use_hl else c_txt
            gr, gg, gb = hex_to_rgb(glow_hex)
            aura = min(1.0, max(0.0, current_opacity))
            shadows.extend([
                f"0 0 {vw(10)} rgba({gr}, {gg}, {gb}, {0.16 * aura:.2f})",
                f"0 0 {vw(22)} rgba({gr}, {gg}, {gb}, {0.10 * aura:.2f})",
            ])
        scene_light_text_on = _scene_light_bool(style.get("scene_light_enable", False)) and current_opacity > 0.02
        scene_light_filter_parts = []
        if scene_light_text_on:
            lr, lg, lb = hex_to_rgb(_normalize_render_hex_color(style.get("scene_light_color", "#F6C76A"), "#F6C76A") or "#F6C76A")
            scene_strength = max(0.0, min(1.0, _safe_float(style.get("scene_light_strength", 76), 76) / 100.0))
            scene_blur = max(6.0, min(120.0, _safe_float(style.get("scene_light_blur", 26), 26)))
            scene_spill = max(0.0, min(1.0, _safe_float(style.get("scene_light_spill", 42), 42) / 100.0))
            scene_radius = max(35.0, min(1200.0, _safe_float(style.get("scene_light_radius", 360), 360)))
            scene_x_scale = max(0.15, min(2.40, _safe_float(style.get("scene_light_x_scale", 56), 56) / 100.0))
            scene_y_scale = max(0.15, min(2.80, _safe_float(style.get("scene_light_y_scale", 112), 112) / 100.0))
            text_glow_scale = max(0.45, min(1.35, (scene_x_scale * 0.65 + scene_y_scale * 0.35)))
            scene_alpha = min(1.0, (0.48 + scene_strength * 0.48 + scene_spill * 0.22) * current_opacity)
            aura_blur = max(scene_blur * 2.65, scene_radius * 0.34) * text_glow_scale
            wide_blur = max(scene_blur * 4.10, scene_radius * 0.58) * max(0.38, min(1.40, scene_x_scale))
            shadows.extend([
                f"0 0 {vw(max(4.0, scene_blur * 0.34))} rgba(255, 255, 248, {min(1.0, scene_alpha * 0.78):.3f})",
                f"0 0 {vw(scene_blur * 0.80)} rgba(255, 246, 205, {min(1.0, scene_alpha * 0.70):.3f})",
                f"0 0 {vw(scene_blur * 1.35)} rgba({lr}, {lg}, {lb}, {scene_alpha:.3f})",
                f"0 0 {vw(aura_blur)} rgba({lr}, {lg}, {lb}, {scene_alpha * 0.58:.3f})",
                f"0 0 {vw(wide_blur)} rgba({lr}, {lg}, {lb}, {scene_alpha * 0.34:.3f})",
            ])
            scene_light_filter_parts = [
                f"brightness({1.0 + scene_strength * 0.16:.3f})",
                f"drop-shadow(0 0 {vw(max(5.0, scene_blur * 0.58))} rgba(255, 255, 248, {min(1.0, scene_alpha * 0.72):.3f}))",
                f"drop-shadow(0 0 {vw(max(scene_blur * 1.35, scene_radius * 0.16))} rgba({lr}, {lg}, {lb}, {scene_alpha * 0.72:.3f}))",
                f"drop-shadow(0 0 {vw(max(scene_blur * 2.45, scene_radius * 0.30))} rgba({lr}, {lg}, {lb}, {scene_alpha * 0.42:.3f}))",
            ]

        text_shadow_css = f"text-shadow: {', '.join(shadows)};" if shadows else "text-shadow: none;"

        # Keep a crisp inner outline and feather the outside through text-shadow.
        hard_stroke_w = stroke_w * (1.0 - 0.42 * (stroke_softness / 100.0))
        stroke_css = f"-webkit-text-stroke: {vw(max(0.0, hard_stroke_w))} {stroke_c}; paint-order: stroke fill; stroke-linejoin: round; stroke-linecap: round;" if stroke_w > 0 else ""

        layout_font_scale = 1.0
        per_word_translate = 0.0
        word_margin_right = ws_vw
        if layout_mode in ("contrast", "triple"):
            if idx in emphasis_idx:
                layout_font_scale = emphasis_scale / 100.0
                per_word_translate = -0.035 if layout_mode == "contrast" else -0.04
                word_margin_right = vw(max(0, word_spacing * 0.55 + 1.4))
            elif idx in small_idx:
                layout_font_scale = contrast_small_scale if layout_mode == "contrast" else 0.80
                per_word_translate = 0.018 if layout_mode == "contrast" else 0.02
                word_margin_right = vw(max(0, word_spacing * 0.35 + 0.6))
            else:
                word_margin_right = vw(max(0, word_spacing * 0.45 + 1.0))
        elif layout_mode == "reel_stack":
            if head_letter_large_variant and idx == first_content_idx:
                layout_font_scale = 1.0
                per_word_translate = -0.02
                word_margin_right = vw(max(0, word_spacing * 0.28 + 0.7))
            elif idx in emphasis_idx:
                layout_font_scale = max(emphasis_scale / 100.0, 1.42 if layout_row_i == 0 else 1.62)
                per_word_translate = -0.035 if layout_row_i == 0 else -0.055
                word_margin_right = vw(max(0, word_spacing * 0.35 + 0.8))
            else:
                layout_font_scale = 0.72 if len(content_indices) > 4 else 0.82
                per_word_translate = 0.035
                word_margin_right = vw(max(0, word_spacing * 0.22 + 0.6))
        elif layout_mode == "random_focus":
            if idx in emphasis_idx:
                rank_boost = 0.18 if layout_row_i % 2 == 1 else 0.0
                layout_font_scale = max(emphasis_scale / 100.0, 1.32 + rank_boost)
                per_word_translate = -0.045
                word_margin_right = vw(max(0, word_spacing * 0.30 + 1.0))
            else:
                layout_font_scale = 0.70 + ((idx * 7) % 4) * 0.08
                per_word_translate = 0.025
                word_margin_right = vw(max(0, word_spacing * 0.18 + 0.55))
        elif layout_mode == "side_steps":
            side = -1 if layout_row_i % 2 == 0 else 1
            layout_font_scale = max(emphasis_scale / 100.0, 1.32)
            current_translate_x_em += side * axis_spread * (1.10 + (0.10 if layout_row_i % 3 == 0 else 0.0))
            per_word_translate = -0.02 * axis_gap
            word_margin_right = vw(max(0, word_spacing * 0.20 + 0.4))
        elif layout_mode == "axis_stack":
            if layout_variant == "axis-diagonal":
                center_row = (max(1, len(layout_rows)) - 1) / 2.0
                row_offset = layout_row_i - center_row
                current_translate_x_em += row_offset * axis_spread * 0.92
                per_word_translate += row_offset * (axis_gap - 1.0) * 0.08
                if abs(row_offset) < 0.4:
                    layout_font_scale = max(emphasis_scale / 100.0, 1.42)
                else:
                    layout_font_scale = max(0.62, min(0.88, contrast_small_scale + 0.06))
                word_margin_right = vw(max(0, word_spacing * 0.22 + 0.7))
            elif layout_row_len == 2:
                current_translate_x_em += (-0.72 if layout_pos_i == 0 else 0.72) * axis_spread
                layout_font_scale = max(emphasis_scale / 100.0, 1.18)
                per_word_translate += (layout_row_i - (max(1, len(layout_rows)) - 1) / 2.0) * (axis_gap - 1.0) * 0.06
                word_margin_right = vw(max(0, word_spacing * 0.35 + 1.2))
            elif idx in emphasis_idx:
                layout_font_scale = max(emphasis_scale / 100.0, 1.34)
                per_word_translate = -0.035 + (layout_row_i - (max(1, len(layout_rows)) - 1) / 2.0) * (axis_gap - 1.0) * 0.06
                word_margin_right = vw(max(0, word_spacing * 0.25 + 0.8))
            else:
                layout_font_scale = 0.88
                per_word_translate += (layout_row_i - (max(1, len(layout_rows)) - 1) / 2.0) * (axis_gap - 1.0) * 0.06
                word_margin_right = vw(max(0, word_spacing * 0.18 + 0.55))
        elif layout_mode == "quote_stack":
            if idx in emphasis_idx:
                layout_font_scale = max(emphasis_scale / 100.0, 1.38 if layout_row_i == 0 else 1.48)
                per_word_translate = -0.045 if layout_row_i == 0 else -0.055
                word_margin_right = vw(max(0, word_spacing * 0.32 + 0.85))
            else:
                layout_font_scale = 0.72 if layout_row_len >= 3 else 0.82
                per_word_translate = 0.025
                word_margin_right = vw(max(0, word_spacing * 0.18 + 0.52))
        elif layout_mode == "narrative_block":
            small_base = max(0.58, min(0.82, contrast_small_scale))
            large_base = max(emphasis_scale / 100.0, 1.50)
            mid_base = max(0.86, min(1.18, (small_base + large_base) * 0.50))
            visible_rows = max(1, len(layout_rows))
            pattern_slots = _layout_pattern_slots(layout_layer_pattern_raw)
            if pattern_slots:
                row_scales = tuple(_scale_from_slot(pattern_slots[i % len(pattern_slots)], small_base, mid_base, large_base) for i in range(visible_rows))
            elif visible_rows <= 1:
                row_scales = (max(1.08, min(1.28, large_base - 0.34)),)
            elif visible_rows == 2:
                row_scales = (large_base, small_base) if len(layout_content_indices) <= 5 else (small_base, large_base)
            elif visible_rows == 3:
                row_scales = (small_base, large_base, min(0.86, small_base + 0.12))
            else:
                row_scales = (small_base, large_base, min(0.84, small_base + 0.10), max(1.34, large_base - 0.16), small_base)
            layout_font_scale = row_scales[min(layout_row_i, len(row_scales) - 1)]
            if layout_row_i >= 1:
                current_translate_x_em += 0.12 if layout_row_i % 2 else 0.02
            word_margin_right = vw(max(0, word_spacing * 0.20 + (0.46 if layout_font_scale < 1.0 else 0.74)))
        elif layout_mode == "prayer_reflow":
            row_shift_patterns = {
                1: [0.0],
                2: [0.0, 1.12],
                3: [0.0, 1.20, 0.42],
                4: [0.0, 1.18, 0.42, 1.52],
                5: [0.0, 0.10],
                6: [0.0, 0.0, 0.0, 0.10],
                7: [0.0, 0.0, 0.0, 0.10],
            }
            visible_count = max(1, len(layout_content_indices))
            row_shifts = row_shift_patterns.get(visible_count, [0.0, 0.0, 0.0, 0.0, 0.12])
            current_translate_x_em += row_shifts[min(layout_row_i, len(row_shifts) - 1)]
            if idx in emphasis_idx:
                anchor_scale = 1.36 if visible_count <= 4 else 1.44
                if layout_row_i >= 2 or (visible_count == 5 and layout_row_i == 1):
                    anchor_scale += 0.16
                layout_font_scale = max(emphasis_scale / 100.0, anchor_scale)
                per_word_translate = -0.045
                word_margin_right = vw(max(0, word_spacing * 0.22 + 0.72))
            else:
                layout_font_scale = 0.60 if layout_row_len >= 3 else 0.66
                if visible_count <= 4 and layout_row_len == 1:
                    layout_font_scale = 0.70
                per_word_translate = 0.018
                word_margin_right = vw(max(0, word_spacing * 0.12 + 0.45))

        current_translate_em += per_word_translate
        if font_motion == "wave" and word_started:
            wave = math.sin(current_time * 5.2 + idx * 0.72)
            current_translate_em += wave * 0.055
            current_scale *= 1.0 + max(0.0, wave) * 0.018
        elif font_motion == "breathe" and word_started:
            breath = (math.sin(current_time * 1.8 + idx * 0.12) + 1.0) / 2.0
            current_scale *= 1.0 + breath * 0.055
        elif font_motion == "ripple3d" and word_started:
            ripple = math.sin(current_time * 3.45 + idx * 0.88)
            cross = math.cos(current_time * 2.75 + idx * 0.52)
            current_translate_em += ripple * 0.052
            current_translate_x_em += cross * 0.025
            current_scale *= 1.0 + ripple * 0.020
            current_rotate_y_deg += ripple * 6.0
            current_rotate_x_deg += cross * 2.4
            depth_shadow = f"{vw(3 + ripple * 2)} {vw(4 + cross * 1.5)} {vw(2.5)} rgba(0, 0, 0, 0.38)"
            if text_shadow_css == "text-shadow: none;":
                text_shadow_css = f"text-shadow: {depth_shadow};"
            else:
                text_shadow_css = text_shadow_css.rstrip(";") + f", {depth_shadow};"
            if current_filter_css == "filter: none;":
                current_filter_css = f"filter: drop-shadow({vw(cross * 2)} {vw(2 + ripple)} {vw(1.2)} rgba(255,255,255,0.16));"
        elif font_motion == "drift" and word_started:
            spread = idx - content_center
            drift_p = ease_in_out(clip_progress)
            current_translate_x_em += spread * 0.12 * drift_p
            current_translate_em += math.sin(idx * 1.37) * 0.035 * drift_p
        elif font_motion == "pulse" and word_started:
            pulse = max(0.0, math.sin(current_time * 8.0 + idx * 0.55))
            current_scale *= 1.0 + pulse * 0.11
            current_translate_em -= pulse * 0.025
        if typewriter_motion and word_started:
            seed = (idx * 37 + int(clip_start * 1000) * 13) % 17
            slide = 0.18 + (seed / 16.0) * 0.24
            current_translate_x_em += slide * (1.0 - typewriter_local_p)
            current_translate_em += (((seed * 5) % 7) - 3) * 0.006 * (1.0 - typewriter_local_p)
        if current_word_idx is not None and hl_motion in ("pop", "push"):
            distance = idx - current_word_idx
            active_word = words[current_word_idx]
            active_start = float(active_word.get("start", clip_start))
            active_end = float(active_word.get("end", active_start + 0.5))
            active_dur = max(0.06, active_end - active_start)
            local_p = ease_in_out((current_time - active_start) / min(max(active_dur, 0.12), 0.35))
            if distance == 0:
                target_scale = 1.055 + (0.055 if hl_motion == "push" else 0.035) * local_p
                current_scale = max(current_scale, target_scale)
                current_translate_em -= 0.006 * local_p
            elif hl_motion == "push" and abs(distance) == 1:
                current_translate_x_em += (0.16 + 0.055 * local_p) * (1 if distance > 0 else -1)
            elif hl_motion == "push" and abs(distance) == 2:
                current_translate_x_em += (0.060 + 0.025 * local_p) * (1 if distance > 0 else -1)
        if stable_word_boxes and not typewriter_motion:
            current_translate_x_em = 0.0
            if font_motion in ("wave", "drift"):
                current_translate_em = per_word_translate
            if anim_type == "pop":
                current_scale = min(current_scale, 1.025)

        if scene_light_filter_parts:
            scene_filter_css = " ".join(scene_light_filter_parts)
            if current_filter_css == "filter: none;":
                current_filter_css = f"filter: {scene_filter_css};"
            else:
                current_filter_css = current_filter_css.rstrip(";") + f" {scene_filter_css};"

        skewable_highlight = hl_style in ("box", "outline", "glow", "capsule", "canva_frame")
        hl_skew_transform = f" skewX({hl_bg_skew:.3f}deg)" if is_current and skewable_highlight and abs(hl_bg_skew) > 0.01 else ""
        word_base = (
            f"font-size: {layout_font_scale:.3f}em; "
            f"font-family: {_css_font_stack(word_font_family)}; "
            f"transform: perspective(720px) translate({current_translate_x_em:.3f}em, {current_translate_em:.3f}em) scale({current_scale:.3f}) rotateY({current_rotate_y_deg:.3f}deg) rotateX({current_rotate_x_deg:.3f}deg){hl_skew_transform}; "
            f"transform-origin: center center; transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1); "
            f"letter-spacing: calc({ls_vw} + {vw(current_letter_extra)}); "
            f"margin-right: {word_margin_right}; white-space: nowrap; overflow-wrap: normal; word-break: keep-all; "
            f"break-inside: avoid; page-break-inside: avoid; box-sizing: border-box; line-height: inherit; "
            f"vertical-align: baseline; will-change: transform, opacity; backface-visibility: hidden; "
            f"{current_filter_css} {current_clip_css}"
        )

        if is_hl_marked:
            if hl_trail_alpha >= 0.999:
                fill_color = c_hl
            else:
                chr_, chg, chb = hex_to_rgb(c_hl)
                fill_color = f"rgba({chr_}, {chg}, {chb}, {hl_trail_alpha:.3f})"
        else:
            fill_color = word_text_color
        if anim_type == "holy_breath" and is_holy_final_word and use_hl:
            fill_color = c_hl
        texture_css = ""
        texture_profiles = {
            "grain": (0.28, 0.18, "0.014em", "0.010em", "0.24em 0.22em", "0.32em 0.27em", 0.12),
            "noise": (0.42, 0.32, "0.010em", "0.007em", "0.16em 0.15em", "0.23em 0.21em", 0.08),
            "roughen": (0.66, 0.48, "0.040em", "0.025em", "0.38em 0.30em", "0.52em 0.42em", 0.26),
            "distressed": (0.86, 0.72, "0.052em", "0.032em", "0.46em 0.34em", "0.62em 0.48em", 0.38),
            "stacked_distress": (0.92, 0.78, "0.060em", "0.038em", "0.42em 0.31em", "0.58em 0.44em", 0.44),
        }
        if text_texture == "gold_metal":
            pos_a = f"{(idx * 17) % 31}% {(idx * 23) % 37}%"
            pos_b = f"{(idx * 29 + 11) % 43}% {(idx * 13 + 7) % 41}%"
            pos_c = f"{(idx * 19 + 5) % 47}% {(idx * 31 + 9) % 53}%"
            sweep_pos = ((float(current_time or 0) * 18.0 + idx * 11.0) % 190.0) - 45.0
            gold_layers = [
                ("linear-gradient(105deg, transparent 0 42%, rgba(255,255,255,0.58) 48%, rgba(255,244,183,0.28) 53%, transparent 62%)", "230% 100%", f"{sweep_pos:.2f}% 0"),
                ("repeating-linear-gradient(0deg, rgba(255,255,255,0.18) 0 0.020em, transparent 0.034em 0.145em)", "100% 0.34em", pos_a),
                ("repeating-linear-gradient(103deg, transparent 0 0.18em, rgba(95,49,4,0.26) 0.205em 0.222em, transparent 0.245em 0.42em)", "0.72em 0.58em", pos_b),
                ("radial-gradient(circle at 34% 42%, rgba(90,47,5,0.32) 0 0.012em, transparent 0.020em)", "0.22em 0.19em", pos_c),
                ("linear-gradient(180deg, #FFF9DA 0%, #FFE88D 12%, #D89A22 29%, #FFF0A6 45%, #B96A0C 62%, #F4BF47 80%, #7E4306 100%)", "100% 100%", "0 0"),
            ]
            texture_css = (
                f"-webkit-text-fill-color: transparent; "
                f"background-color: #F6C14A; "
                f"background-image: {', '.join(layer[0] for layer in gold_layers)}; "
                f"background-size: {', '.join(layer[1] for layer in gold_layers)}; "
                f"background-position: {', '.join(layer[2] for layer in gold_layers)}; "
                f"background-repeat: repeat; "
                f"-webkit-background-clip: text; background-clip: text; "
            )
        elif text_texture in texture_profiles:
            alpha_a, alpha_b, dot_a, dot_b, size_a, size_b, scratch_alpha = texture_profiles[text_texture]
            pos_a = f"{(idx * 17) % 31}% {(idx * 23) % 37}%"
            pos_b = f"{(idx * 29 + 11) % 43}% {(idx * 13 + 7) % 41}%"
            pos_c = f"{(idx * 19 + 5) % 47}% {(idx * 31 + 9) % 53}%"
            layers = [
                (f"radial-gradient(circle at 35% 45%, rgba(0,0,0,{alpha_a:.2f}) 0 {dot_a}, transparent calc({dot_a} + 0.006em))", size_a, pos_a),
                (f"radial-gradient(circle at 66% 28%, rgba(0,0,0,{alpha_b:.2f}) 0 {dot_b}, transparent calc({dot_b} + 0.005em))", size_b, pos_b),
                (f"repeating-linear-gradient(103deg, transparent 0 0.19em, rgba(0,0,0,{scratch_alpha:.2f}) 0.205em 0.222em, transparent 0.238em 0.42em)", "0.72em 0.58em", pos_b),
            ]
            if text_texture in ("noise", "stacked_distress"):
                layers.append((f"repeating-radial-gradient(circle at 30% 35%, rgba(0,0,0,{max(alpha_a - 0.08, 0.18):.2f}) 0 0.004em, transparent 0.006em 0.085em)", "0.19em 0.17em", pos_c))
            if text_texture in ("roughen", "distressed", "stacked_distress"):
                edge_alpha = 0.26 if text_texture == "roughen" else 0.34 if text_texture == "distressed" else 0.42
                layers.append((f"repeating-linear-gradient(8deg, rgba(0,0,0,{edge_alpha:.2f}) 0 0.018em, transparent 0.030em 0.155em)", "0.55em 0.36em", pos_c))
                layers.append((f"radial-gradient(ellipse at 52% 112%, rgba(0,0,0,{edge_alpha:.2f}) 0 0.055em, transparent 0.082em)", "0.34em 0.24em", pos_a))
            layers.append((f"linear-gradient({fill_color}, {fill_color})", "auto", "0 0"))
            texture_css = (
                f"-webkit-text-fill-color: transparent; "
                f"background-color: {fill_color}; "
                f"background-image: {', '.join(layer[0] for layer in layers)}; "
                f"background-size: {', '.join(layer[1] for layer in layers)}; "
                f"background-position: {', '.join(layer[2] for layer in layers)}; "
                f"background-repeat: repeat; "
                f"-webkit-background-clip: text; background-clip: text; "
            )

        back_layer_shadows = []
        if text_texture == "gold_metal" or (text_3d_enable and text_3d_depth > 0):
            if text_shadow_css.startswith("text-shadow: ") and text_shadow_css != "text-shadow: none;":
                back_layer_shadows.append(text_shadow_css[len("text-shadow: "):-1])
        if text_3d_enable and text_3d_depth > 0:
            tr, tg, tb = hex_to_rgb(text_3d_color)
            steps = max(2, min(24, int(text_3d_depth / 4) + 1))
            depth_scale = max(0.05, text_3d_depth / 100.0)
            base_x = text_3d_x if abs(text_3d_x) > 0.01 else 1.0
            base_y = text_3d_y if abs(text_3d_y) > 0.01 else 1.0
            extrude = []
            for step in range(steps, 0, -1):
                ratio = step / max(1, steps)
                sx = base_x * step * depth_scale
                sy = base_y * step * depth_scale
                alpha = 0.42 + 0.42 * ratio
                extrude.append(f"{vw(sx)} {vw(sy)} 0 rgba({tr}, {tg}, {tb}, {alpha:.3f})")
            extrude.append(f"{vw(base_x * steps * depth_scale * 1.10)} {vw(base_y * steps * depth_scale * 1.15)} {vw(max(2.0, text_3d_depth * 0.18))} rgba(0, 0, 0, 0.42)")
            back_layer_shadows = extrude + back_layer_shadows
        layered_text = bool(back_layer_shadows)
        front_text_shadow_css = text_shadow_css if scene_light_text_on else ("text-shadow: none;" if layered_text else text_shadow_css)
        back_text_shadow_css = f"text-shadow: {', '.join(back_layer_shadows)};" if back_layer_shadows else "text-shadow: none;"

        word_css_fg = f"display: inline-block; color: {fill_color}; opacity: {current_opacity:.3f}; {front_text_shadow_css} {stroke_css} {texture_css} {word_base}"
        word_css_bg = f"display: inline-block; color: transparent; -webkit-text-fill-color: transparent; text-shadow: none; -webkit-text-stroke: transparent; opacity: {current_opacity:.3f}; {word_base}"
        layered_shell_css = f"display: inline-block; position: relative; opacity: {current_opacity:.3f}; {word_base}"
        layered_front_css = f"display: inline-block; position: relative; z-index: 2; color: {fill_color}; {front_text_shadow_css} {stroke_css} {texture_css}"
        br, bgc, bb = hex_to_rgb(text_3d_color)
        layered_back_css = f"position: absolute; left: 0; top: 0; z-index: 1; pointer-events: none; color: rgba({br}, {bgc}, {bb}, 0.01); -webkit-text-fill-color: rgba({br}, {bgc}, {bb}, 0.01); -webkit-text-stroke: transparent; {back_text_shadow_css}"

        effective_hl_bg_a = max(0.0, min(1.0, hl_bg_a * hl_trail_alpha))
        if bg_mode == "tape":
            if is_hl_marked and effective_hl_bg_a > 0:
                tape_shadow_a = max(0.0, min(0.25, 0.25 * hl_trail_alpha))
                hl_css = f" background-color: rgba({hl_r}, {hl_g}, {hl_b}, {effective_hl_bg_a:.3f}); border-radius: {hl_rad_vw}; box-shadow: 0 0 0 {hl_spread_vw} rgba({hl_r}, {hl_g}, {hl_b}, {effective_hl_bg_a:.3f}), 0 {vw(3)} {vw(10)} rgba({hl_r}, {hl_g}, {hl_b}, {tape_shadow_a:.3f});"
                word_css_fg += hl_css
                word_css_bg += f" background-color: transparent; border-radius: {hl_rad_vw};"
        elif bg_mode == "block" and is_hl_marked and effective_hl_bg_a > 0:
            word_css_fg += f" background-color: rgba({hl_r}, {hl_g}, {hl_b}, {effective_hl_bg_a:.3f}); border-radius: {hl_rad_vw}; box-shadow: 0 0 0 {hl_spread_vw} rgba({hl_r}, {hl_g}, {hl_b}, {effective_hl_bg_a:.3f});"
        if is_hl_marked and bg_mode != "tape":
            outline_a = max(0.0, min(1.0, max(0.72, min(1.0, hl_bg_a)) * hl_trail_alpha))
            box_a = max(0.0, min(1.0, max(0.18, hl_bg_a) * hl_trail_alpha))
            outline_spread = bg_vw(max(2, min(10, hl_pad or 4)))
            underline_h = bg_vw(max(2, min(10, hl_pad_bottom or hl_pad or 4)))
            underline_offset = bg_vw(max(4, min(18, hl_pad_bottom + 4)))
            if hl_style == "box":
                word_css_fg += f" background-color: rgba({hl_r}, {hl_g}, {hl_b}, {box_a:.3f}); border-radius: {hl_rad_vw}; box-shadow: 0 0 0 {hl_spread_vw} rgba({hl_r}, {hl_g}, {hl_b}, {box_a:.3f});"
            elif hl_style == "underline":
                word_css_fg += f" background-image: linear-gradient(rgba({hl_r}, {hl_g}, {hl_b}, {outline_a:.3f}), rgba({hl_r}, {hl_g}, {hl_b}, {outline_a:.3f})); background-repeat: no-repeat; background-size: 100% {underline_h}; background-position: 0 calc(100% + {underline_offset});"
            elif hl_style == "glow":
                glow_a1 = max(0.0, min(1.0, 0.72 * hl_trail_alpha))
                glow_a2 = max(0.0, min(1.0, 0.42 * hl_trail_alpha))
                stroke_a = max(0.0, min(1.0, 0.82 * hl_trail_alpha))
                word_css_fg += f" filter: drop-shadow(0 0 {bg_vw(max(8, glow_size))} rgba({hl_r}, {hl_g}, {hl_b}, {glow_a1:.3f})) drop-shadow(0 0 {bg_vw(max(16, glow_size * 1.7))} rgba({hl_r}, {hl_g}, {hl_b}, {glow_a2:.3f})); -webkit-text-stroke: {bg_vw(max(1.0, hl_pad * 0.18))} rgba({hl_r}, {hl_g}, {hl_b}, {stroke_a:.3f});"
            elif hl_style == "capsule":
                halo_a = max(0.0, min(1.0, 0.25 * hl_trail_alpha))
                word_css_fg += f" background-color: rgba({hl_r}, {hl_g}, {hl_b}, {box_a:.3f}); border-radius: 999px; box-shadow: 0 0 0 {hl_spread_vw} rgba({hl_r}, {hl_g}, {hl_b}, {box_a:.3f}), inset 0 0 0 {bg_vw(max(1.2, hl_pad * 0.18))} rgba({hl_r}, {hl_g}, {hl_b}, {outline_a:.3f}), 0 0 {bg_vw(max(8, hl_pad * 1.2))} rgba({hl_r}, {hl_g}, {hl_b}, {halo_a:.3f});"
            elif hl_style in ("outline", "canva_frame"):
                word_css_fg += f" border-radius: {hl_rad_vw}; box-shadow: 0 0 0 {outline_spread} rgba({hl_r}, {hl_g}, {hl_b}, {outline_a:.3f});"

        safe_txt = html_text(clean_txt)
        safe_txt_bg = safe_txt
        if head_letter_large_variant and idx == first_content_idx and not typewriter_motion:
            initial_match = re.search(r"[A-Za-z0-9\u4e00-\u9fff]", clean_txt)
            if initial_match:
                initial_pos = initial_match.start()
                initial_scale = max(emphasis_scale / 100.0, 1.58)
                safe_txt = (
                    f"{html_text(clean_txt[:initial_pos])}"
                    f"<span style=\"display:inline-block; font-size:{initial_scale:.3f}em; "
                    f"line-height:0.78; vertical-align:-0.04em; margin-right:0.018em;\">"
                    f"{html_text(clean_txt[initial_pos])}</span>"
                    f"{html_text(clean_txt[initial_pos + 1:])}"
                )
                safe_txt_bg = safe_txt
        if layered_text:
            html_words_fg.append(
                f"<span style='{layered_shell_css}'>"
                f"<span aria-hidden='true' style='{layered_back_css}'>{safe_txt_bg}</span>"
                f"<span style='{layered_front_css}'>{safe_txt}</span>"
                f"</span>"
            )
        else:
            html_words_fg.append(f"<span style='{word_css_fg}'>{safe_txt}</span>")
        html_words_bg.append(f"<span style='{word_css_bg}'>{safe_txt_bg}</span>")

        if idx < len(words) - 1:
            next_raw = str(words[idx + 1].get("text") or words[idx + 1].get("word") or "")
            next_is_visible = True
            if typewriter_motion:
                next_reveal_start = _typewriter_reveal_start_for(idx + 1)
                next_is_visible = current_time >= next_reveal_start
            if next_is_visible and "\n" not in next_raw and not _layout_breaks_before(idx + 1):
                spacer = "<span style='display:inline-block; width:0.14em;'></span>" if layout_mode in ("contrast", "triple", "reel_stack", "random_focus", "side_steps", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block") else " "
                html_words_fg.append(spacer)
                if bg_mode in ("tape", "canva_fit", "canva_joined", "block", "sweep"):
                    html_words_bg.append(spacer if layout_mode in ("contrast", "triple", "reel_stack", "random_focus", "side_steps", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block") else " ")

    inner_html_fg = "".join(html_words_fg)
    inner_html_bg = "".join(html_words_bg)

    def _split_html_lines(html_value):
        lines = []
        current = []
        for part in str(html_value or "").split("<br>"):
            clean_part = part.strip()
            if clean_part:
                lines.append(clean_part)
        return lines

    inner_transform_parts = []
    inner_extra_css = ""
    if typewriter_motion and typewriter_center_push_em > 0.001:
        inner_transform_parts.append(f"translateX({typewriter_center_push_em:.3f}em)")
    elif typewriter_motion and typewriter_group_shift_em > 0.001:
        inner_transform_parts.append(f"translateX({typewriter_group_shift_em:.3f}em)")
    if anim_type == "roll_up":
        y_offset = (1.0 - clip_progress * 2) * 50
        inner_transform_parts.append(f"translateY({y_offset}vh)")
    elif anim_type == "slam_in":
        p = ease_out_cubic((current_time - clip_start) / max(0.05, pop_speed * 1.8))
        if p < 1.0:
            overshoot = math.sin(p * math.pi) * (0.10 + max(0, pop_bounce - 100) / 100.0 * 0.12)
            slam_scale = max(1.0, 4.2 - 3.2 * p + overshoot)
            slam_y = -18.0 * (1.0 - p)
            slam_rot = -7.0 * (1.0 - p)
            blur = 7.0 * (1.0 - p)
            inner_transform_parts.extend([
                f"translateY({slam_y:.3f}vh)",
                f"scale({slam_scale:.3f})",
                f"rotate({slam_rot:.3f}deg)",
            ])
            inner_extra_css = f"opacity: {p:.3f}; filter: blur({vw(blur)});"
        else:
            inner_transform_parts.append("scale(1)")
    elif anim_type == "camera_push":
        intro_p = ease_out_cubic((current_time - clip_start) / max(0.05, pop_speed * 1.4))
        push_p = ease_in_out(clip_progress)
        push_scale = (0.72 + 0.62 * push_p) * (0.86 + 0.14 * intro_p)
        push_y = 5.5 * (1.0 - intro_p)
        blur = 4.5 * (1.0 - intro_p)
        opacity = 0.35 + 0.65 * intro_p
        inner_transform_parts.extend([
            f"translateY({push_y:.3f}vh)",
            f"scale({push_scale:.3f})",
        ])
        inner_extra_css = f"opacity: {opacity:.3f}; filter: blur({vw(blur)});"
    elif anim_type == "depth_push":
        intro_p = ease_out_cubic((current_time - clip_start) / max(0.05, pop_speed * 1.8))
        breathe = math.sin(current_time * 2.35)
        sway = math.sin(current_time * 1.85 + 0.7)
        depth_scale = (0.42 + 0.72 * intro_p) * (1.0 + breathe * 0.022)
        depth_z = -220.0 * (1.0 - intro_p)
        depth_y = 6.5 * (1.0 - intro_p)
        rot_y = 12.0 * (1.0 - intro_p) + sway * 5.2
        rot_x = -3.5 * (1.0 - intro_p) + breathe * 1.4
        blur = 6.0 * (1.0 - intro_p)
        opacity = 0.18 + 0.82 * intro_p
        inner_transform_parts.extend([
            "perspective(900px)",
            f"translateY({depth_y:.3f}vh)",
            f"translateZ({depth_z:.1f}px)",
            f"scale({depth_scale:.3f})",
            f"rotateY({rot_y:.3f}deg)",
            f"rotateX({rot_x:.3f}deg)",
        ])
        inner_extra_css = f"opacity: {opacity:.3f}; filter: blur({vw(blur)}) drop-shadow({vw(8)} {vw(5)} {vw(1)} rgba(0,0,0,0.48)); transform-style: preserve-3d;"
    inner_transform = ""
    if inner_transform_parts:
        transform_origin = "left center" if stable_left_box_mode or align == "left" else "center center"
        inner_transform = f"transform: {' '.join(inner_transform_parts)}; transform-origin: {transform_origin}; {inner_extra_css}"

    # 👑 新增平滑边缘及抗锯齿
    layout_spacing_modes = ("contrast", "triple", "reel_stack", "random_focus", "side_steps", "axis_stack", "quote_stack", "prayer_reflow", "narrative_block")
    if layout_mode in layout_spacing_modes:
        lh = max(0.6, min(3.0, _safe_float(lh, 1.1) * layout_row_gap))

    base_wrapper_css = f"""
        font-family: {_css_font_stack(f_fam)};
        font-size: {size_vw};
        font-weight: {f_weight};
        font-style: {f_style};
        letter-spacing: {ls_vw};
        word-spacing: {('0vw' if layout_mode in ('contrast', 'triple', 'reel_stack', 'random_focus', 'side_steps', 'axis_stack', 'quote_stack', 'prayer_reflow', 'narrative_block') else ws_vw)};
        text-transform: none;
        box-sizing: border-box;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        text-rendering: optimizeLegibility;
        text-wrap: normal;
        overflow-wrap: normal;
        word-break: normal;
        white-space: normal;
    """

    if center_left_mode:
        align = "left"
    j_map = {"center": "center", "left": "start", "right": "end", "justify": "center"}
    align_item = j_map.get(align, "center")
    if stable_left_box_mode:
        width_value = f"{(box_width if box_width > 0 else 74.0):.4f}vw"
        width_css = f"width: {width_value}; max-width: 120vw;"
    elif box_width > 0:
        width_value = f"{box_width:.4f}vw"
        if box_layout == "fixed":
            width_css = f"width: {width_value}; max-width: 120vw;"
        else:
            width_css = f"max-width: {width_value}; width: fit-content;"
    else:
        width_css = "width: max-content; max-width: 120vw;"

    mask_css = ""
    if mask_en:
        mask_css = f"-webkit-mask-image: linear-gradient(to bottom, transparent 0%, black {mask_top}%, black {100-mask_bot}%, transparent 100%); mask-image: linear-gradient(to bottom, transparent 0%, black {mask_top}%, black {100-mask_bot}%, transparent 100%);"

    height_css = f"max-height: {box_height:.4f}vh;" if box_height > 0 else ""
    line_guard_css = ""
    if layout_mode in ("standard", "contrast", "narrative_block") and max_lines > 0:
        line_guard_css = f"--sub-max-lines: {max_lines};"
    overflow_css = "hidden" if box_height > 0 else "visible"
    outer_box_style = f"{width_css} {height_css} {line_guard_css} margin: 0 auto; outline: none; text-align: {align}; position: relative; {mask_css} transform: rotate({rot}deg); overflow: {overflow_css}; transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);"

    if bg_mode == "tape":
        fg_layer_css = base_wrapper_css + f"""
            display: inline;
            background-color: rgba({r}, {g}, {b}, {bg_a});
            border-radius: {rad_vw};
            padding: {pad_y} {pad_x};
            -webkit-box-decoration-break: clone;
            box-decoration-break: clone;
            line-height: {max(0.6, float(lh))};
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; display: flex; justify-content: {align_item}; text-align: {align};">
                <span style="{fg_layer_css}">{inner_html_fg}</span>
            </div>
        </div>
        """
    elif bg_mode == "canva_fit":
        fit_outline_a = max(0.0, min(1.0, hl_bg_a))
        fit_outline_vw = bg_vw(max(1.5, min(8.0, float(hl_pad or 6) * 0.42)))
        fit_line_shadow = f"box-shadow: 0 0 0 {fit_outline_vw} rgba({hl_r}, {hl_g}, {hl_b}, {fit_outline_a:.3f});" if hl_style == "canva_frame" else ""
        fit_line_css = base_wrapper_css + f"""
            display: inline;
            background-color: rgba({r}, {g}, {b}, {bg_a});
            border-radius: {rad_vw};
            padding: {pad_top_vw} {pad_right_vw} {pad_bottom_vw} {pad_left_vw};
            {fit_line_shadow}
            line-height: {max(0.8, float(lh))};
            white-space: normal;
            overflow-wrap: normal;
            word-break: normal;
            background-clip: padding-box;
            box-sizing: border-box;
            -webkit-box-decoration-break: clone;
            box-decoration-break: clone;
        """
        fit_lines = _split_html_lines(inner_html_fg)
        if fit_lines:
            line_gap = bg_vw(max(3.0 if len(fit_lines) > 1 else 0.0, (float(lh) - 1.0) * float(size) * 0.34))
            fit_html = "".join(
                f"<div style=\"display:block; text-align:{align}; width:100%; max-width:100%; margin:{line_gap} 0;\">"
                f"<span style=\"{fit_line_css}\">{line_html}</span></div>"
                for line_html in fit_lines
            )
        else:
            fit_html = ""
        final_html = f"""
        <div class='sub-box canva-fit-bg' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; max-width: 100%; display: block; text-align: {align};">
                {fit_html}
            </div>
        </div>
        """
    elif bg_mode == "canva_joined":
        joined_fg_lines = _split_html_lines(inner_html_fg)
        joined_bg_lines = _split_html_lines(inner_html_bg)
        joined_lh = max(0.82, float(lh))
        joined_justify = {"center": "center", "left": "flex-start", "right": "flex-end", "justify": "center"}.get(align, "center")
        joined_radius_vw = bg_vw(max(8.0, float(rad)))
        joined_pad_top = bg_vw(max(1.0, float(pad_top) * 0.38))
        joined_pad_bottom = bg_vw(max(float(pad_bottom) * 1.22, float(pad_top) * 0.76 + float(size) * 0.145))
        joined_pad_left = bg_vw(max(float(pad_left), float(pad) * 0.70))
        joined_pad_right = bg_vw(max(float(pad_right), float(pad) * 0.70))
        joined_text_raise = -max(1.2, min(16.0, float(size) * 0.038 + float(pad_top) * 0.09))
        joined_bg_down = max(1.2, min(22.0, float(size) * 0.055 + float(pad_bottom) * 0.16))
        joined_next_up = max(0.0, min(34.0, float(size) * 0.132 + float(pad_top) * 0.30 + float(pad_bottom) * 0.23))
        joined_line_gap = bg_vw(max(0.0, (float(lh) - 1.0) * float(size) * 0.16))
        joined_outline_a = max(0.0, min(1.0, hl_bg_a))
        joined_outline_vw = bg_vw(max(1.5, min(8.0, float(hl_pad or 6) * 0.42)))
        joined_shadow_css = f"box-shadow: 0 {vw(2)} {vw(8)} rgba(0, 0, 0, 0.22);"
        if hl_style == "canva_frame":
            joined_shadow_css = f"box-shadow: 0 0 0 {joined_outline_vw} rgba({hl_r}, {hl_g}, {hl_b}, {joined_outline_a:.3f}), 0 {vw(2)} {vw(8)} rgba(0, 0, 0, 0.22);"

        joined_bg_rows = []
        joined_fg_rows = []
        for line_i, line_html in enumerate(joined_fg_lines):
            bg_html = joined_bg_lines[line_i] if line_i < len(joined_bg_lines) else ""
            row_css = f"display:flex; justify-content:{joined_justify}; width:100%; max-width:100%; margin:{joined_line_gap} 0; line-height:{joined_lh};"
            line_join_scale = 0.0 if line_i <= 0 else min(1.72, 1.0 + line_i * 0.24)
            bg_shift = joined_bg_down - joined_next_up * line_join_scale
            bg_line_css = base_wrapper_css + f"""
                display: inline-block;
                background-color: rgb({r}, {g}, {b});
                border-radius: {joined_radius_vw};
                padding: {joined_pad_top} {joined_pad_right} {joined_pad_bottom} {joined_pad_left};
                {joined_shadow_css}
                line-height: {joined_lh};
                white-space: normal;
                overflow-wrap: normal;
                word-break: normal;
                color: transparent;
                -webkit-text-fill-color: transparent;
                text-shadow: none;
                -webkit-text-stroke: transparent;
                background-clip: padding-box;
                box-sizing: border-box;
                transform: translateY({bg_vw(bg_shift)});
                will-change: transform;
            """
            fg_line_css = base_wrapper_css + f"""
                display: inline-block;
                position: relative;
                z-index: 2;
                line-height: {joined_lh};
                white-space: normal;
                overflow-wrap: normal;
                word-break: normal;
                transform: translateY({bg_vw(joined_text_raise)});
                will-change: transform;
            """
            if bg_html:
                joined_bg_rows.append(f"<div class='canva-joined-bg-row' style=\"{row_css}\"><span style=\"{bg_line_css}\">{bg_html}</span></div>")
            joined_fg_rows.append(f"<div class='canva-joined-fg-row' style=\"{row_css}\"><span style=\"{fg_line_css}\">{line_html}</span></div>")

        final_html = f"""
        <div class='sub-box canva-joined-bg' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; max-width: 100%; display: block; text-align: {align}; position: relative; isolation: isolate; overflow: visible;">
                <div class='canva-joined-bg-stack' aria-hidden='true' style="position:absolute; inset:0; z-index:1; opacity:{bg_a:.3f}; pointer-events:none; isolation:isolate; overflow:visible;">
                    {''.join(joined_bg_rows)}
                </div>
                <div class='canva-joined-fg-stack' style="position:relative; z-index:2; width:100%; display:block; text-align:{align}; overflow:visible;">
                    {''.join(joined_fg_rows)}
                </div>
            </div>
        </div>
        """
    elif bg_mode == "block":
        wrapper_css = base_wrapper_css + f"""
            display: inline-block;
            background-color: rgba({r}, {g}, {b}, {bg_a});
            border-radius: {rad_vw};
            padding: {pad_y} {pad_x};
            text-align: {align};
            line-height: {max(0.8, float(lh))};
            width: 100%;
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%;"><div style="{wrapper_css}">{inner_html_fg}</div></div>
        </div>
        """
    elif bg_mode == "full_frame":
        frame_wrap_css = base_wrapper_css + f"""
            display: inline-block;
            line-height: {max(0.8, float(lh))};
            white-space: normal;
            overflow-wrap: normal;
            word-break: normal;
            background-color: rgba({r}, {g}, {b}, {bg_a});
            border-radius: {rad_vw};
            padding: {pad_top_vw} {pad_right_vw} {pad_bottom_vw} {pad_left_vw};
            text-align: {align};
            max-width: 100%;
            box-sizing: border-box;
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; display:flex; justify-content:{align_item}; text-align:{align};">
                <div style="{frame_wrap_css}">{inner_html_fg}</div>
            </div>
        </div>
        """
    elif bg_mode == "cinematic_frame":
        cinema_float_y = math.sin(current_time * 0.82 + clip_start * 0.37) * 0.055
        cinema_glow_p = 0.72 + 0.28 * math.sin(current_time * 1.05 + clip_start * 0.21)
        glass_a = max(0.10, min(0.34, bg_a * 0.34))
        veil_a = max(0.035, min(0.15, bg_a * 0.12))
        edge_a = max(0.13, min(0.36, 0.17 + hl_bg_a * 0.13))
        aura_a = max(0.09, min(0.26, 0.12 + bg_a * 0.10)) * cinema_glow_p
        warm_a = max(0.08, min(0.22, 0.11 + bg_a * 0.08)) * cinema_glow_p
        glass_radius_vw = vw(max(22, rad))
        glass_inner_radius_vw = vw(max(18, max(22, rad) * 0.86))
        glass_pad_top = vw(max(pad_top, pad / 2.0 + 8))
        glass_pad_right = vw(max(pad_right, pad + 18))
        glass_pad_bottom = vw(max(pad_bottom, pad / 2.0 + 9))
        glass_pad_left = vw(max(pad_left, pad + 18))
        frame_wrap_css = base_wrapper_css + f"""
            display: inline-block;
            position: relative;
            isolation: isolate;
            line-height: {max(0.8, float(lh))};
            white-space: normal;
            overflow-wrap: normal;
            word-break: normal;
            background:
                radial-gradient(ellipse at 18% 0%, rgba(255, 244, 218, {warm_a:.3f}) 0%, transparent 58%),
                radial-gradient(ellipse at 82% 100%, rgba(255, 205, 155, {veil_a:.3f}) 0%, transparent 62%),
                linear-gradient(135deg, rgba(255, 255, 255, {glass_a + 0.055:.3f}) 0%, rgba({r}, {g}, {b}, {glass_a:.3f}) 48%, rgba(255, 230, 195, {veil_a:.3f}) 100%);
            border: {vw(1.2)} solid rgba(255, 246, 224, {edge_a:.3f});
            border-radius: {glass_radius_vw};
            padding: {glass_pad_top} {glass_pad_right} {glass_pad_bottom} {glass_pad_left};
            text-align: {align};
            max-width: 100%;
            box-sizing: border-box;
            transform: translateY({cinema_float_y:.3f}em);
            box-shadow:
                0 0 {vw(16)} rgba(255, 229, 178, {edge_a * 0.42:.3f}),
                0 {vw(12)} {vw(38)} rgba(24, 18, 10, {0.18 + bg_a * 0.10:.3f}),
                0 0 {vw(48)} rgba(255, 214, 158, {aura_a:.3f}),
                inset 0 0 {vw(22)} rgba(255, 255, 255, 0.115),
                inset 0 {vw(1.2)} {vw(0.6)} rgba(255, 255, 255, 0.30);
            -webkit-backdrop-filter: blur({vw(14)}) saturate(1.16);
            backdrop-filter: blur({vw(14)}) saturate(1.16);
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; display:flex; justify-content:{align_item}; text-align:{align};">
                <div style="{frame_wrap_css}">
                    <div style="position:absolute; inset:{vw(-5)}; z-index:-1; border-radius:inherit; pointer-events:none; background:radial-gradient(ellipse at 50% 50%, rgba(255, 230, 186, {aura_a:.3f}) 0%, rgba(255, 230, 186, {aura_a * 0.30:.3f}) 42%, transparent 72%); filter: blur({vw(18)}); opacity:{0.72 + cinema_glow_p * 0.18:.3f};"></div>
                    <div style="position:absolute; inset:{vw(1.5)}; z-index:0; border-radius:{glass_inner_radius_vw}; pointer-events:none; background:linear-gradient(115deg, rgba(255,255,255,0.16), transparent 28%, transparent 68%, rgba(255,226,188,0.10));"></div>
                    <div style="position:relative; z-index:1;">{inner_html_fg}</div>
                </div>
            </div>
        </div>
        """
    elif bg_mode == "sweep":
        bg_layer_css = base_wrapper_css + f"""
            display: inline;
            background-color: rgb({r}, {g}, {b});
            border-radius: {rad_vw};
            padding: {pad_y} {pad_x};
            -webkit-box-decoration-break: clone;
            box-decoration-break: clone;
            line-height: {max(0.8, float(lh))};
        """
        fg_layer_css = base_wrapper_css + f"""
            display: inline;
            background-color: transparent;
            border-radius: {rad_vw};
            padding: {pad_y} {pad_x};
            -webkit-box-decoration-break: clone;
            box-decoration-break: clone;
            line-height: {max(0.8, float(lh))};
            background: linear-gradient(to right, {hl_bg_col} {whole_sub_progress}%, {c_txt} {whole_sub_progress}%);
            -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; text-fill-color: transparent;
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%; display: grid; grid-template-columns: 1fr; grid-template-rows: 1fr; justify-items: {align_item}; align-items: center; text-align: {align};">
                <div style="grid-area: 1/1; opacity: {bg_a}; z-index: 1; width: 100%;"><span style="{bg_layer_css}">{inner_html_bg if inner_html_bg else inner_html_fg}</span></div>
                <div style="grid-area: 1/1; z-index: 2; width: 100%;"><span style="{fg_layer_css}">{inner_html_fg}</span></div>
            </div>
        </div>
        """
    else:
        wrapper_css = base_wrapper_css + f"""
            display: inline-block;
            text-align: {align};
            line-height: {max(0.8, float(lh))};
            width: 100%;
        """
        final_html = f"""
        <div class='sub-box' style='{outer_box_style}'>
            <div style="{inner_transform} width: 100%;"><div style="{wrapper_css}">{inner_html_fg}</div></div>
        </div>
        """

    if anim_type == "full_text_roll":
        roll_y = full_roll_start_y + (full_roll_end_y - full_roll_start_y) * full_roll_progress
        roll_mask_css = ""
        if full_roll_feather > 0:
            roll_mask_css = (
                f"-webkit-mask-image: linear-gradient(to bottom, transparent 0%, black {full_roll_feather:.3f}%, black {100.0 - full_roll_feather:.3f}%, transparent 100%); "
                f"mask-image: linear-gradient(to bottom, transparent 0%, black {full_roll_feather:.3f}%, black {100.0 - full_roll_feather:.3f}%, transparent 100%);"
            )
        final_html = f"""
        <div class='sub-full-roll-window' style='position: relative; width: 100%; height: {full_roll_window_height:.3f}vh; overflow: hidden; display: flex; align-items: flex-start; justify-content: center; pointer-events: none; {roll_mask_css}'>
            <div class='sub-full-roll-inner' style='width: 100%; transform: translateY({roll_y:.3f}vh); transform-origin: center top; will-change: transform;'>
                {final_html}
            </div>
        </div>
        """

    if anim_type == "wipe_right":
        reveal_pct = ease_out_cubic(clip_progress) * 100.0
        hidden_pct = max(0.0, 100.0 - reveal_pct)
        final_html = f"""
        <div class='sub-wipe-wrap' style='position: relative; display: inline-block; max-width: 100%;'>
            <div style='-webkit-clip-path: inset(0 {hidden_pct:.3f}% 0 0); clip-path: inset(0 {hidden_pct:.3f}% 0 0);'>
                {final_html}
            </div>
        </div>
        """

    return final_html


def render_signature_html(signature, current_time, proj_w=1080, proj_h=None):
    config = normalize_signature_config(signature)
    text = str(config.get("text", "") or "").strip()
    if not config.get("enabled") or not text:
        return ""

    style = default_signature_style(None, scale_from_subtitle=False)
    if isinstance(config.get("style"), dict):
        style.update(config.get("style", {}))
    placement = str(config.get("placement", "top_right") or "top_right")
    margin_x = max(0.0, min(45.0, float(config.get("margin_x", 5.0) or 0.0)))
    margin_y = max(0.0, min(45.0, float(config.get("margin_y", 4.0) or 0.0)))
    pos_x = max(-100.0, min(100.0, float(config.get("pos_x", 0.0) or 0.0)))
    pos_y = max(-100.0, min(100.0, float(config.get("pos_y", -42.0) or 0.0)))

    align = str(style.get("text_align", "center") or "center") if placement == "custom" else "right" if "right" in placement else "left" if "left" in placement else "center"
    style["text_align"] = align
    style["anim_type"] = "none"
    style["font_motion"] = "none"
    style["use_hl"] = False

    end_time = max(float(current_time or 0.0) + 1.0, 1.0)
    sig_sub = {
        "text": text,
        "start": 0.0,
        "end": end_time,
        "words": [{"text": text, "start": 0.0, "end": end_time}],
        "style": style,
    }
    inner_html = render_subtitle_html(sig_sub, current_time, proj_w, proj_h)

    if placement == "top_left":
        pos_css = f"left:{margin_x:.3f}%; top:{margin_y:.3f}%; text-align:left;"
    elif placement == "bottom_right":
        pos_css = f"right:{margin_x:.3f}%; bottom:{margin_y:.3f}%; text-align:right;"
    elif placement == "bottom_left":
        pos_css = f"left:{margin_x:.3f}%; bottom:{margin_y:.3f}%; text-align:left;"
    elif placement == "top_center":
        pos_css = f"left:50%; top:{margin_y:.3f}%; transform:translateX(-50%); text-align:center;"
    elif placement == "bottom_center":
        pos_css = f"left:50%; bottom:{margin_y:.3f}%; transform:translateX(-50%); text-align:center;"
    elif placement == "custom":
        pos_css = f"left:calc(50% + {pos_x:.3f}%); top:calc(50% + {pos_y:.3f}%); transform:translate(-50%, -50%); text-align:{align};"
    else:
        pos_css = f"right:{margin_x:.3f}%; top:{margin_y:.3f}%; text-align:right;"

    return f"""
    <div class="signature-overlay" style="position:absolute; {pos_css} z-index:90; max-width:72%; pointer-events:none; box-sizing:border-box;">
        {inner_html}
    </div>
    """


def render_design_html(design_state, current_time, proj_w=1080, proj_h=1920):
    state = normalize_design_room_state(design_state)
    pages = state.get("pages", [])
    if not pages:
        return ""

    t = max(0.0, float(current_time or 0.0))
    cursor = 0.0
    active_page = None
    page_local_time = 0.0
    for page in pages:
        dur = max(0.1, float(page.get("duration", 5.0) or 5.0))
        if cursor <= t < cursor + dur:
            active_page = page
            page_local_time = t - cursor
            break
        cursor += dur
    if active_page is None:
        return ""

    design_w = max(1.0, float(state.get("width", proj_w) or proj_w))
    design_h = max(1.0, float(state.get("height", proj_h) or proj_h))

    layer_html = []
    layers = sorted(
        active_page.get("layers", []) or [],
        key=lambda item: int(item.get("zIndex", 0) or 0)
    )
    page_dur = max(0.1, float(active_page.get("duration", 5.0) or 5.0))
    for layer in layers:
        start = max(0.0, float(layer.get("start", 0.0) or 0.0))
        end = float(layer.get("end", 0.0) or 0.0)
        if end <= 0:
            end = page_dur
        if not (start <= page_local_time < end):
            continue

        x_pct = float(layer.get("x", 0) or 0) * 100.0 / design_w
        y_pct = float(layer.get("y", 0) or 0) * 100.0 / design_h
        w_pct = max(0.01, float(layer.get("width", 1) or 1) * 100.0 / design_w)
        h_pct = max(0.01, float(layer.get("height", 1) or 1) * 100.0 / design_h)
        opacity = max(0.0, min(1.0, float(layer.get("opacity", 1) or 0)))
        rot = float(layer.get("rotation", 0) or 0)
        common = (
            f"position:absolute; left:{x_pct:.5f}%; top:{y_pct:.5f}%; "
            f"width:{w_pct:.5f}%; min-height:{h_pct:.5f}%; opacity:{opacity:.3f}; "
            f"transform:rotate({rot:.3f}deg); transform-origin:center center; "
            f"box-sizing:border-box; pointer-events:none;"
        )
        if layer.get("type") == "rect":
            fill = html_attr(layer.get("fill", "#000000") or "#000000")
            radius = float(layer.get("cornerRadius", 0) or 0) * 100.0 / design_w
            layer_html.append(
                f"<div style='{common} height:{h_pct:.5f}%; background:{fill}; border-radius:{radius:.5f}vw;'></div>"
            )
            continue
        if layer.get("type") == "image":
            src = html_attr(design_image_source(layer))
            if not src:
                continue
            fit = str(layer.get("fit", "cover") or "cover").strip().lower()
            object_fit = "fill" if fit == "stretch" else ("contain" if fit == "contain" else "cover")
            layer_html.append(
                f"<img src='{src}' style='{common} height:{h_pct:.5f}%; object-fit:{object_fit}; display:block;' />"
            )
            continue

        text = html_multiline_text(layer.get("text", "") or "")
        if not text:
            continue
        font_size = float(layer.get("fontSize", 48) or 48) * 100.0 / design_w
        family = html_attr(layer.get("fontFamily", "Noto Sans SC") or "Noto Sans SC")
        weight = html_attr(layer.get("fontWeight", "700") or "700")
        fill = html_attr(layer.get("fill", "#FFFFFF") or "#FFFFFF")
        align = html_attr(layer.get("align", "center") or "center")
        line_height = max(0.8, min(2.4, float(layer.get("lineHeight", 1.18) or 1.18)))
        bg = str(layer.get("background", "") or "").strip()
        bg_css = ""
        if bg:
            bg_css = f"background:{html_attr(bg)}; border-radius:0.55vw; padding:0.5vw 0.85vw;"
        shadow_css = "text-shadow:0 0 0.45vw rgba(0,0,0,0.62), 0 0.28vw 0.85vw rgba(0,0,0,0.38);" if layer.get("shadow", True) else "text-shadow:none;"
        layer_html.append(
            f"<div style='{common} color:{fill}; font-family:{_css_font_stack(family)}; "
            f"font-size:{font_size:.5f}vw; font-weight:{weight}; line-height:{line_height}; "
            f"text-align:{align}; white-space:pre-wrap; overflow:hidden; {shadow_css} {bg_css}'>{text}</div>"
        )

    if not layer_html:
        return ""
    return (
        "<div class='design-overlay' style='position:absolute; inset:0; z-index:2; "
        "pointer-events:none; overflow:hidden; box-sizing:border-box;'>"
        + "\n".join(layer_html)
        + "</div>"
    )
