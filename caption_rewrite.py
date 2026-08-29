import copy
import os
import re
import subprocess
import tempfile

from ai_transcription import transcribe_audio_words
from caption_presets import (
    chunk_mode_preserves_caption_blocks,
    fixed_word_count_for_chunk_mode,
    is_exact_single_word_chunk_mode,
    is_full_text_chunk_mode,
    is_smart_transcription_chunk_mode,
    narrative_chunk_merge_words,
    narrative_chunk_word_bounds,
    pacing_merge_word_limit_for_chunk_mode,
    smart_transcription_word_bounds,
)
from core import get_ffmpeg_cmd
from media_probe import get_exact_duration
from ui_components import (
    FAITH_WORDS,
    align_reference_text_to_timestamps,
    format_subtitle_text_spacing,
    merge_short_subtitle_segments,
    merge_single_word_subtitle_segments,
    normalize_scripture_quote_text,
    normalize_word_timestamps,
    protect_fast_subtitle_pacing,
    rebalance_subtitle_layout,
    should_defer_subtitle_break_for_readability,
)


def clean_reference_text(raw_text):
    text = str(raw_text or "")
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"([.,!?;:][\"'\u201d]?)([A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(r"[ \t]+", " ", text)
    sentences = re.split(r"([.!?][\"'\u201d]?\s+)", text)
    cleaned = []
    for sentence in sentences:
        if sentence and sentence[0].islower():
            cleaned.append(sentence[0].upper() + sentence[1:])
        else:
            cleaned.append(sentence)
    return normalize_scripture_quote_text("".join(cleaned).strip())


def sanitize_generated_subtitles(subtitles, edit_state):
    data = copy.deepcopy(subtitles or [])
    def_x = float(edit_state.get("default_pos_x", 0.0) or 0.0)
    def_y = float(edit_state.get("default_pos_y", 25.0) or 25.0)
    default_style = copy.deepcopy(edit_state.get("default_style", {}) if isinstance(edit_state.get("default_style"), dict) else {})
    edit_state["default_style"] = default_style

    for sub in data:
        if not isinstance(sub, dict):
            continue
        sub["track"] = sub.get("track", 1)
        sub["pos_x"] = float(sub.get("pos_x", def_x) or 0.0)
        sub["pos_y"] = float(sub.get("pos_y", def_y) or 0.0)
        if not isinstance(sub.get("style"), dict):
            sub["style"] = {}
        for key, value in default_style.items():
            if key in sub and key not in {"track", "pos_x", "pos_y", "words", "text", "start", "end", "style"}:
                sub["style"][key] = sub.pop(key)
            elif key not in sub["style"]:
                sub["style"][key] = value
        if not sub.get("words"):
            sub["words"] = [{"text": sub.get("text", ""), "start": sub.get("start", 0.0), "end": sub.get("end", 1.0)}]
        fixed_words = []
        for word in sub.get("words", []) or []:
            text = str(word.get("text") or word.get("word") or "").strip()
            if not text:
                continue
            fixed_words.append({
                "text": text,
                "start": float(word.get("start", sub.get("start", 0.0)) or 0.0),
                "end": float(word.get("end", sub.get("end", 1.0)) or 1.0),
            })
        if fixed_words:
            sub["words"] = fixed_words
            if not str(sub.get("text", "")).strip():
                sub["text"] = " ".join(word["text"] for word in fixed_words).replace(" \n", "\n").replace("\n ", "\n")
    return data


def _apply_timing_mode(subtitles, timing_mode):
    if not subtitles:
        return subtitles
    if "对齐声音" in str(timing_mode or ""):
        start_pad, end_pad = 0.0, 0.03
    elif "L Cut" in str(timing_mode or ""):
        start_pad, end_pad = 0.12, 0.04
    else:
        start_pad, end_pad = 0.02, 0.16

    original_starts = [float(sub.get("start", 0.0) or 0.0) for sub in subtitles]
    for idx, sub in enumerate(subtitles):
        raw_start = float(sub.get("start", 0.0) or 0.0)
        raw_end = float(sub.get("end", raw_start + 0.3) or raw_start + 0.3)
        new_start = max(0.0, raw_start - start_pad)
        new_end = raw_end + end_pad
        if idx + 1 < len(subtitles):
            next_start = max(0.0, original_starts[idx + 1] - start_pad)
            new_end = min(new_end, max(new_start + 0.05, next_start - 0.01))
        if new_end <= new_start:
            new_end = new_start + 0.05
        sub["start"] = new_start
        sub["end"] = new_end
    return subtitles


