import os

from app_storage import read_json_file, resolve_user_file, write_json_file

CONFIG_FILE = resolve_user_file("settings.json", legacy_root=os.getcwd(), kind="config")
DEFAULT_SHORTCUTS = {
    "preview_fullscreen": "Ctrl+F",
}
DEFAULT_OUTPUT_RESOLUTION = "竖屏 1080x1920"
OUTPUT_RESOLUTION_OPTIONS = [
    "竖屏 1080x1920",
    "横屏 1920x1080",
    "正方 1080x1080",
    "自动检测 (跟随素材)",
]


def load_app_config():
    data = read_json_file(CONFIG_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_app_config(config):
    data = dict(config or {})
    write_json_file(CONFIG_FILE, data, indent=4)


def get_output_resolution():
    value = str(load_app_config().get("output_resolution") or DEFAULT_OUTPUT_RESOLUTION).strip()
    return value if value in OUTPUT_RESOLUTION_OPTIONS else DEFAULT_OUTPUT_RESOLUTION


def set_output_resolution(value):
    value = value if value in OUTPUT_RESOLUTION_OPTIONS else DEFAULT_OUTPUT_RESOLUTION
    config = load_app_config()
    config["output_resolution"] = value
    save_app_config(config)
    return value


def _shortcut_text(value, default):
    text = str(value or "").strip()
    return text or default


def get_shortcuts():
    saved = load_app_config().get("shortcuts")
    saved = saved if isinstance(saved, dict) else {}
    shortcuts = dict(DEFAULT_SHORTCUTS)
    for key, default in DEFAULT_SHORTCUTS.items():
        shortcuts[key] = _shortcut_text(saved.get(key), default)
    return shortcuts


def get_shortcut(key):
    return get_shortcuts().get(key, DEFAULT_SHORTCUTS.get(key, ""))


def set_shortcut(key, sequence):
    if key not in DEFAULT_SHORTCUTS:
        return ""
    config = load_app_config()
    saved = config.get("shortcuts")
    saved = saved if isinstance(saved, dict) else {}
    value = _shortcut_text(sequence, DEFAULT_SHORTCUTS[key])
    saved[key] = value
    config["shortcuts"] = saved
    save_app_config(config)
    return value


def get_preview_fullscreen_shortcut():
    return get_shortcut("preview_fullscreen")


def set_preview_fullscreen_shortcut(sequence):
    return set_shortcut("preview_fullscreen", sequence)


def resolution_to_size(resolution_text, media_path="", get_media_size=None):
    text = str(resolution_text or DEFAULT_OUTPUT_RESOLUTION)
    if "1920x1080" in text:
        return 1920, 1080
    if "1080x1080" in text:
        return 1080, 1080
    if ("自动" in text or "跟随" in text) and media_path and callable(get_media_size):
        try:
            w, h = get_media_size(media_path)
            if w and h:
                return int(w), int(h)
        except Exception:
            pass
    return 1080, 1920
