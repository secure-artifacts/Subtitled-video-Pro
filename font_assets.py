import json
import os
import re
from pathlib import Path

from core import get_app_dir


FONTS_DIR = os.path.join(get_app_dir(), "fonts", "open")
PERSONAL_FONTS_DIR = os.path.join(get_app_dir(), "fonts", "personal")
FONT_ASSET_MANIFEST = os.path.join(FONTS_DIR, "open_fonts_manifest.json")
FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
LICENSE_EXTS = (".txt", ".md", ".license", ".copyright")
DEFAULT_LICENSE = "OFL-1.1 or compatible open font license"
DEFAULT_SOURCE = "Bundled open font asset"
DEFAULT_NOTES = "Bundled with Subtitle Composer as an open/commercial-safe font. Keep the font license when redistributing project packages."

_REGISTERED_FONT_RECORDS = []
_REGISTERED_PERSONAL_FONT_RECORDS = []


def _key(value):
    return str(value or "").strip().casefold()


def ensure_fonts_dir():
    os.makedirs(FONTS_DIR, exist_ok=True)
    os.makedirs(PERSONAL_FONTS_DIR, exist_ok=True)
    return FONTS_DIR

def ensure_personal_fonts_dir():
    os.makedirs(PERSONAL_FONTS_DIR, exist_ok=True)
    return PERSONAL_FONTS_DIR