def _fill_subtitle_gaps(subtitles, max_fill=1.20, min_gap=0.05):
    ordered = sorted(
        [sub for sub in subtitles or [] if isinstance(sub, dict)],
        key=lambda sub: (int(sub.get("track", 1)), float(sub.get("start", 0.0) or 0.0), float(sub.get("end", 0.0) or 0.0)),
    )
    for idx, sub in enumerate(ordered[:-1]):
        next_sub = ordered[idx + 1]
        if int(sub.get("track", 1)) != int(next_sub.get("track", 1)):
            continue
        end = float(sub.get("end", float(sub.get("start", 0.0) or 0.0) + 0.05) or 0.0)
        next_start = float(next_sub.get("start", end) or end)
        gap = next_start - end
        if gap > min_gap:
            sub["end"] = min(next_start - 0.01, end + max_fill)
    return subtitles



def build_full_text_subtitle(words, start=None, end=None):
    normalized = normalize_word_timestamps(words)
    if not normalized:
        return []
    full_words = []
    for item in normalized:
        word_text = str(item.get("word") or item.get("text") or "").strip()
        if not word_text:
            continue
        full_words.append({
            "text": word_text,
            "start": float(item.get("start", 0.0) or 0.0),
            "end": float(item.get("end", item.get("start", 0.0) or 0.0) or 0.0),
        })
    if not full_words:
        return []
    clip_start = float(start if start is not None else full_words[0]["start"])
    clip_end = float(end if end is not None else max(word["end"] for word in full_words))
    if clip_end <= clip_start:
        clip_end = clip_start + 0.5
    text = " ".join(word["text"] for word in full_words).replace(" \n", "\n").replace("\n ", "\n")
    return [{
        "text": format_subtitle_text_spacing(text),
        "start": clip_start,
        "end": clip_end,
        "track": 1,
        "pos_y": 50.0,
        "caption_build_mode": "full_text",
        "style": {
            "caption_build_mode": "full_text",
            "_full_text_defaults_applied": True,
            "layout_mode": "standard",
            "box_layout": "fixed",
            "box_width": 92.0,
            "box_height": 0.0,
            "max_lines": 10,
            "size": 54,
            "line_height": 1.04,
            "layout_row_gap": 100,
            "text_reveal_mode": "all",
            "inactive_alpha": 100,
            "anim_type": "none",
            "full_roll_window_mode": "lines",
            "full_roll_visible_lines": 3,
            "full_roll_window_height": 28,
            "full_roll_start_y": 18,
            "full_roll_end_y": -16,
            "full_roll_feather": 8,
            "full_roll_lock_to_words": True,
            "font_motion": "none",
            "hl_motion": "stable",
        },
        "words": full_words,
    }]

