import copy
import json
import os
from datetime import datetime


FONT_REGISTRY_FILE = os.path.join(os.getcwd(), "font_registry.json")

STATUS_APPROVED = "approved"
STATUS_OPEN = "open"
STATUS_SYSTEM = "system"
STATUS_NONCOMMERCIAL = "noncommercial"
STATUS_REVIEW = "review"


OPEN_FONT_NOTES = "Open font. Commercial use is generally allowed; keep the font license if bundling or redistributing font files."


OPEN_FONT_FAMILIES = {
    "Noto Sans": "Google Fonts Noto family",
    "Noto Serif": "Google Fonts Noto family",
    "Noto Sans SC": "Google Fonts Noto CJK family",
    "Noto Serif SC": "Google Fonts Noto CJK family",
    "Noto Sans CJK SC": "Google/Adobe Noto CJK family",
    "Noto Serif CJK SC": "Google/Adobe Noto CJK family",
    "Source Han Sans SC": "Adobe/Google Source Han Sans",
    "Source Han Serif SC": "Adobe/Google Source Han Serif",
    "Source Sans 3": "Adobe Source family",
    "Source Serif 4": "Adobe Source family",
    "Source Code Pro": "Adobe Source family",
    "Inter": "Inter font family",
    "Roboto": "Google Fonts Roboto family",
    "Open Sans": "Google Fonts Open Sans",
    "Montserrat": "Google Fonts Montserrat",
    "Poppins": "Google Fonts Poppins",
    "Oswald": "Google Fonts Oswald",
    "Bebas Neue": "Google Fonts Bebas Neue",
    "Anton": "Google Fonts Anton",
    "Lato": "Google Fonts Lato",
    "Merriweather": "Google Fonts Merriweather",
    "TikTok Sans": "Google Fonts / TikTok Sans official release",
}


DEFAULT_FONTS = {
    "Segoe UI": {
        "status": STATUS_SYSTEM,
        "source": "Windows system font",
        "commercial_use": "check_os_license",
        "notes": "Use depends on the Windows/Microsoft font license. Do not bundle it into shared project packages unless the license is confirmed.",
    },
    "Segoe UI Emoji": {
        "status": STATUS_SYSTEM,
        "source": "Windows system font",
        "commercial_use": "check_os_license",
        "notes": "Emoji coverage font from Windows. Treat as OS-licensed, not a bundled commercial asset.",
    },
    "Arial": {
        "status": STATUS_SYSTEM,
        "source": "System font",
        "commercial_use": "check_os_license",
        "notes": "Common OS font. Check distribution and embedding rights before packaging templates.",
    },
    "Impact": {
        "status": STATUS_SYSTEM,
        "source": "System font",
        "commercial_use": "check_os_license",
        "notes": "Common OS font. Check distribution and embedding rights before packaging templates.",
    },
    "Microsoft YaHei": {
        "status": STATUS_SYSTEM,
        "source": "Windows system font",
        "commercial_use": "check_os_license",
        "notes": "Windows CJK system font. Check embedding/distribution rights before packaging.",
    },
    "SimHei": {
        "status": STATUS_SYSTEM,
        "source": "Windows system font",
        "commercial_use": "check_os_license",
        "notes": "Windows CJK system font. Check embedding/distribution rights before packaging.",
    },
}

for _font_name, _font_source in OPEN_FONT_FAMILIES.items():
    DEFAULT_FONTS.setdefault(_font_name, {
        "status": STATUS_OPEN,
        "source": _font_source,
        "commercial_use": "generally_allowed",
        "notes": OPEN_FONT_NOTES,
    })


