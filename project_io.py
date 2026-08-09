# ==========================================
# 文件名: project_io.py (支持二级目录与封面抓取)
# ==========================================
import os
import copy
import json
from datetime import datetime
import shutil
from app_config import get_output_resolution, load_app_config

PROJECT_VERSION = 4
ASSETS_DIR = "assets"
PROJECT_DIR_EXCLUDES = {ASSETS_DIR, "fonts", "__pycache__"}
PROJECT_RECENT_FOLDERS_KEY = "project_hall_recent_folders"
PROJECT_RECENT_REELS_KEY = "project_hall_recent_reels"


def _safe_media_filename(source_path, fallback="media"):
    name = os.path.basename(source_path or "") or fallback
    stem, ext = os.path.splitext(name)
    safe_stem = "".join(c for c in stem if c not in r'\/:*?"<>|').strip().strip(". ")
    safe_ext = "".join(c for c in ext if c not in r'\/:*?"<>|').strip()
    return f"{safe_stem or fallback}{safe_ext}"


def _is_inside_dir(path, folder):
    try:
        path_abs = os.path.abspath(path)
        folder_abs = os.path.abspath(folder)
        return os.path.commonpath([path_abs, folder_abs]) == folder_abs
    except Exception:
        return False


def _project_asset_rel_path(project_dir, media_path):
    if not media_path or not project_dir or not _is_inside_dir(media_path, project_dir):
        return ""
    return os.path.relpath(media_path, project_dir).replace("\\", "/")


def _resolve_media_path(project_dir, path, rel_path):
    if path and os.path.exists(path):
        return path
    if project_dir and rel_path:
        candidate = os.path.abspath(os.path.join(project_dir, rel_path.replace("/", os.sep)))
        if os.path.exists(candidate):
            return candidate
    return path


def _resolve_project_media_paths(data):
    project_dir = data.get("project_dir") or os.path.dirname(data.get("project_path", ""))
    if not project_dir:
        return data

    edit_state = data.get("room_state", {}).get("edit_room", {})
    if not isinstance(edit_state, dict):
        return data

    for clip in edit_state.get("video_clips", []) or []:
        if not isinstance(clip, dict):
            continue
        rel_path = clip.get("cloud_rel_path") or clip.get("asset_rel_path") or ""
        clip["path"] = _resolve_media_path(project_dir, clip.get("path", ""), rel_path)

    audio_rel = edit_state.get("audio_cloud_rel_path") or edit_state.get("audio_asset_rel_path") or ""
    if edit_state.get("audio_path") or audio_rel:
        edit_state["audio_path"] = _resolve_media_path(project_dir, edit_state.get("audio_path", ""), audio_rel)

    music_rel = edit_state.get("music_cloud_rel_path") or edit_state.get("music_asset_rel_path") or ""
    if edit_state.get("music_path") or music_rel:
        edit_state["music_path"] = _resolve_media_path(project_dir, edit_state.get("music_path", ""), music_rel)

    return data


