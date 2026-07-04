import os
import re
from typing import Callable, Iterable

import requests

from app_config import load_app_config
from ui_components import normalize_word_timestamps

GROQ_TRANSCRIPTION_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
DEFAULT_TRANSCRIPTION_PROVIDER_ORDER = ["groq", "cloudflare"]
PROVIDER_LABELS = {
    "groq": "Groq Whisper",
    "cloudflare": "Cloudflare Whisper",
}


def _as_clean_list(value):
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").replace(",", "\n").splitlines()
    return [str(item or "").strip() for item in raw_items if str(item or "").strip()]


def groq_api_keys(config=None):
    config = config if isinstance(config, dict) else load_app_config()
    keys = _as_clean_list(config.get("groq_api_keys", []))
    legacy_key = str(config.get("groq_api_key") or "").strip()
    if legacy_key and legacy_key not in keys:
        keys.insert(0, legacy_key)
    return keys


def cloudflare_accounts(config=None):
    config = config if isinstance(config, dict) else load_app_config()
    accounts = config.get("cf_accounts", [])
    if not isinstance(accounts, list):
        return []
    return [
        {"id": str(item.get("id") or "").strip(), "token": str(item.get("token") or "").strip()}
        for item in accounts
        if isinstance(item, dict) and str(item.get("id") or "").strip() and str(item.get("token") or "").strip()
    ]


def parse_transcription_provider_order(value=None):
    if value is None:
        return list(DEFAULT_TRANSCRIPTION_PROVIDER_ORDER)
    if isinstance(value, str):
        tokens = re.split(r"[>,/|;\s]+", value.strip().lower())
    elif isinstance(value, Iterable):
        tokens = [str(item or "").strip().lower() for item in value]
    else:
        tokens = []
    order = []
    for token in tokens:
        if token in PROVIDER_LABELS and token not in order:
            order.append(token)
    return order or list(DEFAULT_TRANSCRIPTION_PROVIDER_ORDER)


def configured_transcription_provider_order(config=None, provider_order=None):
    config = config if isinstance(config, dict) else load_app_config()
    order = parse_transcription_provider_order(provider_order if provider_order is not None else config.get("ai_transcription_provider_order"))
    available = []
    for provider in order:
        if provider == "groq" and groq_api_keys(config):
            available.append(provider)
        elif provider == "cloudflare" and cloudflare_accounts(config):
            available.append(provider)
    return available


def transcription_provider_label(provider):
    return PROVIDER_LABELS.get(provider, str(provider or "AI"))


def _emit_progress(progress, message, color="#cdd6f4"):
    if not callable(progress):
        return
    try:
        progress(message, color)
    except TypeError:
        progress(message)


def _audio_mime(path):
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext == ".wav":
        return "audio/wav"
    if ext in {".m4a", ".mp4"}:
        return "audio/mp4"
    if ext == ".ogg":
        return "audio/ogg"
    if ext == ".webm":
        return "audio/webm"
    return "audio/mpeg"


def _clean_word_text(text):
    cleaned = re.sub(r"(?i)stereo_[^\s]+", "", str(text or ""))
    cleaned = cleaned.replace(".mp3", "").replace(".wav", "").strip()
    return cleaned


def _words_from_verbose_json(payload):
    raw_words = []
    if isinstance(payload, dict) and isinstance(payload.get("words"), list):
        for item in payload.get("words") or []:
            if not isinstance(item, dict):
                continue
            raw_words.append({
                "word": _clean_word_text(item.get("word") or item.get("text") or ""),
                "start": item.get("start", 0.0),
                "end": item.get("end", item.get("start", 0.0)),
            })
    if not raw_words and isinstance(payload, dict) and isinstance(payload.get("segments"), list):
        for item in payload.get("segments") or []:
            if not isinstance(item, dict):
                continue
            raw_words.append({
                "word": _clean_word_text(item.get("text") or ""),
                "start": item.get("start", 0.0),
                "end": item.get("end", item.get("start", 0.0)),
            })
    return normalize_word_timestamps(raw_words)


def _words_from_cloudflare(payload):
    result = payload.get("result") if isinstance(payload, dict) else {}
    words = result.get("words") if isinstance(result, dict) else []
    raw_words = [
        {"word": _clean_word_text(item.get("word") or item.get("text") or ""), "start": item.get("start", 0.0), "end": item.get("end", item.get("start", 0.0))}
        for item in words or []
        if isinstance(item, dict) and _clean_word_text(item.get("word") or item.get("text") or "")
    ]
    return normalize_word_timestamps(raw_words)