STATUS_LABELS = {
    STATUS_APPROVED: "Legacy approval, treated as personal/non-commercial unless bundled open proof exists",
    STATUS_OPEN: "Open/commercial-safe, keep license when bundling",
    STATUS_SYSTEM: "System/personal-use until OS embedding rights are confirmed",
    STATUS_NONCOMMERCIAL: "Personal use only / non-commercial unless separate license proof is recorded",
    STATUS_REVIEW: "Unregistered, treat as personal preview until license proof is recorded",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _key(name):
    return str(name or "").strip().casefold()


def _default_category_for_status(status):
    if status == STATUS_APPROVED:
        return "team_approved"
    if status == STATUS_OPEN:
        return "open_commercial_safe"
    if status == STATUS_SYSTEM:
        return "system_review"
    if status == STATUS_NONCOMMERCIAL:
        return "restricted_noncommercial"
    return "unregistered_review"


def _infer_style_class(name, record=None):
    record = record if isinstance(record, dict) else {}
    if record.get("style_class"):
        return record.get("style_class")
    text = f"{name} {record.get('source', '')}".casefold()
    if "code" in text or "mono" in text:
        return "monospace"
    if "serif" in text or "song" in text or "ming" in text:
        return "serif"
    if any(token in text for token in ("script", "letter", "hand", "calligraphy", "ananda", "beautiful", "love")):
        return "script_handwriting"
    if any(token in text for token in ("christmas", "spooky", "horror", "death", "crack", "drip", "sprout", "valentine", "cupcake")):
        return "decorative_seasonal"
    if any(token in text for token in ("sans", "inter", "roboto", "poppins", "montserrat", "oswald", "lato", "anton", "bebas")):
        return "sans"
    return "display"


def normalize_font_record(name, record):
    record = copy.deepcopy(record) if isinstance(record, dict) else {}
    status = record.get("status") or STATUS_REVIEW
    if status == STATUS_APPROVED:
        status = STATUS_NONCOMMERCIAL
        record["commercial_use"] = "personal_only_registered"
        existing_notes = str(record.get("notes", "") or "").strip()
        suffix = "Legacy approved entries are treated as personal/non-commercial unless they are bundled open fonts with license proof."
        record["notes"] = f"{existing_notes} {suffix}".strip()
    record["status"] = status
    record.setdefault("category", _default_category_for_status(status))
    record.setdefault("style_class", _infer_style_class(name, record))
    record.setdefault("commercial_use", "unknown")
    record.setdefault("notes", "")
    return record


def _registry_defaults():
    return copy.deepcopy(DEFAULT_FONTS)


def _base_registry():
    return {
        "schema": 1,
        "updated_at": _now(),
        "fonts": _registry_defaults(),
    }


def merge_font_registry_defaults(data):
    data = copy.deepcopy(data) if isinstance(data, dict) else {}
    data.setdefault("schema", 1)
    data.setdefault("fonts", {})
    if not isinstance(data["fonts"], dict):
        data["fonts"] = {}

    existing_by_key = {_key(name): name for name in data["fonts"].keys()}
    for name, record in _registry_defaults().items():
        if _key(name) not in existing_by_key:
            data["fonts"][name] = copy.deepcopy(record)
    for name, record in list(data["fonts"].items()):
        data["fonts"][name] = normalize_font_record(name, record)
    data["updated_at"] = data.get("updated_at") or _now()
    return data


def load_font_registry(write_back=True):
    if not os.path.exists(FONT_REGISTRY_FILE):
        data = _base_registry()
        if write_back:
            save_font_registry(data)
        return data

    try:
        with open(FONT_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = _base_registry()
    data = merge_font_registry_defaults(data)
    if write_back:
        save_font_registry(data)
    return data


def save_font_registry(data):
    data = merge_font_registry_defaults(data)
    data["updated_at"] = _now()
    with open(FONT_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def upsert_approved_fonts(font_names):
    data = load_font_registry()
    fonts = data.setdefault("fonts", {})
    desired = {_key(name) for name in font_names or [] if str(name or "").strip()}
    for name, record in list(fonts.items()):
        if not isinstance(record, dict):
            continue
        is_user_registered = (
            record.get("commercial_use") in ("approved_by_user", "personal_only_registered")
            and record.get("status") in (STATUS_APPROVED, STATUS_NONCOMMERCIAL)
        )
        if is_user_registered and _key(name) not in desired:
            del fonts[name]
    existing_by_key = {_key(name): name for name in fonts.keys()}
    for raw_name in font_names or []:
        name = str(raw_name or "").strip()
        if not name:
            continue
        old_name = existing_by_key.get(_key(name))
        target_name = old_name or name
        existing = fonts.get(target_name, {}) if isinstance(fonts.get(target_name), dict) else {}
        if existing.get("status") == STATUS_OPEN:
            continue
        fonts[target_name] = {
            "status": STATUS_NONCOMMERCIAL,
            "source": "User-registered local/personal font",
            "commercial_use": "personal_only_registered",
            "category": "restricted_noncommercial",
            "style_class": _infer_style_class(target_name),
            "notes": "Registered for local/personal use only. Do not bundle, redistribute, or use commercially unless separate license proof is recorded.",
        }
        existing_by_key[_key(target_name)] = target_name
    return save_font_registry(data)


def upsert_personal_font_assets(records):
    data = load_font_registry()
    fonts = data.setdefault("fonts", {})
    existing_by_key = {_key(name): name for name in fonts.keys()}

    for record in records or []:
        if not isinstance(record, dict):
            continue
        family = str(record.get("family", "") or "").strip()
        if not family:
            continue
        target_name = existing_by_key.get(_key(family), family)
        existing = fonts.get(target_name, {}) if isinstance(fonts.get(target_name), dict) else {}
        if existing.get("status") == STATUS_OPEN:
            continue
        updated = copy.deepcopy(existing)
        updated.update({
            "status": STATUS_NONCOMMERCIAL,
            "source": record.get("source") or "User personal fonts folder",
            "commercial_use": "personal_only_registered",
            "category": "restricted_noncommercial",
            "style_class": record.get("style_class") or updated.get("style_class") or _infer_style_class(family, record),
            "personal_file": record.get("file", updated.get("personal_file", "")),
            "license": record.get("license") or updated.get("license") or "Personal use / local only",
            "license_file": record.get("license_file", updated.get("license_file", "")),
            "license_url": record.get("license_url", updated.get("license_url", "")),
            "notes": record.get("notes") or updated.get("notes") or "Loaded from fonts/personal for local personal use only. Do not bundle, redistribute, upload to GitHub, or use commercially unless separate license proof is recorded.",
        })
        fonts[target_name] = updated
        existing_by_key[_key(target_name)] = target_name

    return save_font_registry(data)

def upsert_open_font_assets(records):
    data = load_font_registry()
    fonts = data.setdefault("fonts", {})
    existing_by_key = {_key(name): name for name in fonts.keys()}

    for record in records or []:
        if not isinstance(record, dict):
            continue
        family = str(record.get("family", "") or "").strip()
        if not family:
            continue

        target_name = existing_by_key.get(_key(family), family)
        existing = fonts.get(target_name, {}) if isinstance(fonts.get(target_name), dict) else {}
        updated = copy.deepcopy(existing)
        updated.update({
            "source": record.get("source") or updated.get("source") or "Bundled open font asset",
            "commercial_use": updated.get("commercial_use") or "generally_allowed",
            "notes": record.get("notes") or updated.get("notes") or OPEN_FONT_NOTES,
            "bundled_file": record.get("file", updated.get("bundled_file", "")),
            "license": record.get("license", updated.get("license", "OFL-1.1 or compatible open font license")),
            "license_file": record.get("license_file", updated.get("license_file", "")),
            "license_url": record.get("license_url", updated.get("license_url", "")),
            "category": "open_commercial_safe",
            "style_class": record.get("style_class") or updated.get("style_class") or _infer_style_class(family, record),
        })
        updated["status"] = STATUS_OPEN
        updated["commercial_use"] = "generally_allowed"
        fonts[target_name] = updated
        existing_by_key[_key(target_name)] = target_name

    return save_font_registry(data)


def reset_to_open_font_policy():
    data = load_font_registry()
    fonts = data.setdefault("fonts", {})
    default_keys = {_key(default_name) for default_name in _registry_defaults().keys()}
    for name, record in list(fonts.items()):
        if not isinstance(record, dict):
            del fonts[name]
            continue
        is_default = _key(name) in default_keys
        is_open = record.get("status") == STATUS_OPEN
        if not is_default and not is_open:
            del fonts[name]
    return save_font_registry(data)


def safe_font_names(include_approved=False, include_open=True):
    data = load_font_registry(write_back=False)
    names = []
    for name, record in data.get("fonts", {}).items():
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if include_open and status == STATUS_OPEN:
            names.append(name)
    return sorted(set(names), key=lambda item: item.casefold())


def safe_font_keys(include_approved=False, include_open=True):
    return {_key(name) for name in safe_font_names(include_approved=include_approved, include_open=include_open)}


def is_safe_font(font_name, include_approved=False, include_open=True):
    return _key(font_name) in safe_font_keys(include_approved=include_approved, include_open=include_open)


def font_record_for(font_name, registry=None):
    return _record_for_font(font_name, registry=registry)


def _record_for_font(font_name, registry=None):
    registry = registry or load_font_registry(write_back=False)
    fonts = registry.get("fonts", {}) if isinstance(registry, dict) else {}
    target_key = _key(font_name)
    for name, record in fonts.items():
        aliases = record.get("aliases", []) if isinstance(record, dict) else []
        alias_keys = {_key(alias) for alias in aliases or []}
        if _key(name) == target_key or target_key in alias_keys:
            result = copy.deepcopy(record) if isinstance(record, dict) else {}
            result["font"] = name
            result["status"] = result.get("status") or STATUS_REVIEW
            result["status_label"] = STATUS_LABELS.get(result["status"], STATUS_LABELS[STATUS_REVIEW])
            return result
    return {
        "font": str(font_name or "").strip() or "Unknown",
        "status": STATUS_REVIEW,
        "status_label": STATUS_LABELS[STATUS_REVIEW],
        "category": "unregistered_review",
        "style_class": _infer_style_class(font_name),
        "source": "",
        "commercial_use": "unknown",
        "notes": "Not bundled. Treat as personal preview only until license proof is recorded; other users must install or provide this font themselves.",
    }


def _collect_style_font(style, output):
    if not isinstance(style, dict):
        return
    name = str(style.get("font", "") or "").strip()
    if name:
        output.setdefault(_key(name), name)


def collect_project_fonts(project_data):
    found = {}
    if not isinstance(project_data, dict):
        return []

    edit_state = project_data.get("room_state", {}).get("edit_room", {})
    if isinstance(edit_state, dict):
        _collect_style_font(edit_state.get("default_style", {}), found)
        for sub in edit_state.get("subs_data", []) or []:
            if isinstance(sub, dict):
                _collect_style_font(sub.get("style", sub), found)

    for sub in project_data.get("subs_data", []) or []:
        if isinstance(sub, dict):
            _collect_style_font(sub.get("style", sub), found)

    return sorted(found.values(), key=lambda item: item.casefold())


def audit_project_fonts(project_data, registry=None):
    registry = registry or load_font_registry(write_back=False)
    rows = []
    summary = {
        STATUS_APPROVED: 0,
        STATUS_OPEN: 0,
        STATUS_SYSTEM: 0,
        STATUS_NONCOMMERCIAL: 0,
        STATUS_REVIEW: 0,
    }
    for font_name in collect_project_fonts(project_data):
        row = _record_for_font(font_name, registry)
        summary[row["status"]] = summary.get(row["status"], 0) + 1
        rows.append(row)
    return {
        "fonts": rows,
        "summary": summary,
        "needs_review": [row for row in rows if row.get("status") in (STATUS_REVIEW, STATUS_SYSTEM, STATUS_NONCOMMERCIAL)],
    }
