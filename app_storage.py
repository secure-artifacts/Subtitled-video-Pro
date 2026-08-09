import os
import shutil
import sys
import json
from pathlib import Path


APP_NAME = "Subtitle Composer"
ENV_HOME = "SUBTITLE_COMPOSER_HOME"
ENV_PORTABLE_DIR = "SUBTITLE_COMPOSER_PORTABLE_DIR"
ENV_DISABLE_PORTABLE = "SUBTITLE_COMPOSER_DISABLE_PORTABLE"
ENV_CONFIG_DIR = "SUBTITLE_COMPOSER_CONFIG_DIR"
ENV_DATA_DIR = "SUBTITLE_COMPOSER_DATA_DIR"
ENV_CACHE_DIR = "SUBTITLE_COMPOSER_CACHE_DIR"
ENV_STATE_DIR = "SUBTITLE_COMPOSER_STATE_DIR"


def _clean_app_name(app_name=APP_NAME):
    return "".join(ch for ch in str(app_name or APP_NAME) if ch.isalnum() or ch in (" ", "-", "_")).strip() or APP_NAME


def _path_from_env(name, env=None):
    env = env or os.environ
    value = env.get(name, "")
    return Path(value).expanduser() if value else None


def app_root_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _truthy_env(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _portable_base(env=None, create_dir=True):
    env = env or os.environ
    if _truthy_env(env.get(ENV_DISABLE_PORTABLE)) or _path_from_env(ENV_HOME, env):
        return None
    explicit = _path_from_env(ENV_PORTABLE_DIR, env)
    base = explicit if explicit else app_root_dir() / "UserData"
    if not create_dir:
        return base
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base
    except Exception:
        return None


def _home_base(env=None):
    env = env or os.environ
    explicit = _path_from_env(ENV_HOME, env)
    if explicit:
        return explicit
    if sys.platform.startswith("win"):
        base = env.get("APPDATA") or env.get("USERPROFILE")
        return Path(base).expanduser() if base else Path.home()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()


def _local_base(env=None):
    env = env or os.environ
    explicit = _path_from_env(ENV_HOME, env)
    if explicit:
        return explicit
    if sys.platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or env.get("APPDATA") or env.get("USERPROFILE")
        return Path(base).expanduser() if base else Path.home()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(env.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()


def _standard_config_dir(app_name=APP_NAME, env=None):
    return _home_base(env) / _clean_app_name(app_name)


def _standard_data_dir(app_name=APP_NAME, env=None):
    return _standard_config_dir(app_name, env)


def _standard_cache_dir(app_name=APP_NAME, env=None):
    if sys.platform.startswith("win"):
        return _local_base(env) / _clean_app_name(app_name) / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _clean_app_name(app_name)
    return Path((env or os.environ).get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser() / _clean_app_name(app_name)


def _standard_state_dir(app_name=APP_NAME, env=None):
    if sys.platform.startswith("win"):
        return _local_base(env) / _clean_app_name(app_name) / "State"
    return _local_base(env) / _clean_app_name(app_name)


def app_config_dir(app_name=APP_NAME, env=None):
    explicit = _path_from_env(ENV_CONFIG_DIR, env)
    if explicit:
        return explicit
    portable = _portable_base(env)
    if portable:
        return portable
    return _standard_config_dir(app_name, env)


def app_data_dir(app_name=APP_NAME, env=None):
    explicit = _path_from_env(ENV_DATA_DIR, env)
    if explicit:
        return explicit
    portable = _portable_base(env)
    if portable:
        return portable
    return _standard_data_dir(app_name, env)


def app_cache_dir(app_name=APP_NAME, env=None):
    explicit = _path_from_env(ENV_CACHE_DIR, env)
    if explicit:
        return explicit
    portable = _portable_base(env)
    if portable:
        return portable / "Cache"
    return _standard_cache_dir(app_name, env)


def app_state_dir(app_name=APP_NAME, env=None):
    explicit = _path_from_env(ENV_STATE_DIR, env)
    if explicit:
        return explicit
    portable = _portable_base(env)
    if portable:
        return portable / "State"
    return _standard_state_dir(app_name, env)


def workspace_data_dir(workspace_root, dirname=".subtitle_composer"):
    return Path(workspace_root).expanduser().resolve() / dirname


def ensure_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file(filename, app_name=APP_NAME, env=None, create_dir=True):
    directory = app_data_dir(app_name, env)
    if create_dir:
        ensure_dir(directory)
    return directory / filename


def config_file(filename, app_name=APP_NAME, env=None, create_dir=True):
    directory = app_config_dir(app_name, env)
    if create_dir:
        ensure_dir(directory)
    return directory / filename


def state_file(filename, app_name=APP_NAME, env=None, create_dir=True):
    directory = app_state_dir(app_name, env)
    if create_dir:
        ensure_dir(directory)
    return directory / filename


def cache_file(filename, app_name=APP_NAME, env=None, create_dir=True):
    directory = app_cache_dir(app_name, env)
    if create_dir:
        ensure_dir(directory)
    return directory / filename


def read_json_file(path, default=None):
    try:
        with open(Path(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def write_json_file(path, data, indent=4):
    target = Path(path)
    ensure_dir(target.parent)
    temp = target.with_name(f"{target.name}.tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(temp, target)
    return target


def migrate_legacy_file(legacy_path, new_path, overwrite=False):
    legacy = Path(legacy_path)
    target = Path(new_path)
    if not legacy.exists():
        return False
    if target.exists() and not overwrite:
        return False
    ensure_dir(target.parent)
    shutil.copy2(legacy, target)
    return True


def _standard_user_file(filename, kind="data", app_name=APP_NAME, env=None):
    if kind == "config":
        return _standard_config_dir(app_name, env) / filename
    if kind == "cache":
        return _standard_cache_dir(app_name, env) / filename
    if kind == "state":
        return _standard_state_dir(app_name, env) / filename
    return _standard_data_dir(app_name, env) / filename


def _migrate_user_file_sources(filename, target, legacy_root=None, kind="data", app_name=APP_NAME, env=None):
    sources = []
    if legacy_root:
        sources.append(Path(legacy_root) / filename)
    sources.append(_standard_user_file(filename, kind, app_name, env))
    seen = set()
    for source in sources:
        try:
            source = Path(source)
            key = source.resolve(strict=False)
            if key in seen or key == Path(target).resolve(strict=False):
                continue
            seen.add(key)
            migrate_legacy_file(source, target)
        except Exception:
            continue


def resolve_user_file(filename, legacy_root=None, kind="data", app_name=APP_NAME, env=None, migrate=True):
    if kind == "config":
        target = config_file(filename, app_name, env)
    elif kind == "cache":
        target = cache_file(filename, app_name, env)
    elif kind == "state":
        target = state_file(filename, app_name, env)
    else:
        target = data_file(filename, app_name, env)
    if migrate:
        _migrate_user_file_sources(filename, target, legacy_root=legacy_root, kind=kind, app_name=app_name, env=env)
    return target


def known_user_files(legacy_root=None, app_name=APP_NAME, env=None):
    root = Path(legacy_root) if legacy_root else None
    return {
        "style_presets": resolve_user_file("style_presets.json", root, "config", app_name, env, migrate=False),
        "signature_presets": resolve_user_file("signature_presets.json", root, "config", app_name, env, migrate=False),
        "layout_presets": resolve_user_file("layout_presets.json", root, "config", app_name, env, migrate=False),
        "title_caption_presets": resolve_user_file("title_caption_presets.json", root, "config", app_name, env, migrate=False),
        "caption_mode_presets": resolve_user_file("caption_mode_presets.json", root, "config", app_name, env, migrate=False),
        "effects": resolve_user_file("effects.json", root, "config", app_name, env, migrate=False),
        "settings": resolve_user_file("settings.json", root, "config", app_name, env, migrate=False),
        "batch_queue_backups": resolve_user_file("batch_queue_backups.json", root, "state", app_name, env, migrate=False),
        "export_queue_backups": resolve_user_file("export_queue_backups.json", root, "state", app_name, env, migrate=False),
        "project_cache": resolve_user_file("project_cache.json", root, "cache", app_name, env, migrate=False),
        "edit_project_cache": resolve_user_file("sh_v8_project_cache.json", root, "cache", app_name, env, migrate=False),
    }