def generate_subtitles_from_words(words, mode, timing_mode=None, fill_subtitle_gaps=True, clip_end=None):
    words = normalize_word_timestamps(words, fallback_start=0.0, fallback_end=clip_end) if clip_end else normalize_word_timestamps(words)
    if is_full_text_chunk_mode(mode):
        return build_full_text_subtitle(words, end=clip_end)
    subtitles = []
    current = {"words": []}
    puncts = [".", "!", "?", ",", "，", "。", "！", "？", ";"]
    timing_mode = timing_mode or "J Cut (字幕稍后收尾)"
    sound_aligned = "对齐声音" in timing_mode
    narrative_min_words, narrative_max_words = narrative_chunk_word_bounds(mode)
    narrative_merge_words = narrative_chunk_merge_words(mode)
    fixed_count = fixed_word_count_for_chunk_mode(mode)
    exact_single_word = is_exact_single_word_chunk_mode(mode)
    precise_chunk_mode = exact_single_word or fixed_count > 0
    smart_transcription_mode = is_smart_transcription_chunk_mode(mode)
    smart_min_words, smart_max_words = smart_transcription_word_bounds(mode)

    for idx, word in enumerate(words):
        if not current["words"]:
            current["start"] = word["start"]
        current["words"].append({"text": word["word"], "start": word["start"], "end": word["end"]})
        current["end"] = word["end"]

        token = str(word.get("word", ""))
        has_punct = any(token.endswith(punct) for punct in puncts)
        word_count = len(current["words"])
        current_duration = current["end"] - current["start"]
        next_word = words[idx + 1]["word"] if idx + 1 < len(words) else ""
        next_start = words[idx + 1]["start"] if idx + 1 < len(words) else 9999.0
        silence_gap = next_start - current["end"]
        force_break = silence_gap > 0.8
        narrative_block = narrative_max_words > 0
        tiktok_smart = smart_transcription_mode
        tiktok_min_words = smart_min_words or 4
        tiktok_max_words = smart_max_words or 7
        smart_short = "智能重点" in str(mode or "") or "3-4词为主" in str(mode or "")
        natural_short = "自然短句" in str(mode or "") or "1-4" in str(mode or "")
        long_chunk_block = chunk_mode_preserves_caption_blocks(mode) and not narrative_block

        clean_current = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff']", "", token).lower()
        weak_words = {
            "i", "you", "he", "she", "we", "they", "a", "an", "the", "to", "of", "in", "on",
            "for", "and", "or", "but", "is", "am", "are", "was", "were", "be", "been", "do",
            "does", "did", "not", "would", "could", "should", "have", "has", "had", "it",
            "my", "your", "his", "her", "their", "our",
        }
        is_key_word = bool(clean_current) and clean_current not in weak_words and (
            len(clean_current) >= 7 or clean_current in FAITH_WORDS or clean_current.isupper()
        )

        is_break = False
        if exact_single_word:
            is_break = True
        elif fixed_count:
            is_break = word_count >= fixed_count or force_break
        elif narrative_block:
            narrative_hard_gap_min = max(6, narrative_min_words - 2)
            narrative_key_min = max(narrative_min_words + 2, narrative_max_words - 2)
            narrative_key_dur = 3.2 if narrative_max_words >= 18 else 2.6
            is_break = (
                (force_break and word_count >= narrative_hard_gap_min)
                or (has_punct and word_count >= narrative_min_words)
                or (silence_gap > 0.42 and word_count >= narrative_min_words)
                or (is_key_word and word_count >= narrative_key_min and (silence_gap > 0.16 or current_duration > narrative_key_dur))
                or word_count >= narrative_max_words
            )
        elif tiktok_smart:
            key_min = min(tiktok_max_words, max(tiktok_min_words + 1, 5))
            is_break = (
                (force_break and word_count >= tiktok_min_words)
                or (has_punct and word_count >= tiktok_min_words)
                or (silence_gap > 0.46 and word_count >= tiktok_min_words)
                or (silence_gap > 0.28 and word_count >= tiktok_min_words)
                or (is_key_word and word_count >= key_min and (silence_gap > 0.14 or current_duration > 1.55))
                or word_count >= tiktok_max_words
                or (word_count >= key_min and current_duration > max(2.05, tiktok_min_words * 0.32))
            )
        elif smart_short:
            long_slot = (len(subtitles) + int(float(current.get("start", 0.0)) * 10)) % 5 == 3
            is_break = (
                force_break
                or (has_punct and word_count >= 1)
                or (is_key_word and word_count >= 4)
                or (silence_gap > 0.42 and word_count >= 1 and is_key_word)
                or (silence_gap > 0.28 and word_count >= 2)
                or word_count >= 6
                or (word_count >= 4 and (not long_slot or silence_gap > 0.16 or current_duration > 1.80))
                or (word_count >= 3 and current_duration > 1.45)
            )
        elif natural_short:
            is_break = (
                force_break
                or (has_punct and word_count >= 1)
                or (silence_gap > 0.30 and word_count >= 2)
                or word_count >= 4
                or (word_count >= 3 and current_duration > 1.35)
            )
        elif long_chunk_block:
            long_block_max = max(8, pacing_merge_word_limit_for_chunk_mode(mode))
            long_block_min = max(6, long_block_max - 3)
            is_break = (
                (force_break and word_count >= long_block_min)
                or (has_punct and word_count >= long_block_min)
                or (silence_gap > 0.42 and word_count >= long_block_min)
                or word_count >= long_block_max
                or (word_count >= max(6, long_block_max - 2) and current_duration > 3.2)
            )
        elif sound_aligned:
            is_break = (
                (silence_gap > 0.55 and current_duration >= 0.25)
                or (silence_gap > 0.34 and word_count >= 2)
                or (has_punct and silence_gap > 0.18 and current_duration > 0.75)
                or current_duration >= 3.8
                or word_count >= 13
            )
        elif "双行" in str(mode or ""):
            is_break = force_break or (has_punct and current_duration > 1.2) or word_count >= 12 or (word_count >= 8 and current_duration > 2.5)
        else:
            is_break = force_break or (has_punct and current_duration > 0.8) or word_count >= 6 or (word_count >= 3 and current_duration > 1.5)

        if next_word:
            if re.match(r"^[:\d]", str(next_word)):
                is_break = False
            curr_clean = re.sub(r"[^a-zA-Z]", "", token).lower()
            if curr_clean in {"proverbs", "psalm", "psalms", "matthew", "mark", "luke", "john", "genesis", "exodus", "romans", "corinthians", "chapter", "verse"}:
                is_break = False
            if token.endswith(":"):
                is_break = False
            if not precise_chunk_mode and is_break and should_defer_subtitle_break_for_readability(
                token,
                next_word,
                segment_word_count=word_count,
                silence_gap=silence_gap,
                has_punct=has_punct,
                is_last_word=(idx == len(words) - 1),
            ):
                is_break = False

        if is_break:
            if ("双行" in str(mode or "") or long_chunk_block or sound_aligned) and word_count >= 6:
                mid = word_count // 2
                current["words"][mid]["text"] = "\n" + current["words"][mid]["text"].lstrip()
            current["text"] = format_subtitle_text_spacing(" ".join(item["text"] for item in current["words"]))
            current["track"] = 1
            subtitles.append(current)
            current = {"words": []}

    if current["words"]:
        if (chunk_mode_preserves_caption_blocks(mode) or sound_aligned) and len(current["words"]) >= 6:
            mid = len(current["words"]) // 2
            current["words"][mid]["text"] = "\n" + current["words"][mid]["text"].lstrip()
        current["text"] = format_subtitle_text_spacing(" ".join(item["text"] for item in current["words"]))
        current["track"] = 1
        subtitles.append(current)

    if not precise_chunk_mode and (narrative_max_words > 0 or "双行" in str(mode or "") or "长句" in str(mode or "") or "约10" in str(mode or "")):
        subtitles = merge_single_word_subtitle_segments(subtitles, max_merged_words=narrative_merge_words if narrative_max_words > 0 else 18)
    if smart_transcription_mode:
        subtitles = merge_short_subtitle_segments(subtitles, min_words=smart_min_words or 4, max_merged_words=smart_max_words or 7)

    subtitles = _apply_timing_mode(subtitles, timing_mode)
    if fill_subtitle_gaps:
        subtitles = _fill_subtitle_gaps(subtitles)
    pacing_merge_words = pacing_merge_word_limit_for_chunk_mode(mode)
    return protect_fast_subtitle_pacing(
        subtitles,
        allow_merge=pacing_merge_words > 0,
        max_merged_words=pacing_merge_words or 1,
    )