def _backfill_edit_room_from_legacy_fields(merged, source):
    room_state = merged.setdefault("room_state", {})
    edit_state = room_state.setdefault("edit_room", {})
    media_files = source.get("media_files", {}) if isinstance(source.get("media_files"), dict) else {}

    legacy_clips = []
    if isinstance(source.get("timeline"), list) and source.get("timeline"):
        legacy_clips = source.get("timeline")
    elif isinstance(media_files.get("video_clips"), list) and media_files.get("video_clips"):
        legacy_clips = media_files.get("video_clips")

    legacy_subs = source.get("subs_data", []) if isinstance(source.get("subs_data"), list) else []

    if not edit_state.get("video_clips") and legacy_clips:
        edit_state["video_clips"] = copy.deepcopy(legacy_clips)
    if not edit_state.get("subs_data") and legacy_subs:
        edit_state["subs_data"] = copy.deepcopy(legacy_subs)
    if not edit_state.get("audio_path") and media_files.get("audio_path"):
        edit_state["audio_path"] = media_files.get("audio_path", "")
    if not edit_state.get("audio_cloud_rel_path") and media_files.get("audio_cloud_rel_path"):
        edit_state["audio_cloud_rel_path"] = media_files.get("audio_cloud_rel_path", "")
    if not edit_state.get("audio_asset_rel_path") and media_files.get("audio_asset_rel_path"):
        edit_state["audio_asset_rel_path"] = media_files.get("audio_asset_rel_path", "")
    if not edit_state.get("music_path") and media_files.get("music_path"):
        edit_state["music_path"] = media_files.get("music_path", "")
    if not edit_state.get("music_cloud_rel_path") and media_files.get("music_cloud_rel_path"):
        edit_state["music_cloud_rel_path"] = media_files.get("music_cloud_rel_path", "")
    if not edit_state.get("music_asset_rel_path") and media_files.get("music_asset_rel_path"):
        edit_state["music_asset_rel_path"] = media_files.get("music_asset_rel_path", "")

    for key in ("duration", "resolution", "v_scale", "v_volume", "a_volume", "music_volume", "video_mask_enabled", "video_mask_color", "video_mask_alpha", "music_dur", "music_match_duration", "music_loop", "chunk_mode", "timing_mode", "fill_subtitle_gaps"):
        if key in source and source.get(key) not in (None, ""):
            try:
                current_duration = float(str(edit_state.get("duration", 0) or 0).replace(",", "."))
            except Exception:
                current_duration = 0.0
            if key != "duration" or current_duration <= 10.0:
                edit_state[key] = copy.deepcopy(source.get(key))

    if edit_state.get("subs_data"):
        try:
            max_end = max(float(s.get("end", 0) or 0) for s in edit_state.get("subs_data", []) if isinstance(s, dict))
            if max_end > float(edit_state.get("duration", 0) or 0):
                edit_state["duration"] = max_end
        except Exception:
            pass

    merged["subs_data"] = copy.deepcopy(edit_state.get("subs_data", []))
    merged["timeline"] = copy.deepcopy(edit_state.get("video_clips", []))
    merged_media = merged.setdefault("media_files", {})
    merged_media["video_clips"] = copy.deepcopy(edit_state.get("video_clips", []))
    merged_media["audio_path"] = edit_state.get("audio_path", "")
    merged_media["music_path"] = edit_state.get("music_path", "")
    if edit_state.get("audio_cloud_rel_path"):
        merged_media["audio_cloud_rel_path"] = edit_state.get("audio_cloud_rel_path")
    if edit_state.get("audio_asset_rel_path"):
        merged_media["audio_asset_rel_path"] = edit_state.get("audio_asset_rel_path")
    if edit_state.get("music_cloud_rel_path"):
        merged_media["music_cloud_rel_path"] = edit_state.get("music_cloud_rel_path")
    if edit_state.get("music_asset_rel_path"):
        merged_media["music_asset_rel_path"] = edit_state.get("music_asset_rel_path")
    return merged


def copy_media_to_project_assets(project_data, source_path):
    project_data = ensure_project_schema(project_data, project_data.get("project_path") if isinstance(project_data, dict) else None)
    source_path = os.path.abspath(source_path) if source_path else ""
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(source_path or "empty media path")

    project_dir = project_data.get("project_dir") or os.path.dirname(project_data.get("project_path", ""))
    if not project_dir:
        raise ValueError("Project directory is not available.")
    os.makedirs(project_dir, exist_ok=True)

    if _is_inside_dir(source_path, project_dir):
        return source_path, False, _project_asset_rel_path(project_dir, source_path)

    assets_dir = os.path.join(project_dir, ASSETS_DIR)
    os.makedirs(assets_dir, exist_ok=True)

    safe_name = _safe_media_filename(source_path)
    stem, ext = os.path.splitext(safe_name)
    target = os.path.join(assets_dir, safe_name)
    n = 2
    while os.path.exists(target):
        try:
            if os.path.samefile(source_path, target):
                return target, False, _project_asset_rel_path(project_dir, target)
        except Exception:
            pass
        target = os.path.join(assets_dir, f"{stem}-{n}{ext}")
        n += 1

    shutil.copy2(source_path, target)
    return target, True, _project_asset_rel_path(project_dir, target)


