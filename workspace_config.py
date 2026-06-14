import json
import os

from app_config import CONFIG_FILE, load_app_config, save_app_config

WORKSPACE_MODE_LOCAL = "local"
WORKSPACE_MODE_CLOUD = "cloud"
CLOUD_LINK_MODE_COLLAB = "collaborate"
CLOUD_LINK_MODE_COPY = "copy_to_my_drive"
CLOUD_LINK_MODE_RENDER = "render_only"


def default_local_workspace():
    return os.path.join(os.getcwd(), "MyWorkspace")


def _load_settings():
    return load_app_config()


def _save_settings(data):
    try:
        save_app_config(data)
    except Exception:
        pass


def get_workspace_config():
    data = _load_settings()
    workspace = data.get("workspace", {}) if isinstance(data.get("workspace"), dict) else {}
    mode = workspace.get("mode", WORKSPACE_MODE_LOCAL)
    if mode not in (WORKSPACE_MODE_LOCAL, WORKSPACE_MODE_CLOUD):
        mode = WORKSPACE_MODE_LOCAL
    local_path = workspace.get("local_path") or default_local_workspace()
    cloud_path = workspace.get("cloud_path") or ""
    cloud_link = workspace.get("cloud_link") or ""
    cloud_link_mode = workspace.get("cloud_link_mode") or CLOUD_LINK_MODE_COLLAB
    if cloud_link_mode not in (CLOUD_LINK_MODE_COLLAB, CLOUD_LINK_MODE_COPY, CLOUD_LINK_MODE_RENDER):
        cloud_link_mode = CLOUD_LINK_MODE_COLLAB
    return {
        "mode": mode,
        "local_path": local_path,
        "cloud_path": cloud_path,
        "cloud_link": cloud_link,
        "cloud_link_mode": cloud_link_mode,
    }


def save_workspace_config(mode=None, local_path=None, cloud_path=None, cloud_link=None, cloud_link_mode=None):
    data = _load_settings()
    cfg = get_workspace_config()
    if mode in (WORKSPACE_MODE_LOCAL, WORKSPACE_MODE_CLOUD):
        cfg["mode"] = mode
    if local_path:
        cfg["local_path"] = local_path
    if cloud_path is not None:
        cfg["cloud_path"] = cloud_path
    if cloud_link is not None:
        cfg["cloud_link"] = cloud_link
    if cloud_link_mode in (CLOUD_LINK_MODE_COLLAB, CLOUD_LINK_MODE_COPY, CLOUD_LINK_MODE_RENDER):
        cfg["cloud_link_mode"] = cloud_link_mode
    data["workspace"] = cfg
    _save_settings(data)
    return cfg


def get_active_workspace():
    cfg = get_workspace_config()
    if cfg["mode"] == WORKSPACE_MODE_CLOUD and cfg.get("cloud_path"):
        return cfg["cloud_path"]
    return cfg["local_path"] or default_local_workspace()