def _emit_progress(progress, message, color="#cdd6f4"):
    if callable(progress):
        try:
            progress(message, color)
        except TypeError:
            progress(message)


def extract_transcription_audio(source_path):
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError("听译素材不存在")
    fd, temp_audio = tempfile.mkstemp(prefix="subtitle_project_ai_", suffix=".mp3")
    os.close(fd)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-i",
        source_path,
        "-vn",
        "-map",
        "a:0?",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-b:a",
        "16k",
        temp_audio,
    ]
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    if not os.path.exists(temp_audio) or os.path.getsize(temp_audio) <= 100:
        try:
            os.remove(temp_audio)
        except Exception:
            pass
        raise RuntimeError("音频抽取失败：素材可能没有声音或格式不支持")
    return temp_audio


def project_transcription_source(edit_state):
    audio_path = edit_state.get("audio_path", "")
    if audio_path and os.path.exists(audio_path):
        return audio_path
    for clip in edit_state.get("video_clips", []) or []:
        path = clip.get("path", "") if isinstance(clip, dict) else ""
        if path and os.path.exists(path):
            return path
    return ""


def rewrite_project_subtitles(project, chunk_mode, timing_mode=None, provider_order=None, progress=None):
    project = copy.deepcopy(project or {})
    edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
    source_path = project_transcription_source(edit_state)
    if not source_path:
        raise FileNotFoundError("这个 Reel 没有可听译的音频或视频素材")

    chunk_mode = chunk_mode or edit_state.get("chunk_mode", "智能听译 (4-7词，适配双行按词)")
    timing_mode = timing_mode or edit_state.get("timing_mode", "J Cut (字幕稍后收尾)")
    edit_state["chunk_mode"] = chunk_mode
    edit_state["timing_mode"] = timing_mode

    temp_audio = None
    try:
        _emit_progress(progress, "正在提取 AI 听译专用小音频...")
        temp_audio = extract_transcription_audio(source_path)
        duration = float(get_exact_duration(temp_audio) or 0.0)
        fallback_end = duration if duration > 0 else None
        _emit_progress(progress, "正在调用 AI 听译服务...")
        words = normalize_word_timestamps(
            transcribe_audio_words(temp_audio, progress=progress, provider_order=provider_order),
            fallback_start=0.0,
            fallback_end=fallback_end,
        )
        custom_text = str(edit_state.get("custom_text", "") or "").strip()
        if custom_text:
            _emit_progress(progress, "正在把工程文案贴合到 AI 时间戳...")
            words = align_reference_text_to_timestamps(
                words,
                clean_reference_text(custom_text),
                fallback_start=0.0,
                fallback_end=fallback_end,
            )
        _emit_progress(progress, "正在按当前听译参数重建字幕...")
        subtitles = generate_subtitles_from_words(words, chunk_mode, timing_mode, bool(edit_state.get("fill_subtitle_gaps", True)), clip_end=fallback_end)
        subtitles = sanitize_generated_subtitles(subtitles, edit_state)
        subtitles, _ = rebalance_subtitle_layout(
            subtitles,
            fallback_style=edit_state.get("default_style", {}),
            default_pos=(float(edit_state.get("default_pos_x", 0.0) or 0.0), float(edit_state.get("default_pos_y", 25.0) or 25.0)),
            force_standard_box=True,
            allow_split=not chunk_mode_preserves_caption_blocks(chunk_mode),
        )
        edit_state["subs_data"] = subtitles
        project["subs_data"] = copy.deepcopy(subtitles)
        if subtitles:
            edit_state["duration"] = max(float(edit_state.get("duration", 0.0) or 0.0), max(float(sub.get("end", 0.0) or 0.0) for sub in subtitles))
        _emit_progress(progress, f"AI 听译重写完成：{len(subtitles)} 条字幕", "#a6e3a1")
        return project, {"subtitle_count": len(subtitles), "source_path": source_path, "used_custom_text": bool(custom_text)}
    finally:
        if temp_audio and os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except Exception:
                pass
