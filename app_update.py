import os
import re
import shutil
from datetime import datetime

import requests

from app_config import load_app_config, save_app_config


DEFAULT_UPDATE_CONFIG = {
    "repo": "",
    "current_version": "0.1.16",
    "auto_check": True,
    "download_dir": "",
}


def load_update_config():
    config = load_app_config().get("updates", {})
    if not isinstance(config, dict):
        config = {}
    merged = dict(DEFAULT_UPDATE_CONFIG)
    merged.update(config)
    return merged


def save_update_config(update_config):
    config = load_app_config()
    merged = dict(DEFAULT_UPDATE_CONFIG)
    merged.update(update_config or {})
    config["updates"] = merged
    save_app_config(config)
    return merged


def normalize_repo(value):
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)", value, re.I)
    if match:
        value = match.group(1)
    value = value.strip("/").replace(".git", "")
    return value if "/" in value else ""


def version_tuple(value):
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer_version(latest, current):
    return version_tuple(latest) > version_tuple(current)


def release_download_dir(config=None):
    config = config or load_update_config()
    folder = str(config.get("download_dir") or "").strip()
    if folder:
        return folder
    return os.path.join(os.getcwd(), "updates")


def check_latest_release(repo, current_version="", include_prerelease=False, timeout=12):
    repo = normalize_repo(repo)
    if not repo:
        raise ValueError("请先填写 GitHub 仓库，格式例如 owner/repo。")

    if include_prerelease:
        url = f"https://api.github.com/repos/{repo}/releases"
        response = requests.get(url, headers={"User-Agent": "subtitle-composer"}, timeout=timeout)
        response.raise_for_status()
        releases = response.json()
        release = next((item for item in releases if not item.get("draft")), None)
    else:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(url, headers={"User-Agent": "subtitle-composer"}, timeout=timeout)
        response.raise_for_status()
        release = response.json()

    if not isinstance(release, dict) or not release.get("tag_name"):
        raise RuntimeError("没有找到可用的 GitHub Release。")

    tag = str(release.get("tag_name") or "")
    assets = release.get("assets", []) if isinstance(release.get("assets"), list) else []
    return {
        "repo": repo,
        "tag_name": tag,
        "name": release.get("name") or tag,
        "html_url": release.get("html_url") or "",
        "published_at": release.get("published_at") or "",
        "body": release.get("body") or "",
        "assets": [
            {
                "name": asset.get("name") or "",
                "size": int(asset.get("size") or 0),
                "browser_download_url": asset.get("browser_download_url") or "",
            }
            for asset in assets
            if asset.get("browser_download_url")
        ],
        "is_newer": is_newer_version(tag, current_version),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def pick_release_asset(release, platform_hint=None):
    assets = release.get("assets", []) if isinstance(release, dict) else []
    if not assets:
        return None
    hint = (platform_hint or ("windows" if os.name == "nt" else "macos")).lower()
    scored = []
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        score = 0
        if hint in name:
            score += 10
        if name.endswith(".zip"):
            score += 5
        if "x64" in name or "arm64" in name:
            score += 1
        scored.append((score, asset))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def download_asset(asset, target_dir, progress_callback=None, timeout=30):
    url = asset.get("browser_download_url") if isinstance(asset, dict) else ""
    name = asset.get("name") if isinstance(asset, dict) else ""
    if not url or not name:
        raise ValueError("Release 资源信息不完整，无法下载。")
    os.makedirs(target_dir, exist_ok=True)
    final_path = os.path.join(target_dir, name)
    temp_path = final_path + ".part"
    with requests.get(url, stream=True, headers={"User-Agent": "subtitle-composer"}, timeout=timeout) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or asset.get("size") or 0)
        done = 0
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if callable(progress_callback):
                    progress_callback(done, total)
    if os.path.exists(final_path):
        os.remove(final_path)
    shutil.move(temp_path, final_path)
    if callable(progress_callback):
        progress_callback(os.path.getsize(final_path), os.path.getsize(final_path))
    return final_path