def sync_project_assets_to_project_dir(project_data):
    project_data = ensure_project_schema(project_data, project_data.get("project_path") if isinstance(project_data, dict) else None)
    report = {"copied": [], "missing": [], "already_local": []}

    edit_state = project_data.setdefault("room_state", {}).setdefault("edit_room", {})
    for clip in edit_state.get("video_clips", []) or []:
        if not isinstance(clip, dict):
            continue
        old_path = clip.get("path", "")
        if not old_path:
            continue
        try:
            new_path, copied, rel_path = copy_media_to_project_assets(project_data, old_path)
            clip["path"] = new_path
            if rel_path:
                clip["cloud_rel_path"] = rel_path
            report["copied" if copied else "already_local"].append(new_path)
        except FileNotFoundError:
            report["missing"].append(old_path)

    audio_path = edit_state.get("audio_path", "")
    if audio_path:
        try:
            new_path, copied, rel_path = copy_media_to_project_assets(project_data, audio_path)
            edit_state["audio_path"] = new_path
            if rel_path:
                edit_state["audio_cloud_rel_path"] = rel_path
            report["copied" if copied else "already_local"].append(new_path)
        except FileNotFoundError:
            report["missing"].append(audio_path)

    music_path = edit_state.get("music_path", "")
    if music_path:
        try:
            new_path, copied, rel_path = copy_media_to_project_assets(project_data, music_path)
            edit_state["music_path"] = new_path
            if rel_path:
                edit_state["music_cloud_rel_path"] = rel_path
            report["copied" if copied else "already_local"].append(new_path)
        except FileNotFoundError:
            report["missing"].append(music_path)

    project_data["subs_data"] = copy.deepcopy(edit_state.get("subs_data", []))
    project_data["timeline"] = copy.deepcopy(edit_state.get("video_clips", []))
    media_files = project_data.setdefault("media_files", {})
    media_files["video_clips"] = copy.deepcopy(edit_state.get("video_clips", []))
    media_files["audio_path"] = edit_state.get("audio_path", "")
    media_files["music_path"] = edit_state.get("music_path", "")
    if edit_state.get("audio_cloud_rel_path"):
        media_files["audio_cloud_rel_path"] = edit_state.get("audio_cloud_rel_path")
    if edit_state.get("music_cloud_rel_path"):
        media_files["music_cloud_rel_path"] = edit_state.get("music_cloud_rel_path")

    path = project_data.get("project_path")
    if path:
        project_data = save_project(path, project_data)
    return project_data, report

def _base_project_data(path, project_type, project_name):
    return {
        "project_name": project_name,
        "project_path": path,
        "project_dir": os.path.dirname(path),
        "project_type": project_type,
        "project_version": PROJECT_VERSION,
        "cover_img": f"{project_name}_cover.jpg", # 每个 Reel 独立的封面图
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "media_files": {},
        "room_state": {
            "edit_room": {
                "video_clips": [], "audio_path": "", "music_path": "", "subs_data": [],
                "a_trim": [0.0, 0.0], "audio_source_in": 0.0,
                "duration": 10.0, "resolution": get_output_resolution(),
                "v_scale": 100, "v_volume": 100, "a_volume": 100, "music_volume": 35,
                "video_mask_enabled": False, "video_mask_color": "#000000", "video_mask_alpha": 35,
                "music_dur": 0.0, "music_match_duration": 0.0, "music_loop": True,
                "chunk_mode": "双行大段 (约10字，智能折行)",
                "timing_mode": "J Cut (字幕稍后收尾)",
                "fill_subtitle_gaps": True,
                "default_pos_x": 0.0, "default_pos_y": 25.0, "default_style": {}
            },
            "design_room": {}
        },
        "subs_data": [],
        "timeline": []
    }