def load_font_asset_manifest():
    ensure_fonts_dir()
    if not os.path.exists(FONT_ASSET_MANIFEST):
        return {"schema": 1, "fonts": []}
    try:
        with open(FONT_ASSET_MANIFEST, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.setdefault("schema", 1)
    data.setdefault("fonts", [])
    if not isinstance(data["fonts"], list):
        data["fonts"] = []
    return data


def save_font_asset_manifest(data):
    ensure_fonts_dir()
    data = data if isinstance(data, dict) else {}
    data.setdefault("schema", 1)
    data.setdefault("fonts", [])
    with open(FONT_ASSET_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _relative_font_path(path):
    try:
        return os.path.relpath(os.path.abspath(path), FONTS_DIR).replace("\\", "/")
    except Exception:
        return os.path.basename(path)

def _relative_personal_font_path(path):
    try:
        return os.path.relpath(os.path.abspath(path), PERSONAL_FONTS_DIR).replace("\\", "/")
    except Exception:
        return os.path.basename(path)


def _font_file_path(filename):
    filename = str(filename or "").strip()
    if not filename:
        return ""
    if os.path.isabs(filename):
        return filename
    return os.path.join(FONTS_DIR, filename)


def _font_files_in_dir(root_dir):
    os.makedirs(root_dir, exist_ok=True)
    files = []
    for root, _, names in os.walk(root_dir):
        for name in names:
            if name.lower().endswith(FONT_EXTS):
                files.append(os.path.abspath(os.path.join(root, name)))
    return sorted(files, key=lambda item: item.casefold())


def bundled_font_files():
    ensure_fonts_dir()
    return _font_files_in_dir(FONTS_DIR)


def personal_font_files():
    ensure_personal_fonts_dir()
    return _font_files_in_dir(PERSONAL_FONTS_DIR)


def _guess_family_from_filename(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"\[[^\]]+\]", "", stem)
    stem = re.sub(r"[-_](Regular|Bold|Italic|Medium|SemiBold|Black|Light|Thin|ExtraBold|ExtraLight).*$", "", stem, flags=re.I)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    stem = stem.replace("_", " ").replace("-", " ")
    return " ".join(stem.split()).strip() or os.path.splitext(os.path.basename(path))[0]


def _manifest_by_family_file(manifest):
    result = {}
    for record in manifest.get("fonts", []) or []:
        if not isinstance(record, dict):
            continue
        family = str(record.get("family", "") or "").strip()
        filename = str(record.get("file", "") or "").strip()
        if family and filename:
            result[(_key(family), filename.replace("\\", "/"))] = record
    return result


def _license_candidates_for(path):
    font_dir = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0].casefold()
    candidates = []
    try:
        for name in os.listdir(font_dir):
            lower = name.casefold()
            full = os.path.join(font_dir, name)
            if not os.path.isfile(full):
                continue
            if lower.startswith("license") or lower.startswith("ofl") or lower.startswith("readme"):
                candidates.append(full)
            elif lower.startswith(stem) and lower.endswith(LICENSE_EXTS):
                candidates.append(full)
    except Exception:
        pass
    return sorted(set(candidates), key=lambda item: item.casefold())


def _record_for_font(font_path, family, existing=None):
    existing = existing if isinstance(existing, dict) else {}
    rel_file = _relative_font_path(font_path)
    license_file = existing.get("license_file", "")
    if not license_file:
        candidates = _license_candidates_for(font_path)
        if candidates:
            license_file = _relative_font_path(candidates[0])
    return {
        **existing,
        "family": str(family or "").strip() or _guess_family_from_filename(font_path),
        "file": rel_file,
        "source": existing.get("source") or DEFAULT_SOURCE,
        "license": existing.get("license") or DEFAULT_LICENSE,
        "license_file": license_file,
        "license_url": existing.get("license_url", ""),
        "notes": existing.get("notes") or DEFAULT_NOTES,
    }


def sync_manifest_from_registered(loaded_records):
    manifest = load_font_asset_manifest()
    known = _manifest_by_family_file(manifest)

    for item in loaded_records or []:
        path = item.get("path", "")
        if not path or not os.path.exists(path):
            continue
        for family in item.get("families", []) or [_guess_family_from_filename(path)]:
            family = str(family or "").strip()
            if not family:
                continue
            rel_file = _relative_font_path(path)
            key = (_key(family), rel_file)
            known[key] = _record_for_font(path, family, known.get(key))

    manifest["fonts"] = sorted(known.values(), key=lambda item: (str(item.get("family", "")).casefold(), str(item.get("file", "")).casefold()))
    return save_font_asset_manifest(manifest)


def sync_registry_from_manifest():
    try:
        from font_registry import upsert_open_font_assets
    except Exception:
        return None
    manifest = load_font_asset_manifest()
    return upsert_open_font_assets(manifest.get("fonts", []))

def _record_for_personal_font(font_path, family):
    license_file = ""
    candidates = _license_candidates_for(font_path)
    if candidates:
        license_file = _relative_personal_font_path(candidates[0])
    return {
        "family": str(family or "").strip() or _guess_family_from_filename(font_path),
        "file": _relative_personal_font_path(font_path),
        "source": "User personal fonts folder",
        "license": "Personal use / local only",
        "license_file": license_file,
        "license_url": "",
        "notes": "Loaded from fonts/personal for local personal use only. Do not bundle, redistribute, upload to GitHub, or use commercially unless separate license proof is recorded.",
        "personal_only": True,
    }


def sync_registry_from_personal(loaded_records):
    try:
        from font_registry import upsert_personal_font_assets
    except Exception:
        return None
    records = []
    for item in loaded_records or []:
        path = item.get("path", "")
        if not path or not os.path.exists(path):
            continue
        for family in item.get("families", []) or [_guess_family_from_filename(path)]:
            family = str(family or "").strip()
            if family:
                records.append(_record_for_personal_font(path, family))
    return upsert_personal_font_assets(records)


def _load_font_files_into_qt(font_files):
    try:
        from PyQt6.QtGui import QFontDatabase
    except Exception:
        return []

    loaded = []
    for font_path in font_files:
        try:
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id >= 0:
                loaded.append({
                    "path": font_path,
                    "families": QFontDatabase.applicationFontFamilies(font_id),
                })
        except Exception:
            continue
    return loaded


def register_bundled_fonts():
    global _REGISTERED_FONT_RECORDS, _REGISTERED_PERSONAL_FONT_RECORDS
    open_loaded = _load_font_files_into_qt(bundled_font_files())
    personal_loaded = _load_font_files_into_qt(personal_font_files())
    _REGISTERED_FONT_RECORDS = open_loaded
    _REGISTERED_PERSONAL_FONT_RECORDS = personal_loaded
    try:
        sync_manifest_from_registered(open_loaded)
        sync_registry_from_manifest()
        sync_registry_from_personal(personal_loaded)
    except Exception:
        pass
    return open_loaded + personal_loaded


def _file_url(path):
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return "file:///" + os.path.abspath(path).replace("\\", "/")


def _css_string(value):
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def font_asset_records():
    manifest = load_font_asset_manifest()
    records = []
    seen = set()

    for record in manifest.get("fonts", []) or []:
        if not isinstance(record, dict):
            continue
        family = str(record.get("family", "") or "").strip()
        filename = str(record.get("file", "") or "").strip()
        path = _font_file_path(filename)
        key = (_key(family), os.path.abspath(path).casefold())
        if not family or not os.path.exists(path) or key in seen:
            continue
        seen.add(key)
        fixed = dict(record)
        fixed["path"] = os.path.abspath(path)
        records.append(fixed)

    for item in _REGISTERED_FONT_RECORDS:
        path = item.get("path", "")
        if not path or not os.path.exists(path):
            continue
        for family in item.get("families", []) or [_guess_family_from_filename(path)]:
            key = (_key(family), os.path.abspath(path).casefold())
            if not family or key in seen:
                continue
            seen.add(key)
            records.append(_record_for_font(path, family))
            records[-1]["path"] = os.path.abspath(path)

    for item in _REGISTERED_PERSONAL_FONT_RECORDS:
        path = item.get("path", "")
        if not path or not os.path.exists(path):
            continue
        for family in item.get("families", []) or [_guess_family_from_filename(path)]:
            key = (_key(family), os.path.abspath(path).casefold())
            if not family or key in seen:
                continue
            seen.add(key)
            record = _record_for_personal_font(path, family)
            record["path"] = os.path.abspath(path)
            records.append(record)

    return sorted(records, key=lambda item: (str(item.get("family", "")).casefold(), str(item.get("file", "")).casefold()))


def font_face_css():
    rules = []
    for record in font_asset_records():
        if not isinstance(record, dict):
            continue
        family = str(record.get("family", "")).strip()
        path = record.get("path") or _font_file_path(record.get("file", ""))
        if not family or not path:
            continue
        if not os.path.exists(path):
            continue
        fmt = "opentype" if path.lower().endswith((".otf", ".otc")) else "truetype"
        weight = str(record.get("weight", "100 900") or "100 900").strip()
        style = str(record.get("style", "normal") or "normal").strip()
        rules.append(
            "@font-face { "
            f"font-family: '{_css_string(family)}'; "
            f"src: url('{_file_url(path)}') format('{fmt}'); "
            f"font-weight: {_css_string(weight)}; font-style: {_css_string(style)}; font-display: block; "
            "}"
        )
    return "\n".join(rules)


def font_asset_summary():
    records = font_asset_records()
    font_files = bundled_font_files()
    personal_files = personal_font_files()
    families = sorted({record.get("family", "") for record in records if record.get("family")}, key=lambda item: item.casefold())
    return {
        "fonts_dir": FONTS_DIR,
        "personal_fonts_dir": PERSONAL_FONTS_DIR,
        "manifest": FONT_ASSET_MANIFEST,
        "font_file_count": len(font_files),
        "personal_font_file_count": len(personal_files),
        "family_count": len(families),
        "families": families,
    }


def font_package_entries_for_families(families):
    wanted = {_key(family) for family in families or [] if str(family or "").strip()}
    if not wanted:
        return []

    entries = []
    seen_paths = set()

    def add_file(path, arc_rel):
        if not path or not os.path.exists(path):
            return
        abs_path = os.path.abspath(path)
        key = abs_path.casefold()
        if key in seen_paths:
            return
        seen_paths.add(key)
        entries.append((abs_path, arc_rel.replace("\\", "/")))

    for record in font_asset_records():
        if record.get("personal_only"):
            continue
        if _key(record.get("family", "")) not in wanted:
            continue
        path = record.get("path") or _font_file_path(record.get("file", ""))
        rel_file = _relative_font_path(path)
        add_file(path, os.path.join("fonts", "open", rel_file))
        license_file = str(record.get("license_file", "") or "").strip()
        if license_file:
            license_path = _font_file_path(license_file)
            add_file(license_path, os.path.join("fonts", "open", license_file))

    add_file(FONT_ASSET_MANIFEST, os.path.join("fonts", "open", "open_fonts_manifest.json"))
    return entries