def _transcribe_with_groq(audio_path, data, config, timeout):
    keys = groq_api_keys(config)
    if not keys:
        raise Exception("Groq API Key \u672a\u914d\u7f6e")
    model = str(config.get("groq_whisper_model") or DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
    language = str(config.get("groq_transcription_language") or "en").strip()
    if language.lower() in {"auto", "detect", "none", "\u81ea\u52a8"}:
        language = ""
    filename = os.path.basename(str(audio_path or "audio.mp3")) or "audio.mp3"
    last_err = ""
    for index, key in enumerate(keys, start=1):
        try:
            form_data = [
                ("model", model),
                ("response_format", "verbose_json"),
                ("temperature", "0"),
                ("timestamp_granularities[]", "word"),
                ("timestamp_granularities[]", "segment"),
            ]
            if language:
                form_data.append(("language", language))
            res = requests.post(
                GROQ_TRANSCRIPTION_ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, data, _audio_mime(audio_path))},
                data=form_data,
                timeout=timeout,
            )
            if res.status_code == 200:
                words = _words_from_verbose_json(res.json())
                if words:
                    return words
                last_err = "Groq \u8fd4\u56de\u4e86\u7ed3\u679c\uff0c\u4f46\u6ca1\u6709\u53ef\u7528\u7684 word \u65f6\u95f4\u6233"
            else:
                last_err = f"Groq key {index} HTTP {res.status_code}: {res.text[:300]}"
        except Exception as exc:
            last_err = f"Groq key {index}: {exc}"
    raise Exception(last_err or "Groq \u8bf7\u6c42\u5931\u8d25")


def _transcribe_with_cloudflare(data, config, timeout):
    accounts = cloudflare_accounts(config)
    if not accounts:
        raise Exception("Cloudflare API \u672a\u914d\u7f6e")
    last_err = ""
    for index, account in enumerate(accounts, start=1):
        try:
            res = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account['id']}/ai/run/@cf/openai/whisper",
                headers={"Authorization": f"Bearer {account['token']}", "Content-Type": "application/octet-stream"},
                data=data,
                timeout=timeout,
            )
            if res.status_code == 200:
                payload = res.json()
                if payload.get("success"):
                    words = _words_from_cloudflare(payload)
                    if words:
                        return words
                    last_err = "Cloudflare \u8fd4\u56de\u4e86\u7ed3\u679c\uff0c\u4f46\u6ca1\u6709\u53ef\u7528\u7684 word \u65f6\u95f4\u6233"
                else:
                    last_err = f"Cloudflare account {index}: {str(payload)[:300]}"
            else:
                last_err = f"Cloudflare account {index} HTTP {res.status_code}: {res.text[:300]}"
        except Exception as exc:
            last_err = f"Cloudflare account {index}: {exc}"
    raise Exception(last_err or "Cloudflare \u8bf7\u6c42\u5931\u8d25")


def transcribe_audio_words(audio_path, progress=None, timeout=60, provider_order=None):
    if not audio_path or not os.path.exists(audio_path):
        raise Exception("\u542c\u8bd1\u97f3\u9891\u4e0d\u5b58\u5728")
    with open(audio_path, "rb") as f:
        data = f.read()
    if not data:
        raise Exception("\u542c\u8bd1\u97f3\u9891\u4e3a\u7a7a")

    config = load_app_config()
    order = configured_transcription_provider_order(config, provider_order=provider_order)
    if not order:
        raise Exception("\u672a\u914d\u7f6e AI \u542c\u8bd1 API\uff1a\u8bf7\u5728\u8bbe\u7f6e\u91cc\u586b\u5199 Groq API Key \u6216 Cloudflare \u8d26\u53f7\u6c60")

    errors = []
    for provider in order:
        label = transcription_provider_label(provider)
        _emit_progress(progress, f"\u23f3 \u6b63\u5728\u8c03\u7528 {label} \u542c\u8bd1...", "#cdd6f4")
        try:
            if provider == "groq":
                words = _transcribe_with_groq(audio_path, data, config, timeout)
            elif provider == "cloudflare":
                words = _transcribe_with_cloudflare(data, config, timeout)
            else:
                continue
            _emit_progress(progress, f"\u2705 {label} \u542c\u8bd1\u6210\u529f", "#a6e3a1")
            return words
        except Exception as exc:
            message = f"{label}: {exc}"
            errors.append(message)
            _emit_progress(progress, f"\u26a0 {label} \u5931\u8d25\uff0c\u5c1d\u8bd5\u4e0b\u4e00\u4e2a\u670d\u52a1...", "#f9e2af")
    raise Exception("AI \u542c\u8bd1\u8bf7\u6c42\u5931\u8d25\uff1a\n" + "\n".join(errors[-4:]))