def ensure_project_schema(data, path=None):
    data = copy.deepcopy(data) if data else {}
    project_type = data.get("project_type", "edit_room")
    project_path = path or data.get("project_path", "")
    project_name = data.get("project_name", os.path.basename(project_path).replace(".scomp", "") if project_path else "未命名Reel")

    base = _base_project_data(project_path, project_type, project_name)
    merged = copy.deepcopy(base)
    merged.update(data)

    merged["project_path"] = project_path or merged.get("project_path", "")
    merged["project_dir"] = os.path.dirname(merged["project_path"]) if merged["project_path"] else ""
    merged["project_type"] = project_type
    merged["project_version"] = PROJECT_VERSION
    merged["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    room_state = copy.deepcopy(base["room_state"])
    for room_name, room_payload in data.get("room_state", {}).items():
        if isinstance(room_payload, dict) and isinstance(room_state.get(room_name), dict):
            merged_room = copy.deepcopy(room_state[room_name])
            merged_room.update(room_payload)
            room_state[room_name] = merged_room
        else:
            room_state[room_name] = copy.deepcopy(room_payload)
    merged["room_state"] = room_state
    merged = _backfill_edit_room_from_legacy_fields(merged, data)
    merged = _resolve_project_media_paths(merged)

    return merged

# 👑 获取所有项目文件夹 (一级目录)
def get_project_folder_paths(workspace, recursive=False, max_depth=None):
    folders = []
    if not os.path.exists(workspace):
        return folders

    workspace_abs = os.path.abspath(workspace)

    def is_visible_dir(name):
        return bool(name) and not name.startswith(".") and name not in PROJECT_DIR_EXCLUDES

    if not recursive:
        for item in os.listdir(workspace_abs):
            if not is_visible_dir(item):
                continue
            p = os.path.join(workspace_abs, item)
            if os.path.isdir(p):
                folders.append(item)
        return sorted(folders, key=lambda item: item.casefold())

    for root, dirs, _ in os.walk(workspace_abs):
        if os.path.normcase(os.path.abspath(root)) == os.path.normcase(workspace_abs):
            current_depth = 0
        else:
            root_rel = os.path.relpath(root, workspace_abs)
            current_depth = len(root_rel.split(os.sep))

        kept_dirs = []
        for name in sorted(dirs, key=lambda item: item.casefold()):
            if not is_visible_dir(name):
                continue
            child_depth = current_depth + 1
            if max_depth is not None and child_depth > max_depth:
                continue
            child_path = os.path.join(root, name)
            rel_path = os.path.relpath(child_path, workspace_abs)
            folders.append(rel_path)
            if max_depth is None or child_depth < max_depth:
                kept_dirs.append(name)
        dirs[:] = kept_dirs

    return sorted(folders, key=lambda item: [part.casefold() for part in item.split(os.sep)])


def get_project_folders(workspace):
    return get_project_folder_paths(workspace, recursive=False)

# 👑 获取某个文件夹下的所有 Reels (二级目录)
def get_reels_in_folder(folder_path, recursive=False):
    reels = []
    if os.path.exists(folder_path):
        if recursive:
            walker = os.walk(folder_path)
            for root, dirs, files in walker:
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("assets", "fonts", "__pycache__")]
                for item in files:
                    if item.lower().endswith(".scomp"):
                        p = os.path.join(root, item)
                        reels.append({"path": p, "mtime": os.path.getmtime(p)})
        else:
            for item in os.listdir(folder_path):
                if item.lower().endswith(".scomp"):
                    p = os.path.join(folder_path, item)
                    reels.append({"path": p, "mtime": os.path.getmtime(p)})
    reels.sort(key=lambda x: x["mtime"], reverse=True)
    return [r["path"] for r in reels]

def create_reel(project_dir, reel_name, project_type="edit_room"):
    if not os.path.exists(project_dir):
        os.makedirs(project_dir)
    safe_name = "".join(c for c in reel_name if c not in r'\/:*?"<>|')
    if not safe_name:
        raise ValueError("Reel name cannot be empty after removing invalid filename characters.")
    scomp_path = os.path.join(project_dir, f"{safe_name}.scomp")
    if os.path.exists(scomp_path):
        raise FileExistsError(f"Reel already exists: {scomp_path}")
    data = ensure_project_schema(_base_project_data(scomp_path, project_type, safe_name), scomp_path)
    save_project(scomp_path, data)
    return data

def create_project(path, project_type="edit_room"):
    project_dir = os.path.dirname(path)
    if project_dir and not os.path.exists(project_dir):
        os.makedirs(project_dir)
    project_name = os.path.splitext(os.path.basename(path))[0] or "未命名Reel"
    data = _base_project_data(path, project_type, project_name)
    if project_type == "scroll_room":
        data.setdefault("room_state", {})["scroll_room"] = {"pages": []}
    return save_project(path, data)

def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ensure_project_schema(data, path)

def save_project(path, data):
    normalized = ensure_project_schema(data, path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
    return normalized

def update_room_state(project_data, room_name, room_payload):
    project_data = ensure_project_schema(project_data, project_data.get("project_path"))
    project_data.setdefault("room_state", {})[room_name] = copy.deepcopy(room_payload)

    if room_name == "edit_room":
        project_tag = str(room_payload.get("project_tag", "") or "").strip()
        if project_tag:
            project_data["project_tag"] = project_tag
            project_data["tags"] = [project_tag]
        project_data["subs_data"] = copy.deepcopy(room_payload.get("subs_data", []))
        project_data["timeline"] = copy.deepcopy(room_payload.get("video_clips", []))
        media_files = project_data.setdefault("media_files", {})
        media_files["video_clips"] = copy.deepcopy(room_payload.get("video_clips", []))
        media_files["audio_path"] = room_payload.get("audio_path", "")
        media_files["music_path"] = room_payload.get("music_path", "")
        if room_payload.get("cover_img"):
            project_data["cover_img"] = room_payload.get("cover_img")

    path = project_data.get("project_path")
    if path:
        project_data = save_project(path, project_data)
    return project_data


def _recent_project_folders_for_workspace(workspace, limit=10):
    try:
        config = load_app_config()
    except Exception:
        config = {}
    raw = config.get(PROJECT_RECENT_FOLDERS_KEY, [])
    if not raw:
        raw = [os.path.dirname(path) for path in config.get(PROJECT_RECENT_REELS_KEY, []) if isinstance(path, str)]
    results = []
    seen = set()
    for folder in raw if isinstance(raw, list) else []:
        if not isinstance(folder, str) or not folder.strip():
            continue
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder) or not _is_inside_dir(folder, workspace):
            continue
        key = os.path.normcase(folder)
        if key in seen:
            continue
        seen.add(key)
        results.append(folder)
        if len(results) >= limit:
            break
    return results
def load_or_create_default_project(workspace=None):
    workspace = workspace or os.path.join(os.getcwd(), "MyWorkspace")
    if not os.path.exists(workspace): os.makedirs(workspace)

    for recent_folder in _recent_project_folders_for_workspace(workspace):
        reels = get_reels_in_folder(recent_folder, recursive=True)
        if reels:
            return load_project(reels[0])
        return create_reel(recent_folder, "第一条Reel", "edit_room")

    folders = get_project_folders(workspace)
    if not folders:
        default_folder = os.path.join(workspace, "默认项目")
        os.makedirs(default_folder)
        return create_reel(default_folder, "第一条Reel", "edit_room")

    first_folder = os.path.join(workspace, folders[0])
    reels = get_reels_in_folder(first_folder, recursive=True)
    if reels: return load_project(reels[0])
    return create_reel(first_folder, "第一条Reel", "edit_room")
