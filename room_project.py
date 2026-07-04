# ==========================================
# 文件名: room_project.py (加入项目重命名与删除功能)
# ==========================================
import os
import shutil
import zipfile
import html
import json
import re
import random
import copy
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QMessageBox, QFrame, QScrollArea, QGridLayout, QInputDialog, QGraphicsDropShadowEffect, QSplitter,
    QFileDialog, QDialog, QComboBox, QTextEdit, QLineEdit, QDialogButtonBox, QFormLayout,
    QMenu, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QUrl, QMimeData, QTimer
from PyQt6.QtGui import QPixmap, QCursor, QFont, QIcon, QDesktopServices, QDrag

from project_io import create_reel, load_project, get_project_folders, get_project_folder_paths, get_reels_in_folder, save_project, sync_project_assets_to_project_dir
from ui_components import default_signature_config, normalize_signature_config
from project_ui_kit import ProjectMetrics, compact_project_grid_columns, project_card_width
from media_probe import get_exact_duration, get_video_stream_duration
from app_config import load_app_config, save_app_config
from app_storage import read_json_file, resolve_user_file
from font_assets import font_package_entries_for_families
from font_registry import is_safe_font
from caption_presets import REFERENCE_NARRATIVE_CHUNK_MODE, merge_built_in_style_presets
from workspace_config import (
    CLOUD_LINK_MODE_COLLAB,
    CLOUD_LINK_MODE_COPY,
    CLOUD_LINK_MODE_RENDER,
    WORKSPACE_MODE_CLOUD,
    WORKSPACE_MODE_LOCAL,
    get_active_workspace,
    get_workspace_config,
    save_workspace_config,
)
from cloud_workspace import (
    acquire_project_lock,
    ensure_cloud_workspace,
    get_cloud_identity,
    get_share_config,
    release_project_lock,
    save_cloud_identity,
    set_share_config,
    update_manifest_from_workspace,
)
from project_audit import format_scan_report, scan_folder, scan_to_json, scan_workspace
from project_board_interactions import contiguous_range_ids
from project_sidebar_state import SidebarState, apply_sidebar_state


PROJECT_HALL_THEME_KEY = "project_hall_theme"
PROJECT_SIDEBAR_EXPANDED_KEY = "project_hall_sidebar_expanded"
PROJECT_RECENT_FOLDERS_KEY = "project_hall_recent_folders"
PROJECT_RECENT_REELS_KEY = "project_hall_recent_reels"
PROJECT_HOME_NODE = "__project_hall_home__"
PROJECT_RECENT_LIMIT = 10
PROJECT_FOLDER_SCAN_CAP = 999
REEL_DRAG_MIME = "application/x-subtitle-composer-reel"
REEL_GROUP_MARKER = ".subtitle_reel_group"
TRASH_DIR_NAME = ".subtitle_trash"
PROJECT_GRID_METRICS = ProjectMetrics(card_min_width=166, card_max_width=196, card_height=214, grid_gap=14)
PROJECT_COMPACT_CARD_HEIGHT = 82
PROJECT_FOLDER_CARD_HEIGHT = 184
PROJECT_ACTION_CARD_HEIGHT = 154
PROJECT_STYLE_PRESETS_FILE = resolve_user_file("style_presets.json", legacy_root=os.getcwd(), kind="config")
PROJECT_SIGNATURE_PRESETS_FILE = resolve_user_file("signature_presets.json", legacy_root=os.getcwd(), kind="config")
PROJECT_STYLE_POSITION_KEY = "__position__"
PROJECT_MEDIA_EXTS = (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png")
PROJECT_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
PROJECT_MUSIC_ASSIGN_MODES = (
    ("顺序循环", "cycle"),
    ("随机分配", "random"),
    ("固定第一首", "first"),
)
PROJECT_CHUNK_MODES = [
    "短句快速 (1-3字)",
    "智能重点短句 (3-4词为主)",
    "智能听译 (4-7词，适配双行按词)",
    REFERENCE_NARRATIVE_CHUNK_MODE,
    "自然短句 (1-4词)",
    "双词节奏 (2词/句)",
    "三词短句 (3词/句)",
    "四词短句 (4词/句)",
    "双行大段 (约10字，智能折行)",
    "单字轰炸 (1字/句)",
]
PROJECT_TIMING_MODES = ["L Cut (字幕提前进入)", "J Cut (字幕稍后收尾)", "对齐声音 (按停顿)"]
PROJECT_HALL_THEMES = {
    "dark_star": {
        "name": "暗色星空",
        "bg": "#0f1220",
        "panel": "#171a2b",
        "panel_2": "#20243a",
        "card": "#232742",
        "card_hover": "#2b3150",
        "text": "#eef2ff",
        "muted": "#aeb8d6",
        "accent": "#8aa3ff",
        "accent_2": "#7fc7d9",
        "warn": "#d8b871",
        "danger": "#e98aa2",
        "border": "#3a4062",
        "input": "#121628",
        "selected": "#8aa3ff",
        "selected_text": "#0b1020",
        "hint": "#151a2e",
        "star": "#dbe7ff",
    },
    "light_care": {
        "name": "亮色护眼",
        "bg": "#f5f7f1",
        "panel": "#fbfcf7",
        "panel_2": "#edf3e7",
        "card": "#ffffff",
        "card_hover": "#eef5e9",
        "text": "#263226",
        "muted": "#687866",
        "accent": "#557b5f",
        "accent_2": "#6c8a59",
        "warn": "#8f7438",
        "danger": "#b45d65",
        "border": "#d8e0cf",
        "input": "#fffefa",
        "selected": "#cfe3c4",
        "selected_text": "#1e2b1f",
        "hint": "#edf4e8",
        "star": "#91a889",
    },
    "studio_ember": {
        "name": "Studio Ember",
        "bg": "#111417",
        "panel": "#171d20",
        "panel_2": "#20282b",
        "card": "#1d2427",
        "card_hover": "#263136",
        "text": "#f2f0e8",
        "muted": "#9ba8a6",
        "accent": "#55c2a8",
        "accent_2": "#ffb86b",
        "warn": "#ffd166",
        "danger": "#ff6b6b",
        "border": "#334044",
        "input": "#101719",
        "selected": "#55c2a8",
        "selected_text": "#081311",
        "hint": "#151c1f",
        "star": "#ffb86b",
    },
    "graphite_cut": {
        "name": "Graphite Cut",
        "bg": "#111315",
        "panel": "#171a1d",
        "panel_2": "#202428",
        "card": "#1b2025",
        "card_hover": "#252b31",
        "text": "#e8edf2",
        "muted": "#9aa4ad",
        "accent": "#6aa8ff",
        "accent_2": "#69d2c0",
        "warn": "#f2c572",
        "danger": "#ef6f7a",
        "border": "#303842",
        "input": "#0e1114",
        "selected": "#6aa8ff",
        "selected_text": "#07111f",
        "hint": "#15191e",
        "star": "#8fc0ff",
    },
    "colorist_teal": {
        "name": "Colorist Teal",
        "bg": "#0f1516",
        "panel": "#141d1f",
        "panel_2": "#1c292c",
        "card": "#182326",
        "card_hover": "#223337",
        "text": "#edf5f2",
        "muted": "#91a4a0",
        "accent": "#35c7b1",
        "accent_2": "#d8b46a",
        "warn": "#e4c365",
        "danger": "#ff7373",
        "border": "#2e464a",
        "input": "#0b1213",
        "selected": "#35c7b1",
        "selected_text": "#061312",
        "hint": "#121b1d",
        "star": "#d8b46a",
    },
    "paper_cut": {
        "name": "Paper Cut",
        "bg": "#f6f3ed",
        "panel": "#fffaf3",
        "panel_2": "#eee7dc",
        "card": "#fffdf8",
        "card_hover": "#efe7d9",
        "text": "#252a2b",
        "muted": "#697170",
        "accent": "#2f7f76",
        "accent_2": "#b96f40",
        "warn": "#9a6b1f",
        "danger": "#b65058",
        "border": "#d8d0c2",
        "input": "#fffdfa",
        "selected": "#c9e4dc",
        "selected_text": "#10201d",
        "hint": "#eee9df",
        "star": "#b96f40",
    },
}


def project_audio_file_filter():
    return "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"


def project_media_file_filter():
    return "Video/Image Files (*.mp4 *.mov *.webm *.jpg *.jpeg *.png)"


def split_project_style_preset(raw):
    if not isinstance(raw, dict):
        return {}, None
    if isinstance(raw.get("style"), dict):
        style = copy.deepcopy(raw.get("style") or {})
        position = raw.get("position") or raw.get(PROJECT_STYLE_POSITION_KEY)
    else:
        style = {
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key not in (PROJECT_STYLE_POSITION_KEY, "position")
        }
        position = raw.get(PROJECT_STYLE_POSITION_KEY) or raw.get("position")
    if isinstance(position, dict):
        try:
            position = {
                "pos_x": float(position.get("pos_x", 0.0)),
                "pos_y": float(position.get("pos_y", 25.0)),
            }
        except Exception:
            position = None
    else:
        position = None
    return style, position


GOOGLE_DRIVE_HINT_NAMES = (
    "Google Drive",
    "My Drive",
    "Shared drives",
    "Il mio Drive",
    "Drive condivisi",
    "Mi unidad",
    "Mon Drive",
    "Meine Ablage",
)


def _parse_google_drive_link(url):
    text = (url or "").strip()
    if not text:
        return {"kind": "", "id": "", "is_drive": False}
    patterns = [
        ("folder", r"/folders/([A-Za-z0-9_-]+)"),
        ("file", r"/file/d/([A-Za-z0-9_-]+)"),
        ("open", r"[?&]id=([A-Za-z0-9_-]+)"),
        ("resource", r"/drive/(?:u/\d+/)?(?:folders|shared-drives)/([A-Za-z0-9_-]+)"),
    ]
    for kind, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return {"kind": kind, "id": match.group(1), "is_drive": "drive.google.com" in text.lower()}
    return {"kind": "unknown", "id": "", "is_drive": "drive.google.com" in text.lower() or "docs.google.com" in text.lower()}


def _cloud_link_mode_label(mode):
    return {
        CLOUD_LINK_MODE_COLLAB: "协作编辑",
        CLOUD_LINK_MODE_COPY: "复制到我的云盘",
        CLOUD_LINK_MODE_RENDER: "仅渲染下载",
    }.get(mode, "协作编辑")


def _is_path_inside(child, parent):
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except Exception:
        return False


def _looks_like_google_drive_path(path):
    if not path:
        return False
    norm = os.path.normcase(os.path.abspath(path))
    return any(os.path.normcase(name) in norm for name in GOOGLE_DRIVE_HINT_NAMES)


def _candidate_cover_paths(reel_path, project_data=None):
    project_data = project_data if isinstance(project_data, dict) else {}
    project_dir = project_data.get("project_dir") or os.path.dirname(reel_path)
    cover_rel = project_data.get("cover_img", "")
    candidates = [reel_path.replace(".scomp", "_cover.jpg")]
    if cover_rel:
        candidates.insert(0, cover_rel if os.path.isabs(cover_rel) else os.path.join(project_dir, cover_rel))
    seen = set()
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path)) if path else ""
        if path and key not in seen:
            seen.add(key)
            yield path


def find_reel_cover_path(reel_path, project_data=None):
    for cover_path in _candidate_cover_paths(reel_path, project_data):
        if os.path.exists(cover_path):
            return cover_path
    if project_data is None:
        try:
            data = load_project(reel_path)
        except Exception:
            data = {}
        for cover_path in _candidate_cover_paths(reel_path, data):
            if os.path.exists(cover_path):
                return cover_path
    return ""


def _iter_reel_paths_fast(folder_path, recursive=True, max_items=None):
    if not folder_path or not os.path.isdir(folder_path):
        return
    yielded = 0
    excluded = {"assets", "fonts", "__pycache__"}
    try:
        if recursive:
            for root, dirs, files in os.walk(folder_path):
                dirs[:] = [d for d in dirs if d and not d.startswith(".") and d not in excluded]
                for item in sorted(files, key=lambda value: value.casefold()):
                    if item.lower().endswith(".scomp"):
                        yield os.path.join(root, item)
                        yielded += 1
                        if max_items and yielded >= max_items:
                            return
        else:
            for item in sorted(os.listdir(folder_path), key=lambda value: value.casefold()):
                if item.lower().endswith(".scomp"):
                    yield os.path.join(folder_path, item)
                    yielded += 1
                    if max_items and yielded >= max_items:
                        return
    except Exception:
        return


def count_reels_fast(folder_path, recursive=True, cap=PROJECT_FOLDER_SCAN_CAP):
    count = 0
    for _ in _iter_reel_paths_fast(folder_path, recursive=recursive, max_items=cap):
        count += 1
    return count


def first_folder_cover_path(folder_path, max_reels=18):
    for reel_path in _iter_reel_paths_fast(folder_path, recursive=True, max_items=max(1, int(max_reels or 1))):
        cover_path = find_reel_cover_path(reel_path)
        if cover_path:
            return cover_path
    return ""


def _find_google_drive_candidates():
    candidates = []
    seen = set()

    def add(path):
        if path and os.path.isdir(path):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                candidates.append(os.path.abspath(path))

    home = os.path.expanduser("~")
    for name in GOOGLE_DRIVE_HINT_NAMES:
        add(os.path.join(home, name))
        add(os.path.join(home, "Google Drive", name))

    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if not os.path.exists(root):
                continue
            for name in GOOGLE_DRIVE_HINT_NAMES:
                add(os.path.join(root, name))

    return candidates


def _scan_workspace_summary(workspace):
    folders = get_project_folders(workspace)
    reel_count = 0
    missing_media = 0
    external_media = 0

    for folder_name in folders:
        folder_path = os.path.join(workspace, folder_name)
        for reel_path in get_reels_in_folder(folder_path, recursive=True):
            reel_count += 1
            try:
                project = load_project(reel_path)
            except Exception:
                continue

            edit_state = project.get("room_state", {}).get("edit_room", {})
            media_paths = []
            for clip in edit_state.get("video_clips", []) or []:
                media_paths.append(clip.get("path", ""))
            media_paths.append(edit_state.get("audio_path", ""))

            for media_path in media_paths:
                if not media_path:
                    continue
                if not os.path.exists(media_path):
                    missing_media += 1
                elif not _is_path_inside(media_path, workspace):
                    external_media += 1

    return {
        "folder_count": len(folders),
        "reel_count": reel_count,
        "missing_media": missing_media,
        "external_media": external_media,
    }


class ProjectFolderListWidget(QTreeWidget):
    files_dropped = pyqtSignal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(True)

    def _paths_from_event(self, event):
        if not event.mimeData().hasUrls():
            return []
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        paths = self._paths_from_event(event)
        if any(os.path.isdir(path) or path.lower().endswith(".scomp") for path in paths):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        paths = self._paths_from_event(event)
        if any(os.path.isdir(path) or path.lower().endswith(".scomp") for path in paths):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = self._paths_from_event(event)
        if paths:
            point = event.position().toPoint() if hasattr(event, "position") else event.pos()
            item = self.itemAt(point)
            target_folder = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
            self.files_dropped.emit(paths, target_folder)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class CloudJoinWizard(QDialog):
    def __init__(self, workspace_cfg, parent=None):
        super().__init__(parent)
        self.workspace_cfg = workspace_cfg or {}
        self.setWindowTitle("加入云端团队工程")
        self.resize(760, 620)
        self.setStyleSheet("""
            QDialog { background-color: #11111b; color: #cdd6f4; }
            QLabel { color: #cdd6f4; }
            QLineEdit, QTextEdit, QComboBox {
                background-color: #181825; color: #cdd6f4;
                border: 1px solid #313244; border-radius: 7px; padding: 8px;
            }
            QPushButton {
                background-color: #313244; color: #cdd6f4;
                border: none; border-radius: 7px; padding: 8px 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #45475a; }
        """)
        self.init_ui()
        self.run_checks()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("云端团队工程向导")
        title.setStyleSheet("font-size: 24px; font-weight: 900; color: #89b4fa;")
        layout.addWidget(title)

        intro = QLabel(
            "粘贴 Google Drive 工程链接后，选择使用方式：有编辑权限就加入协作工程；只有查看权限就复制到自己的云盘；"
            "只想渲染成品时可以走仅渲染下载。当前稳定版优先使用 Google Drive 桌面版同步目录，Google API 自动复制/下载接口会在此入口继续接入。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #a6adc8; background-color: #181825; border-radius: 8px; padding: 10px;")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.invite_input = QLineEdit()
        self.invite_input.setText(self.workspace_cfg.get("cloud_link", ""))
        self.invite_input.setPlaceholderText("粘贴 Google Drive 工程文件夹链接")
        self.invite_input.textChanged.connect(self.run_checks)
        invite_row = QHBoxLayout()
        invite_row.addWidget(self.invite_input, stretch=1)
        self.btn_open_invite = QPushButton("打开链接")
        self.btn_open_invite.clicked.connect(self.open_invite_link)
        invite_row.addWidget(self.btn_open_invite)
        form.addRow("共享链接", invite_row)

        self.link_mode_combo = QComboBox()
        self.link_mode_combo.addItem("有编辑权限：加入协作工程（推荐团队成员）", CLOUD_LINK_MODE_COLLAB)
        self.link_mode_combo.addItem("只有查看权限：复制到我的云盘后修改", CLOUD_LINK_MODE_COPY)
        self.link_mode_combo.addItem("只渲染下载：临时缓存工程素材", CLOUD_LINK_MODE_RENDER)
        saved_mode = self.workspace_cfg.get("cloud_link_mode", CLOUD_LINK_MODE_COLLAB)
        idx = self.link_mode_combo.findData(saved_mode)
        self.link_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.link_mode_combo.currentIndexChanged.connect(self.on_link_mode_changed)
        form.addRow("使用方式", self.link_mode_combo)

        identity = get_cloud_identity()
        self.email_input = QLineEdit(identity.get("email", ""))
        self.email_input.setPlaceholderText("每个成员自己的 Gmail，用于编辑锁和协作记录")
        self.name_input = QLineEdit(identity.get("name", ""))
        self.name_input.setPlaceholderText("显示名称，例如 Mia / Luca / Team-A")
        form.addRow("个人 Gmail", self.email_input)
        form.addRow("显示名称", self.name_input)

        self.folder_input = QLineEdit(self.workspace_cfg.get("cloud_path", ""))
        self.folder_input.setPlaceholderText("选择 Google Drive 桌面版同步出来的团队工程文件夹")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_input, stretch=1)
        self.btn_pick_folder = QPushButton("选择文件夹")
        self.btn_pick_folder.clicked.connect(self.select_workspace_folder)
        folder_row.addWidget(self.btn_pick_folder)
        form.addRow("云端文件夹", folder_row)

        layout.addLayout(form)

        action_row = QHBoxLayout()
        self.btn_open_drive_desktop = QPushButton("安装 Google Drive 桌面版")
        self.btn_open_drive_desktop.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.google.com/drive/download/")))
        self.btn_open_my_drive = QPushButton("登录/打开我的 Google Drive")
        self.btn_open_my_drive.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://drive.google.com/drive/my-drive")))
        action_row.addWidget(self.btn_open_drive_desktop)
        action_row.addWidget(self.btn_open_my_drive)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("color: #f9e2af; background-color: #1e1e2e; border-radius: 8px; padding: 10px;")
        layout.addWidget(self.mode_hint)

        step_box = QFrame()
        step_box.setStyleSheet("QFrame { background-color: #181825; border: 1px solid #313244; border-radius: 8px; }")
        step_layout = QVBoxLayout(step_box)
        step_layout.setContentsMargins(12, 12, 12, 12)
        step_layout.setSpacing(6)
        steps = [
            "1. 协作编辑：打开共享链接，确认自己是 Editor，然后选择 Google Drive 桌面版同步出来的工程目录。",
            "2. 复制修改：如果只是 Viewer，但允许下载/复制，可以先在 Google Drive 里复制到自己的云盘，再选择自己的同步目录。",
            "3. 仅渲染：后续 Google API 模块会直接下载到临时缓存；当前可以先手动下载工程包再导入渲染。",
            "4. 每个成员使用自己的 Gmail，软件用它写入编辑锁和协作记录，不建议多人共用一个账号。",
            "5. 完成检测后，软件会切换到云端版，并创建 .subtitle_cloud 协作元数据。",
        ]
        for text in steps:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #a6adc8;")
            step_layout.addWidget(lbl)
        layout.addWidget(step_box)

        check_header = QHBoxLayout()
        check_title = QLabel("检测结果")
        check_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #f9e2af;")
        check_header.addWidget(check_title)
        check_header.addStretch()
        self.btn_recheck = QPushButton("重新检测")
        self.btn_recheck.clicked.connect(self.run_checks)
        check_header.addWidget(self.btn_recheck)
        layout.addLayout(check_header)

        self.check_log = QTextEdit()
        self.check_log.setReadOnly(True)
        self.check_log.setMinimumHeight(150)
        layout.addWidget(self.check_log, stretch=1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("完成加入")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.on_link_mode_changed()

    def current_link_mode(self):
        return self.link_mode_combo.currentData() or CLOUD_LINK_MODE_COLLAB

    def on_link_mode_changed(self, *args):
        mode = self.current_link_mode()
        hints = {
            CLOUD_LINK_MODE_COLLAB: "协作编辑模式：需要对方给你编辑权限，并建议安装 Google Drive 桌面版。软件会直接读取同步目录里的同一份工程。",
            CLOUD_LINK_MODE_COPY: "复制副本模式：适合只有查看权限但允许复制/下载的用户。先把工程复制到自己的云盘，再选择自己的同步目录，修改不会影响原工程。",
            CLOUD_LINK_MODE_RENDER: "仅渲染下载模式：目标是不安装桌面版也能渲染。当前稳定版会记录链接和模式，Google API 下载模块接入后可直接下载到临时缓存。",
        }
        self.mode_hint.setText(hints.get(mode, hints[CLOUD_LINK_MODE_COLLAB]))
        if hasattr(self, "buttons"):
            ok_text = "保存渲染入口" if mode == CLOUD_LINK_MODE_RENDER else "完成加入"
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        self.run_checks()

    def _append_check(self, status, message):
        safe = html.escape(message)
        color = {
            "PASS": "#a6e3a1",
            "WARN": "#f9e2af",
            "FAIL": "#f38ba8",
            "INFO": "#89b4fa",
        }.get(status, "#cdd6f4")
        self.check_log.append(f"<span style='color:{color}; font-weight:700'>[{status}]</span> {safe}")

    def open_invite_link(self):
        url = self.invite_input.text().strip()
        if not url:
            QMessageBox.information(self, "需要链接", "请先粘贴管理员发来的 Google Drive 共享链接。")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        QDesktopServices.openUrl(QUrl(url))

    def select_workspace_folder(self):
        candidates = _find_google_drive_candidates()
        default_dir = self.folder_input.text().strip() or (candidates[0] if candidates else os.path.expanduser("~"))
        folder = QFileDialog.getExistingDirectory(self, "选择 Google Drive 团队工程文件夹", default_dir)
        if folder:
            self.folder_input.setText(folder)
            self.run_checks()

    def run_checks(self, *args):
        if not hasattr(self, "check_log"):
            return False
        self.check_log.clear()
        mode = self.current_link_mode() if hasattr(self, "link_mode_combo") else CLOUD_LINK_MODE_COLLAB
        link = self.invite_input.text().strip() if hasattr(self, "invite_input") else ""
        link_info = _parse_google_drive_link(link)
        if link:
            if link_info["is_drive"]:
                detail = f"，ID: {link_info['id']}" if link_info.get("id") else ""
                self._append_check("PASS", f"已识别 Google Drive 链接（{link_info.get('kind') or 'unknown'}{detail}）。")
            else:
                self._append_check("WARN", "已填写链接，但不像 Google Drive 链接；仍可保存，建议确认链接来源。")
        else:
            self._append_check("INFO", "可以粘贴 Google Drive 工程链接，软件会保存到云端入口记录里。")
        self._append_check("INFO", f"当前使用方式：{_cloud_link_mode_label(mode)}。")

        candidates = _find_google_drive_candidates()
        if candidates:
            self._append_check("PASS", f"检测到 Google Drive 本地目录：{candidates[0]}")
        else:
            self._append_check("WARN", "没有自动找到 Google Drive 目录；如果已经安装，也可以手动选择同步文件夹。")

        email = self.email_input.text().strip()
        if email and "@" in email:
            self._append_check("PASS", f"协作身份：{email}")
        else:
            self._append_check("WARN", "还没有填写个人 Gmail。每个成员应使用自己的 Gmail，不建议共用一个账号。")

        folder = self.folder_input.text().strip()
        if not folder:
            if mode == CLOUD_LINK_MODE_RENDER:
                self._append_check("INFO", "仅渲染下载模式暂时可以先保存链接；Google API 下载模块接入后会直接下载到临时缓存。")
                return True
            self._append_check("WARN", "还没有选择云端工程文件夹。协作编辑/复制副本模式需要选择 Google Drive 桌面版同步出来的本地目录。")
            return False

        if not os.path.isdir(folder):
            self._append_check("FAIL", f"文件夹不存在：{folder}")
            return False

        if _looks_like_google_drive_path(folder):
            self._append_check("PASS", "路径看起来是 Google Drive 同步目录。")
        else:
            self._append_check("WARN", "路径不像常见 Google Drive 目录；仍可使用，但请确认它会自动同步到团队云端。")

        try:
            meta_dir = os.path.join(folder, ".subtitle_cloud")
            os.makedirs(meta_dir, exist_ok=True)
            probe_path = os.path.join(meta_dir, "_write_test.tmp")
            with open(probe_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe_path)
            self._append_check("PASS", "读写权限正常，可以创建协作锁和工程索引。")
        except Exception as e:
            self._append_check("FAIL", f"无法写入该文件夹：{e}")
            return False

        try:
            ensure_cloud_workspace(folder)
            update_manifest_from_workspace(folder)
            summary = _scan_workspace_summary(folder)
            self._append_check("PASS", f"工程扫描完成：{summary['folder_count']} 个项目文件夹，{summary['reel_count']} 个 Reel。")
            if summary["external_media"]:
                self._append_check("WARN", f"发现 {summary['external_media']} 个素材路径在云端文件夹外；其他用户可能打不开这些本机素材。")
            if summary["missing_media"]:
                self._append_check("WARN", f"发现 {summary['missing_media']} 个素材路径当前不可访问。")
        except Exception as e:
            self._append_check("FAIL", f"云端工程初始化失败：{e}")
            return False

        return True

    def accept(self):
        folder = self.folder_input.text().strip()
        email = self.email_input.text().strip()
        name = self.name_input.text().strip()
        link = self.invite_input.text().strip()
        mode = self.current_link_mode()
        self.completed_cloud_workspace = False

        if mode != CLOUD_LINK_MODE_RENDER and (not email or "@" not in email):
            QMessageBox.warning(self, "需要个人 Gmail", "请填写当前成员自己的 Gmail，用于协作身份和工程编辑锁。")
            return
        if mode != CLOUD_LINK_MODE_RENDER and (not folder or not os.path.isdir(folder)):
            QMessageBox.warning(self, "需要云端文件夹", "请选择 Google Drive 桌面版同步出来的团队工程文件夹。")
            return

        if not self.run_checks():
            QMessageBox.warning(self, "检测未通过", "请先处理检测结果里的红色错误，再完成加入。")
            return

        try:
            if email and "@" in email:
                save_cloud_identity(email, name or email.split("@")[0])
            if folder and os.path.isdir(folder):
                ensure_cloud_workspace(folder)
                update_manifest_from_workspace(folder)
                save_workspace_config(
                    mode=WORKSPACE_MODE_CLOUD,
                    cloud_path=folder,
                    cloud_link=link,
                    cloud_link_mode=mode,
                )
                self.completed_cloud_workspace = True
            else:
                save_workspace_config(cloud_link=link, cloud_link_mode=mode)
        except Exception as e:
            QMessageBox.critical(self, "加入失败", str(e))
            return

        super().accept()


class CloudShareDialog(QDialog):
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.setWindowTitle("云端共享设置")
        self.resize(560, 420)
        self.setStyleSheet("QDialog { background-color: #181825; color: #cdd6f4; } QLabel { color: #cdd6f4; }")
        self.identity = get_cloud_identity()
        self.share = get_share_config(workspace)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("云端共享工程")
        title.setStyleSheet("font-size: 22px; font-weight: 900; color: #89b4fa;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.email_input = QLineEdit(self.identity.get("email", ""))
        self.email_input.setPlaceholderText("你的 Google 邮箱，用于编辑锁和协作身份")
        self.name_input = QLineEdit(self.identity.get("name", ""))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("私有：只有自己", "private")
        self.mode_combo.addItem("指定成员可编辑", "members_edit")
        self.mode_combo.addItem("链接可查看", "link_view")
        self.mode_combo.addItem("链接可编辑（谨慎）", "link_edit")
        current_mode = self.share.get("mode", "private")
        idx = self.mode_combo.findData(current_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        self.members_input = QTextEdit()
        self.members_input.setPlaceholderText("每行一个 Google 邮箱")
        self.members_input.setPlainText("\n".join(self.share.get("members", []) or []))

        self.link_input = QLineEdit(self.share.get("link", ""))
        self.link_input.setPlaceholderText("后续接入 Google Drive API 后自动生成共享链接")

        for widget in (self.email_input, self.name_input, self.mode_combo, self.members_input, self.link_input):
            widget.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; border-radius: 6px; padding: 8px;")

        form.addRow("我的邮箱", self.email_input)
        form.addRow("我的名称", self.name_input)
        form.addRow("共享权限", self.mode_combo)
        form.addRow("成员邮箱", self.members_input)
        form.addRow("共享链接", self.link_input)
        layout.addLayout(form)

        note = QLabel("当前版本会把共享配置写入云端工作区的 .subtitle_cloud/manifest.json。后续 Google 登录/API 接入后，会用这里的配置创建 Drive 权限和共享链接。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #a6adc8; background-color: #11111b; border-radius: 8px; padding: 10px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        email = self.email_input.text().strip()
        name = self.name_input.text().strip()
        members = [line.strip() for line in self.members_input.toPlainText().splitlines() if line.strip()]
        save_cloud_identity(email, name)
        set_share_config(
            self.workspace,
            self.mode_combo.currentData(),
            members=members,
            link=self.link_input.text().strip(),
        )
        super().accept()

class ReelCard(QFrame):
    clicked = pyqtSignal(str)
    selection_clicked = pyqtSignal(str, object)
    delete_clicked = pyqtSignal(str)
    rename_clicked = pyqtSignal(str)
    duplicate_clicked = pyqtSignal(str)

    def __init__(self, project_data, parent=None, card_width=184, card_height=214):
        super().__init__(parent)
        self.project_data = project_data
        self.scomp_path = project_data.get("project_path", "")
        self.card_width = int(card_width or 184)
        self.card_height = int(card_height or 214)
        self._drag_start_pos = None
        self._drag_started = False
        self._selected = False
        self._theme_colors = None
        self.init_ui()

    def init_ui(self):
        self.setFixedSize(self.card_width, self.card_height)
        self._apply_card_frame_style()
        shadow = QGraphicsDropShadowEffect(); shadow.setBlurRadius(10); shadow.setColor(Qt.GlobalColor.black); shadow.setOffset(0, 3)
        self.setGraphicsEffect(shadow)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        self.lbl_cover = QLabel()
        cover_height = max(116, self.card_height - 74)
        self.lbl_cover.setFixedSize(self.card_width, cover_height)
        self.lbl_cover.setStyleSheet("background-color: #11111b; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom: none;")
        self.lbl_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)

        cover_rel = self.project_data.get("cover_img", "")
        p_dir = self.project_data.get("project_dir", "")
        cover_path = find_reel_cover_path(self.scomp_path, self.project_data)
        self.has_cover = bool(cover_path and os.path.exists(cover_path))

        if self.has_cover:
            pixmap = QPixmap(cover_path)
            self.lbl_cover.setPixmap(pixmap.scaled(self.lbl_cover.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            self.lbl_cover.setText("🎬\n无封面\n(在精修室保存后生成)")
            self.lbl_cover.setStyleSheet("background-color: #11111b; color: #45475a; font-size: 13px; font-weight: bold; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom: none;")

        layout.addWidget(self.lbl_cover)

        info_frame = QFrame(); info_frame.setStyleSheet("background: transparent; border: none;")
        info_layout = QVBoxLayout(info_frame); info_layout.setContentsMargins(10, 8, 10, 8); info_layout.setSpacing(3)

        title_row = QHBoxLayout()
        p_name = self.project_data.get("project_name", "未命名Reel")
        self.lbl_title = QLabel(p_name)
        self.lbl_title.setToolTip(p_name)
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #cdd6f4;")

        btn_rename = QPushButton("✎")
        btn_rename.setFixedSize(22, 22)
        btn_rename.setToolTip("重命名 Reel")
        btn_rename.setStyleSheet("background: transparent; border: none; color: #b4befe; font-size: 14px;")
        btn_rename.clicked.connect(self._on_rename_clicked)

        btn_copy = QPushButton("⧉")
        btn_copy.setFixedSize(22, 22)
        btn_copy.setToolTip("复制 Reel")
        btn_copy.setStyleSheet("background: transparent; border: none; color: #a6e3a1; font-size: 14px;")
        btn_copy.clicked.connect(self._on_duplicate_clicked)

        btn_del = QPushButton("🗑️")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet("background: transparent; border: none; color: #f38ba8; font-size: 14px;")
        btn_del.clicked.connect(self._on_del_clicked)

        title_row.addWidget(self.lbl_title, stretch=1)
        title_row.addWidget(btn_rename)
        title_row.addWidget(btn_copy)
        title_row.addWidget(btn_del)
        info_layout.addLayout(title_row)

        self.lbl_date = QLabel(self.project_data.get("updated_at", "").split(" ")[0])
        self.lbl_date.setStyleSheet("font-size: 11px; color: #a6adc8;")
        info_layout.addWidget(self.lbl_date)

        layout.addWidget(info_frame)

    def apply_theme(self, colors):
        self._theme_colors = colors
        self._apply_card_frame_style()
        self.lbl_cover.setStyleSheet(
            f"background-color: {colors['input']}; color: {colors['muted']}; font-size: 13px; "
            "font-weight: bold; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom: none;"
        )
        self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 800; color: {colors['text']};")
        self.lbl_date.setStyleSheet(f"font-size: 11px; color: {colors['muted']};")

    def _apply_card_frame_style(self):
        colors = self._theme_colors or {
            "card": "#1e1e2e",
            "card_hover": "#313244",
            "border": "#313244",
            "accent": "#89b4fa",
            "selected": "#89b4fa",
            "selected_text": "#11111b",
        }
        bg = colors.get("selected", colors["accent"]) if self._selected else colors["card"]
        border = colors.get("accent", "#89b4fa") if self._selected else colors["border"]
        border_w = 3 if self._selected else 1
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg};
                border: {border_w}px solid {border};
                border-radius: 8px;
            }}
            QFrame:hover {{
                border: 2px solid {colors['accent']};
                background-color: {colors['card_hover']};
            }}
        """)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._apply_card_frame_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        current_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (current_pos - self._drag_start_pos).manhattanLength() < 8:
            super().mouseMoveEvent(event)
            return
        self._drag_started = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(REEL_DRAG_MIME, self.scomp_path.encode("utf-8"))
        mime.setText(self.scomp_path)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._drag_started:
            modifiers = event.modifiers()
            if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                self.selection_clicked.emit(self.scomp_path, modifiers)
            else:
                self.clicked.emit(self.scomp_path)
        self._drag_start_pos = None
        self._drag_started = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_open = menu.addAction("打开 Reel")
        act_rename = menu.addAction("重命名 Reel")
        act_duplicate = menu.addAction("复制 Reel")
        menu.addSeparator()
        act_delete = menu.addAction("删除 Reel")
        action = menu.exec(event.globalPos())
        if action == act_open:
            self.clicked.emit(self.scomp_path)
        elif action == act_rename:
            self.rename_clicked.emit(self.scomp_path)
        elif action == act_duplicate:
            self.duplicate_clicked.emit(self.scomp_path)
        elif action == act_delete:
            self.delete_clicked.emit(self.scomp_path)

    def _on_del_clicked(self, event):
        self.delete_clicked.emit(self.scomp_path)

    def _on_rename_clicked(self):
        self.rename_clicked.emit(self.scomp_path)

    def _on_duplicate_clicked(self):
        self.duplicate_clicked.emit(self.scomp_path)


class ReelCompactCard(QFrame):
    clicked = pyqtSignal(str)
    selection_clicked = pyqtSignal(str, object)
    delete_clicked = pyqtSignal(str)
    rename_clicked = pyqtSignal(str)
    duplicate_clicked = pyqtSignal(str)

    def __init__(self, path, project_data=None, colors=None, parent=None, card_width=184):
        super().__init__(parent)
        self.scomp_path = path
        self.project_data = project_data if isinstance(project_data, dict) else {}
        self._theme_colors = colors or PROJECT_HALL_THEMES["graphite_cut"]
        self.card_width = int(card_width or 184)
        self._selected = False
        self._drag_start_pos = None
        self._drag_started = False
        self.init_ui()

    def init_ui(self):
        self.setFixedSize(self.card_width, PROJECT_COMPACT_CARD_HEIGHT)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        name = self.project_data.get("project_name") or os.path.splitext(os.path.basename(self.scomp_path))[0]
        self.lbl_title = QLabel(name)
        self.lbl_title.setToolTip(name)
        self.lbl_title.setWordWrap(True)
        rel = os.path.basename(os.path.dirname(self.scomp_path))
        self.lbl_meta = QLabel(rel)
        self.lbl_meta.setToolTip(self.scomp_path)
        try:
            updated = datetime.fromtimestamp(os.path.getmtime(self.scomp_path)).strftime("%Y-%m-%d")
        except Exception:
            updated = ""
        self.lbl_date = QLabel(updated)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_meta)
        layout.addWidget(self.lbl_date)
        self.apply_theme(self._theme_colors)

    def apply_theme(self, colors):
        self._theme_colors = colors
        self._apply_card_frame_style()
        self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 900; color: {colors['text']}; border: none; background: transparent;")
        self.lbl_meta.setStyleSheet(f"font-size: 11px; color: {colors['muted']}; border: none; background: transparent;")
        self.lbl_date.setStyleSheet(f"font-size: 11px; color: {colors['accent_2']}; border: none; background: transparent;")

    def _apply_card_frame_style(self):
        c = self._theme_colors
        bg = c.get("selected", c["accent"]) if self._selected else c["card"]
        border = c.get("accent", "#89b4fa") if self._selected else c["border"]
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg};
                border: {3 if self._selected else 1}px solid {border};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border: 2px solid {c['accent']};
                background-color: {c['card_hover']};
            }}
        """)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._apply_card_frame_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        current_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (current_pos - self._drag_start_pos).manhattanLength() < 8:
            super().mouseMoveEvent(event)
            return
        self._drag_started = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(REEL_DRAG_MIME, self.scomp_path.encode("utf-8"))
        mime.setText(self.scomp_path)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._drag_started:
            modifiers = event.modifiers()
            if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                self.selection_clicked.emit(self.scomp_path, modifiers)
            else:
                self.clicked.emit(self.scomp_path)
        self._drag_start_pos = None
        self._drag_started = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_open = menu.addAction("打开 Reel")
        act_rename = menu.addAction("重命名 Reel")
        act_duplicate = menu.addAction("复制 Reel")
        menu.addSeparator()
        act_delete = menu.addAction("删除 Reel")
        action = menu.exec(event.globalPos())
        if action == act_open:
            self.clicked.emit(self.scomp_path)
        elif action == act_rename:
            self.rename_clicked.emit(self.scomp_path)
        elif action == act_duplicate:
            self.duplicate_clicked.emit(self.scomp_path)
        elif action == act_delete:
            self.delete_clicked.emit(self.scomp_path)


class ReelFolderCard(QFrame):
    clicked = pyqtSignal(str)
    reel_dropped = pyqtSignal(str, str)
    rename_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, folder_path, reel_count=0, colors=None, parent=None, card_width=184):
        super().__init__(parent)
        self.folder_path = folder_path
        self.reel_count = reel_count
        self.colors = colors or PROJECT_HALL_THEMES["graphite_cut"]
        self.card_width = int(card_width or 184)
        self.setAcceptDrops(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.init_ui()
        self.apply_theme(self.colors)

    def init_ui(self):
        self.setFixedSize(self.card_width, PROJECT_FOLDER_CARD_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(max(120, self.card_width - 20), 94)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_path = first_folder_cover_path(self.folder_path)
        self.has_preview = bool(cover_path)
        if self.has_preview:
            pixmap = QPixmap(cover_path)
            self.lbl_preview.setPixmap(
                pixmap.scaled(
                    self.lbl_preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.lbl_preview.setText("Folder")
        layout.addWidget(self.lbl_preview)
        self.lbl_name = QLabel(os.path.basename(self.folder_path))
        self.lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_name.setWordWrap(True)
        layout.addWidget(self.lbl_name)
        self.lbl_count = QLabel(f"{self.reel_count} 个 Reel")
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_count)
        layout.addStretch(1)

    def _first_reel_cover_path(self):
        return first_folder_cover_path(self.folder_path)

    def apply_theme(self, colors):
        self.colors = colors
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {colors['hint']};
                border: 2px dashed {colors['accent']};
                border-radius: 12px;
            }}
            QFrame:hover {{
                background-color: {colors['card_hover']};
                border-color: {colors['accent_2']};
            }}
        """)
        self.lbl_preview.setStyleSheet(
            f"background-color: {colors['input']}; color: {colors['muted']}; "
            f"border: 1px solid {colors['border']}; border-radius: 7px; "
            f"font-size: 12px; font-weight: 800;"
        )
        self.lbl_name.setStyleSheet(f"color: {colors['text']}; font-size: 13px; font-weight: 900; border: none; background: transparent;")
        self.lbl_count.setStyleSheet(f"color: {colors['muted']}; font-size: 11px; border: none; background: transparent;")

    def _dragged_reel_path(self, event):
        mime = event.mimeData()
        if mime.hasFormat(REEL_DRAG_MIME):
            return bytes(mime.data(REEL_DRAG_MIME)).decode("utf-8")
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path.lower().endswith(".scomp"):
                    return path
        text = mime.text() if mime.hasText() else ""
        return text if text.lower().endswith(".scomp") else ""

    def dragEnterEvent(self, event):
        if self._dragged_reel_path(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dragged_reel_path(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._dragged_reel_path(event)
        if path:
            self.reel_dropped.emit(path, self.folder_path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.folder_path)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_open = menu.addAction("打开分组")
        act_rename = menu.addAction("重命名分组")
        act_delete = menu.addAction("删除分组")
        action = menu.exec(event.globalPos())
        if action == act_open:
            self.clicked.emit(self.folder_path)
        elif action == act_rename:
            self.rename_clicked.emit(self.folder_path)
        elif action == act_delete:
            self.delete_clicked.emit(self.folder_path)


class ProjectView(QWidget):
    def __init__(self, project_data=None, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}
        self.workspace_cfg = get_workspace_config()
        self.workspace = get_active_workspace()
        if not os.path.exists(self.workspace): os.makedirs(self.workspace)
        self.current_folder = ""
        self.current_reel_dir = ""
        self.active_lock_project_path = ""
        self.folder_filter = ""
        self.reel_filter = ""
        self.project_theme = self.load_project_hall_theme()
        self.sidebar_state = SidebarState(expanded=self.load_project_sidebar_expanded())
        self._refreshing_folder_list = False
        self.selected_reel_paths = set()
        self._last_selected_reel_path = ""
        self._visible_reel_paths = []
        self._reel_cards = {}
        self.performance_reel_threshold = 24
        self.project_metrics = PROJECT_GRID_METRICS
        self._grid_generation = 0
        self._pending_reel_records = []
        self._resize_refresh_timer = QTimer(self)
        self._resize_refresh_timer.setSingleShot(True)
        self._resize_refresh_timer.timeout.connect(self.refresh_reels_grid)
        self.setAcceptDrops(True)
        self.init_ui()
        self.refresh_workspace_controls()
        self.refresh_folders()

    def init_ui(self):
        self.setStyleSheet("QWidget { background-color: #11111b; color: #cdd6f4; font-family: 'Segoe UI', Arial; }")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)

        header = QHBoxLayout()
        self.title_label = QLabel("🎬 Reels 视频工程大厅")
        self.title_label.setStyleSheet("font-size: 28px; font-weight: 900; color: #cdd6f4;")
        header.addWidget(self.title_label)

        self.lbl_current = QLabel("当前加载: 无")
        self.lbl_current.setStyleSheet("color: #a6e3a1; font-size: 14px; font-weight: bold; background: #1e1e2e; padding: 5px 15px; border-radius: 15px; margin-left: 20px;")
        header.addWidget(self.lbl_current)

        self.btn_local_workspace = QPushButton("本地版")
        self.btn_cloud_workspace = QPushButton("云端版")
        self.btn_cloud_join = QPushButton("加入云端链接")
        self.btn_pick_cloud_workspace = QPushButton("选择云端文件夹")
        self.btn_cloud_share = QPushButton("共享设置")
        mode_btn_style = """
            QPushButton { background-color: #1e1e2e; color: #a6adc8; border: 1px solid #313244; border-radius: 8px; padding: 7px 12px; font-weight: bold; }
            QPushButton:hover { border-color: #89b4fa; color: #cdd6f4; }
            QPushButton:checked { background-color: #89b4fa; color: #11111b; }
        """
        for btn in (self.btn_local_workspace, self.btn_cloud_workspace):
            btn.setCheckable(True)
            btn.setStyleSheet(mode_btn_style)
        self.btn_cloud_join.setStyleSheet("background-color: #a6e3a1; color: #11111b; border: none; border-radius: 8px; padding: 7px 12px; font-weight: bold;")
        self.btn_pick_cloud_workspace.setStyleSheet("background-color: #313244; color: #f9e2af; border: none; border-radius: 8px; padding: 7px 12px; font-weight: bold;")
        self.btn_cloud_share.setStyleSheet("background-color: #313244; color: #a6e3a1; border: none; border-radius: 8px; padding: 7px 12px; font-weight: bold;")
        self.btn_local_workspace.clicked.connect(lambda: self.switch_workspace_mode(WORKSPACE_MODE_LOCAL))
        self.btn_cloud_workspace.clicked.connect(lambda: self.switch_workspace_mode(WORKSPACE_MODE_CLOUD))
        self.btn_cloud_join.clicked.connect(self.open_cloud_join_wizard)
        self.btn_pick_cloud_workspace.clicked.connect(lambda: self.choose_cloud_workspace(True))
        self.btn_cloud_share.clicked.connect(self.open_cloud_share_settings)
        header.addWidget(self.btn_local_workspace)
        header.addWidget(self.btn_cloud_workspace)
        header.addWidget(self.btn_cloud_join)
        header.addWidget(self.btn_pick_cloud_workspace)
        header.addWidget(self.btn_cloud_share)

        self.theme_combo = QComboBox()
        self.theme_combo.setToolTip("切换全局界面主题")
        self.theme_combo.addItem("暗色星空", "dark_star")
        self.theme_combo.addItem("亮色护眼", "light_care")
        self.theme_combo.addItem("Studio Ember", "studio_ember")
        self.theme_combo.addItem("Graphite Cut", "graphite_cut")
        self.theme_combo.addItem("Colorist Teal", "colorist_teal")
        self.theme_combo.addItem("Paper Cut", "paper_cut")
        theme_idx = self.theme_combo.findData(self.project_theme)
        self.theme_combo.setCurrentIndex(theme_idx if theme_idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(self.on_project_theme_changed)
        header.addWidget(self.theme_combo)
        header.addStretch()
        main_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background-color: #313244; width: 2px; }")

        # 👑 左侧：项目文件夹列表
        self.splitter = splitter
        self.left_panel = QFrame()
        self.left_panel.setStyleSheet("background-color: #181825; border-radius: 10px;")
        left_layout = QVBoxLayout(self.left_panel)

        left_header = QHBoxLayout()
        self.btn_sidebar_toggle = QPushButton(self.sidebar_state.arrow)
        self.btn_sidebar_toggle.setFixedSize(28, 28)
        self.btn_sidebar_toggle.clicked.connect(self.toggle_project_sidebar)
        left_header.addWidget(self.btn_sidebar_toggle)
        self.list_title = QLabel("📁 项目列表")
        self.list_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa;")
        left_header.addWidget(self.list_title)

        # 👑 新增：左侧操作按钮
        btn_new_folder = QPushButton("➕"); btn_new_folder.setFixedSize(30, 30)
        btn_new_folder.setStyleSheet("background-color: #313244; color: white; border-radius: 15px;")
        btn_new_folder.setToolTip("新建项目"); btn_new_folder.clicked.connect(self.create_new_folder)

        btn_rename_folder = QPushButton("✏️"); btn_rename_folder.setFixedSize(30, 30)
        btn_rename_folder.setStyleSheet("background-color: #313244; color: white; border-radius: 15px;")
        btn_rename_folder.setToolTip("重命名选中项目"); btn_rename_folder.clicked.connect(self.rename_current_folder)

        btn_copy_folder = QPushButton("⧉"); btn_copy_folder.setFixedSize(30, 30)
        btn_copy_folder.setStyleSheet("background-color: #313244; color: #a6e3a1; border-radius: 15px;")
        btn_copy_folder.setToolTip("复制当前项目文件夹"); btn_copy_folder.clicked.connect(self.copy_current_folder)

        btn_delete_folder = QPushButton("🗑️"); btn_delete_folder.setFixedSize(30, 30)
        btn_delete_folder.setStyleSheet("background-color: #313244; color: #f38ba8; border-radius: 15px;")
        btn_delete_folder.setToolTip("删除选中项目"); btn_delete_folder.clicked.connect(self.delete_current_folder)

        btn_import_folder = QPushButton("📥"); btn_import_folder.setFixedSize(30, 30)
        btn_import_folder.setStyleSheet("background-color: #313244; color: #a6e3a1; border-radius: 15px;")
        btn_import_folder.setToolTip("导入/拖入外部项目文件夹"); btn_import_folder.clicked.connect(self.import_project_folder_dialog)

        btn_package_folder = QPushButton("📦"); btn_package_folder.setFixedSize(30, 30)
        btn_package_folder.setStyleSheet("background-color: #313244; color: #f9e2af; border-radius: 15px;")
        btn_package_folder.setToolTip("打包当前项目文件夹，方便上传云盘协作"); btn_package_folder.clicked.connect(self.package_current_folder)

        btn_batch_create = QPushButton("🧩"); btn_batch_create.setFixedSize(30, 30)
        btn_batch_create.setStyleSheet("background-color: #313244; color: #b4befe; border-radius: 15px;")
        btn_batch_create.setToolTip("在当前工程文件夹里批量创建 Reel"); btn_batch_create.clicked.connect(self.open_batch_project_builder)

        left_header.addWidget(btn_new_folder)
        left_header.addWidget(btn_rename_folder)
        left_header.addWidget(btn_copy_folder)
        left_header.addWidget(btn_delete_folder)
        left_header.addWidget(btn_import_folder)
        left_header.addWidget(btn_package_folder)
        left_header.addWidget(btn_batch_create)
        self.folder_action_buttons = [
            btn_new_folder,
            btn_rename_folder,
            btn_copy_folder,
            btn_delete_folder,
            btn_import_folder,
            btn_package_folder,
            btn_batch_create,
        ]
        left_layout.addLayout(left_header)

        self.folder_search = QLineEdit()
        self.folder_search.setPlaceholderText("搜索项目/分类路径")
        self.folder_search.setStyleSheet("""
            QLineEdit {
                background-color: #11111b; color: #cdd6f4;
                border: 1px solid #313244; border-radius: 8px;
                padding: 8px 10px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #89b4fa; }
        """)
        self.folder_search.textChanged.connect(self.on_folder_filter_changed)
        left_layout.addWidget(self.folder_search)

        self.lbl_workspace_summary = QLabel("")
        self.lbl_workspace_summary.setWordWrap(True)
        self.lbl_workspace_summary.setStyleSheet("color: #a6adc8; background-color: #11111b; border-radius: 8px; padding: 8px; font-size: 12px;")
        left_layout.addWidget(self.lbl_workspace_summary)

        self.drop_hint = QLabel("左侧支持 分类/项目 二级目录；右键文件夹可新建子文件夹\n拖入项目文件夹可导入；拖 .scomp 到某个项目名上可复制进去")
        self.drop_hint.setStyleSheet("color: #a6adc8; background-color: #11111b; border: 1px dashed #45475a; border-radius: 8px; padding: 10px; font-size: 12px;")
        self.drop_hint.setWordWrap(True)
        left_layout.addWidget(self.drop_hint)

        self.folder_list = ProjectFolderListWidget()
        self.folder_list.setStyleSheet("""
            QTreeWidget { background: transparent; border: none; outline: none; }
            QTreeWidget::item { padding: 10px; margin: 3px 0; border-radius: 8px; font-size: 14px; color: #a6adc8; font-weight: bold; }
            QTreeWidget::item:hover { background-color: #313244; }
            QTreeWidget::item:selected { background-color: #89b4fa; color: #11111b; }
        """)
        self.folder_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_list.itemClicked.connect(lambda item, column: self.on_folder_selected(item))
        self.folder_list.itemDoubleClicked.connect(lambda item, column: self.folder_list.editItem(item, 0))
        self.folder_list.itemChanged.connect(lambda item, column: self.on_folder_item_changed(item))
        self.folder_list.customContextMenuRequested.connect(self.show_folder_context_menu)
        self.folder_list.files_dropped.connect(self.import_dropped_paths)
        left_layout.addWidget(self.folder_list)

        # 👑 右侧：Reels 分页网格
        self.right_panel = QFrame()
        self.right_panel.setStyleSheet("background-color: transparent;")
        right_layout = QVBoxLayout(self.right_panel)

        self.lbl_folder_title = QLabel("请在左侧选择一个项目...")
        self.lbl_folder_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #f9e2af; padding-bottom: 10px;")
        right_layout.addWidget(self.lbl_folder_title)

        tools_row = QHBoxLayout()
        self.reel_search = QLineEdit()
        self.reel_search.setPlaceholderText("搜索 Reel 名称 / 文件名")
        self.reel_search.setStyleSheet("""
            QLineEdit {
                background-color: #181825; color: #cdd6f4;
                border: 1px solid #313244; border-radius: 8px;
                padding: 8px 10px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #89b4fa; }
        """)
        self.reel_search.textChanged.connect(self.on_reel_filter_changed)

        self.btn_audit_folder = QPushButton("体检当前项目")
        self.btn_audit_workspace = QPushButton("体检全部")
        self.btn_safe_fonts = QPushButton("字体安全化")
        self.btn_replace_video_selected = QPushButton("换画面")
        self.btn_replace_music_selected = QPushButton("批量换配乐")
        self.btn_apply_style_selected = QPushButton("套样式")
        self.btn_apply_signature_selected = QPushButton("\u6362\u7f72\u540d")
        self.btn_caption_mode_selected = QPushButton("听译模式")
        self.btn_move_selected = QPushButton("移动选中")
        self.btn_trash_selected = QPushButton("删除选中")
        self.btn_open_trash = QPushButton("垃圾桶")
        self.btn_replace_video_selected.setToolTip("给选中的 Reel 批量替换主画面素材，可一次选择多条画面并循环/随机分配。")
        self.btn_replace_music_selected.setToolTip("给选中的 Reel 批量替换配乐，可一次选择多首并顺序循环或随机分配。")
        self.btn_apply_style_selected.setToolTip("把一个字幕样式预设批量应用到选中 Reel 的现有字幕和默认样式。")
        self.btn_apply_signature_selected.setToolTip("\u7ed9\u9009\u4e2d\u7684 Reel \u6279\u91cf\u5957\u7528\u7f72\u540d\u6a21\u677f\u3001\u53ea\u66ff\u6362\u7f72\u540d\u6587\u5b57\uff0c\u6216\u5173\u95ed\u7f72\u540d\u3002")
        self.btn_caption_mode_selected.setToolTip("批量修改选中 Reel 的听译断句模式和时间模式，方便统一调度。")
        for btn in (self.btn_audit_folder, self.btn_audit_workspace, self.btn_safe_fonts, self.btn_replace_video_selected, self.btn_replace_music_selected, self.btn_apply_style_selected, self.btn_apply_signature_selected, self.btn_caption_mode_selected, self.btn_move_selected, self.btn_trash_selected, self.btn_open_trash):
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; border: none; border-radius: 8px; padding: 8px 12px; font-weight: bold;")
        self.btn_audit_folder.clicked.connect(self.show_current_folder_audit)
        self.btn_audit_workspace.clicked.connect(self.show_workspace_audit)
        self.btn_safe_fonts.clicked.connect(self.safe_fontize_current_folder)
        self.btn_replace_video_selected.clicked.connect(self.replace_selected_reels_video_dialog)
        self.btn_replace_music_selected.clicked.connect(self.replace_selected_reels_music_dialog)
        self.btn_apply_style_selected.clicked.connect(self.apply_style_to_selected_reels_dialog)
        self.btn_apply_signature_selected.clicked.connect(self.apply_signature_to_selected_reels_dialog)
        self.btn_caption_mode_selected.clicked.connect(self.update_selected_reels_caption_modes_dialog)
        self.btn_move_selected.clicked.connect(self.move_selected_reels_dialog)
        self.btn_trash_selected.clicked.connect(self.delete_selected_reels)
        self.btn_open_trash.clicked.connect(self.open_trash_folder)
        tools_row.addWidget(self.reel_search, stretch=1)
        tools_row.addWidget(self.btn_audit_folder)
        tools_row.addWidget(self.btn_audit_workspace)
        tools_row.addWidget(self.btn_safe_fonts)
        tools_row.addWidget(self.btn_replace_video_selected)
        tools_row.addWidget(self.btn_replace_music_selected)
        tools_row.addWidget(self.btn_apply_style_selected)
        tools_row.addWidget(self.btn_apply_signature_selected)
        tools_row.addWidget(self.btn_caption_mode_selected)
        tools_row.addWidget(self.btn_move_selected)
        tools_row.addWidget(self.btn_trash_selected)
        tools_row.addWidget(self.btn_open_trash)
        right_layout.addLayout(tools_row)

        self.lbl_reel_summary = QLabel("")
        self.lbl_reel_summary.setStyleSheet("color: #a6adc8; font-size: 12px; padding-bottom: 6px;")
        right_layout.addWidget(self.lbl_reel_summary)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(self.project_metrics.grid_gap)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setWidget(self.grid_widget)
        right_layout.addWidget(self.scroll_area, stretch=1)

        splitter.addWidget(self.left_panel)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([self.sidebar_state.width, 1040])
        main_layout.addWidget(splitter, stretch=1)
        self.apply_project_sidebar_state()
        self.apply_project_theme()

    def parent_window(self):
        p = self.parent()
        while p is not None and not hasattr(p, "switch_room"): p = p.parent()
        return p

    def load_project_sidebar_expanded(self):
        try:
            return bool(load_app_config().get(PROJECT_SIDEBAR_EXPANDED_KEY, False))
        except Exception:
            return False

    def save_project_sidebar_expanded(self):
        try:
            data = load_app_config()
            data[PROJECT_SIDEBAR_EXPANDED_KEY] = bool(self.sidebar_state.expanded)
            save_app_config(data)
        except Exception:
            pass

    def toggle_project_sidebar(self):
        self.sidebar_state.toggle()
        self.apply_project_sidebar_state()
        self.save_project_sidebar_expanded()

    def apply_project_sidebar_state(self):
        apply_sidebar_state(
            getattr(self, "left_panel", None),
            getattr(self, "btn_sidebar_toggle", None),
            self.sidebar_state,
        )
        expanded = bool(self.sidebar_state.expanded)
        for widget in [
            getattr(self, "list_title", None),
            getattr(self, "folder_search", None),
            getattr(self, "lbl_workspace_summary", None),
            getattr(self, "drop_hint", None),
            getattr(self, "folder_list", None),
        ]:
            if widget is not None and hasattr(widget, "setVisible"):
                widget.setVisible(expanded)
        for btn in getattr(self, "folder_action_buttons", []):
            btn.setVisible(expanded)
        if hasattr(self, "left_panel") and self.left_panel.layout():
            margins = (8, 8, 8, 8) if expanded else (10, 10, 10, 10)
            self.left_panel.layout().setContentsMargins(*margins)
        if hasattr(self, "splitter"):
            self.splitter.setSizes([self.sidebar_state.width, max(860, self.width() - self.sidebar_state.width)])

    def load_project_hall_theme(self):
        try:
            theme = load_app_config().get(PROJECT_HALL_THEME_KEY, "graphite_cut")
            if theme in PROJECT_HALL_THEMES:
                return theme
        except Exception:
            pass
        return "graphite_cut"

    def save_project_hall_theme(self):
        try:
            data = load_app_config()
            data[PROJECT_HALL_THEME_KEY] = self.project_theme
            save_app_config(data)
        except Exception:
            pass

    def theme_colors(self):
        return PROJECT_HALL_THEMES.get(self.project_theme, PROJECT_HALL_THEMES["graphite_cut"])

    def on_project_theme_changed(self, *args):
        theme = self.theme_combo.currentData() if hasattr(self, "theme_combo") else "graphite_cut"
        self.project_theme = theme if theme in PROJECT_HALL_THEMES else "graphite_cut"
        self.save_project_hall_theme()
        self.apply_project_theme()
        self.refresh_reels_grid()

    def _pill_button_style(self, accent=None):
        c = self.theme_colors()
        accent = accent or c["accent"]
        return f"""
            QPushButton {{
                background-color: {c['panel_2']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {c['card_hover']};
                border-color: {accent};
            }}
            QPushButton:checked {{
                background-color: {accent};
                color: {c['selected_text']};
                border-color: {accent};
            }}
        """

    def _round_icon_button_style(self, accent=None):
        c = self.theme_colors()
        accent = accent or c["accent"]
        return f"""
            QPushButton {{
                background-color: {c['panel_2']};
                color: {accent};
                border: 1px solid {c['border']};
                border-radius: 15px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background-color: {c['card_hover']};
                border-color: {accent};
            }}
        """

    def apply_project_theme(self):
        c = self.theme_colors()
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {c['bg']};
                color: {c['text']};
                font-family: 'Segoe UI', 'Microsoft YaHei', Arial;
            }}
            QMenu {{
                background-color: {c['panel']};
                color: {c['text']};
                border: 1px solid {c['border']};
                padding: 6px;
            }}
            QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
            QMenu::item:selected {{ background-color: {c['selected']}; color: {c['selected_text']}; }}
            QToolTip {{
                background-color: {c['panel']};
                color: {c['text']};
                border: 1px solid {c['border']};
                padding: 6px;
            }}
            QScrollBar:vertical {{
                background-color: {c['panel']};
                width: 10px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background-color: {c['border']};
                min-height: 34px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {c['accent']};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                background-color: {c['panel']};
                height: 10px;
                margin: 0;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background-color: {c['border']};
                min-width: 34px;
                border-radius: 5px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background-color: {c['accent']};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
        """)
        if hasattr(self, "title_label"):
            star = "  ✦" if self.project_theme == "dark_star" else ""
            self.title_label.setText(f"🎬 Reels 视频工程大厅{star}")
            self.title_label.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {c['text']};")
        if hasattr(self, "lbl_current"):
            self.lbl_current.setStyleSheet(
                f"color: {c['accent_2']}; font-size: 14px; font-weight: bold; "
                f"background: {c['hint']}; padding: 6px 16px; border-radius: 15px; margin-left: 20px;"
            )
        if hasattr(self, "theme_combo"):
            self.theme_combo.setStyleSheet(
                f"QComboBox {{ background-color: {c['panel_2']}; color: {c['text']}; border: 1px solid {c['border']}; "
                f"border-radius: 8px; padding: 7px 10px; font-weight: bold; min-width: 96px; }}"
                f"QComboBox:hover {{ border-color: {c['accent']}; }}"
                f"QComboBox QAbstractItemView {{ background-color: {c['panel']}; color: {c['text']}; selection-background-color: {c['selected']}; }}"
            )
        if hasattr(self, "splitter"):
            self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {c['border']}; width: 2px; }}")
        if hasattr(self, "left_panel"):
            self.left_panel.setStyleSheet(f"background-color: {c['panel']}; border: 1px solid {c['border']}; border-radius: 14px;")
        if hasattr(self, "right_panel"):
            self.right_panel.setStyleSheet("background-color: transparent;")
        if hasattr(self, "list_title"):
            self.list_title.setStyleSheet(f"font-size: 16px; font-weight: 900; color: {c['accent']};")

        for btn in getattr(self, "folder_action_buttons", []):
            btn.setStyleSheet(self._round_icon_button_style())
        if hasattr(self, "btn_sidebar_toggle"):
            self.btn_sidebar_toggle.setStyleSheet(self._round_icon_button_style())
        for btn in (getattr(self, "btn_local_workspace", None), getattr(self, "btn_cloud_workspace", None)):
            if btn:
                btn.setStyleSheet(self._pill_button_style())
        for btn in (
            getattr(self, "btn_cloud_join", None),
            getattr(self, "btn_pick_cloud_workspace", None),
            getattr(self, "btn_cloud_share", None),
            getattr(self, "btn_audit_folder", None),
            getattr(self, "btn_audit_workspace", None),
            getattr(self, "btn_safe_fonts", None),
            getattr(self, "btn_replace_video_selected", None),
            getattr(self, "btn_replace_music_selected", None),
            getattr(self, "btn_apply_style_selected", None),
            getattr(self, "btn_apply_signature_selected", None),
            getattr(self, "btn_caption_mode_selected", None),
            getattr(self, "btn_move_selected", None),
            getattr(self, "btn_trash_selected", None),
            getattr(self, "btn_open_trash", None),
        ):
            if btn:
                btn.setStyleSheet(self._pill_button_style())

        input_style = f"""
            QLineEdit {{
                background-color: {c['input']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 10px;
                padding: 9px 11px;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {c['accent']}; }}
        """
        for edit in (getattr(self, "folder_search", None), getattr(self, "reel_search", None)):
            if edit:
                edit.setStyleSheet(input_style)
        if hasattr(self, "lbl_workspace_summary"):
            self.lbl_workspace_summary.setStyleSheet(
                f"color: {c['muted']}; background-color: {c['hint']}; border: 1px solid {c['border']}; "
                f"border-radius: 10px; padding: 9px; font-size: 12px;"
            )
        if hasattr(self, "drop_hint"):
            self.drop_hint.setStyleSheet(
                f"color: {c['muted']}; background-color: {c['hint']}; border: 1px dashed {c['border']}; "
                f"border-radius: 10px; padding: 10px; font-size: 12px;"
            )
        if hasattr(self, "folder_list"):
            self.folder_list.setStyleSheet(f"""
                QTreeWidget {{ background: transparent; border: none; outline: none; }}
                QTreeWidget::branch {{ background: transparent; }}
                QTreeWidget::item {{
                    padding: 10px 9px;
                    margin: 3px 0;
                    border-radius: 10px;
                    font-size: 14px;
                    color: {c['muted']};
                    font-weight: 700;
                }}
                QTreeWidget::item:hover {{ background-color: {c['card_hover']}; color: {c['text']}; }}
                QTreeWidget::item:selected {{ background-color: {c['selected']}; color: {c['selected_text']}; }}
            """)
        if hasattr(self, "lbl_folder_title"):
            self.lbl_folder_title.setStyleSheet(f"font-size: 21px; font-weight: 900; color: {c['warn']}; padding-bottom: 10px;")
        if hasattr(self, "lbl_reel_summary"):
            self.lbl_reel_summary.setStyleSheet(f"color: {c['muted']}; font-size: 12px; padding-bottom: 6px;")
        if hasattr(self, "scroll_area"):
            self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        if hasattr(self, "grid_widget"):
            self.grid_widget.setStyleSheet("background: transparent;")
        parent = self.parent_window()
        if parent and hasattr(parent, "apply_chrome_theme"):
            parent.apply_chrome_theme(self.project_theme)

    def on_folder_filter_changed(self, text):
        self.folder_filter = (text or "").strip().lower()
        self.refresh_folders()

    def on_reel_filter_changed(self, text):
        self.reel_filter = (text or "").strip().lower()
        self.refresh_reels_grid()

    def show_audit_dialog(self, title, report_text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(880, 680)
        dialog.setStyleSheet("QDialog { background-color: #11111b; color: #cdd6f4; }")
        layout = QVBoxLayout(dialog)
        header = QLabel(title)
        header.setStyleSheet("font-size: 20px; font-weight: 900; color: #89b4fa;")
        layout.addWidget(header)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(report_text)
        body.setStyleSheet("background-color: #181825; color: #cdd6f4; border: 1px solid #313244; border-radius: 8px; padding: 10px; font-family: Consolas, 'Microsoft YaHei';")
        layout.addWidget(body, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def show_current_folder_audit(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个项目文件夹。")
            return
        report = scan_folder(self.current_folder, workspace=self.workspace)
        self.show_audit_dialog("当前项目体检", format_scan_report(report))
        self.refresh_workspace_summary()

    def show_workspace_audit(self):
        report = scan_workspace(self.workspace)
        self.show_audit_dialog("工作区体检", format_scan_report(report))
        self.refresh_workspace_summary()

    def _replace_unsafe_project_fonts(self, project_data, replacement="Noto Sans SC"):
        changed = 0

        def fix_style(style):
            nonlocal changed
            if not isinstance(style, dict):
                return
            old_font = str(style.get("font", "") or "").strip()
            if old_font and not is_safe_font(old_font):
                style["font"] = replacement
                changed += 1

        edit_state = project_data.get("room_state", {}).get("edit_room", {}) if isinstance(project_data, dict) else {}
        if isinstance(edit_state, dict):
            fix_style(edit_state.get("default_style", {}))
            for sub in edit_state.get("subs_data", []) or []:
                if isinstance(sub, dict):
                    fix_style(sub.get("style", sub))
        for sub in project_data.get("subs_data", []) or []:
            if isinstance(sub, dict):
                fix_style(sub.get("style", sub))
        return changed

    def safe_fontize_current_folder(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个项目文件夹。")
            return
        reply = QMessageBox.question(
            self,
            "字体安全化",
            "这会把当前项目文件夹内所有未登记/系统字体替换为 Noto Sans SC。\n\n建议替换后打开重点 Reel 看一眼排版。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        project_count = 0
        changed_count = 0
        errors = []
        for path in get_reels_in_folder(self.current_folder, recursive=True):
            try:
                project = load_project(path)
                changed = self._replace_unsafe_project_fonts(project)
                if changed:
                    save_project(path, project)
                    changed_count += changed
                    project_count += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        current_path = self.project_data.get("project_path", "") if isinstance(self.project_data, dict) else ""
        if current_path and os.path.abspath(current_path).startswith(os.path.abspath(self.current_folder) + os.sep) and os.path.exists(current_path):
            try:
                self.project_data = load_project(current_path)
                self.sync_current_project_to_main()
                self.sync_current_project_label()
            except Exception:
                pass
        self.refresh_reels_grid()
        msg = f"已处理 {project_count} 个 Reel，替换 {changed_count} 处字体为 Noto Sans SC。"
        if errors:
            msg += f"\n\n有 {len(errors)} 个工程处理失败：\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "字体安全化完成", msg)
        self.show_current_folder_audit()

    def refresh_workspace_summary(self):
        if not hasattr(self, "lbl_workspace_summary"):
            return
        try:
            folders = get_project_folder_paths(self.workspace, recursive=False)
            reel_count = 0
            for rel_path in folders:
                reel_count += count_reels_fast(os.path.join(self.workspace, rel_path), recursive=True)
            current_line = ""
            if self.current_folder and os.path.isdir(self.current_folder):
                current_line = f"\n当前文件夹: {os.path.basename(self.current_folder)} / {count_reels_fast(self.current_folder, recursive=True)} 个 Reel"
            self.lbl_workspace_summary.setText(
                f"工作区: {len(folders)} 个一级文件夹 / {reel_count} 个 Reel\n"
                f"素材缺失和外部素材请用体检按钮精查"
                f"{current_line}"
            )
        except Exception:
            self.lbl_workspace_summary.setText("工作区摘要暂不可用")

    def open_cloud_join_wizard(self):
        previous_project_dir = self.project_data.get("project_dir", "") if isinstance(self.project_data, dict) else ""
        dialog = CloudJoinWizard(get_workspace_config(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not getattr(dialog, "completed_cloud_workspace", False):
                QMessageBox.information(
                    self,
                    "云端链接已保存",
                    "已保存 Google Drive 工程链接和使用方式。\n\n仅渲染下载 / 自动复制到我的云盘需要 Google Drive API 授权模块；当前可以先用浏览器打开链接，下载工程包后拖入工程大厅。"
                )
                return
            self.release_active_cloud_lock()
            self.reload_workspace()
            if (
                previous_project_dir
                and os.path.isdir(previous_project_dir)
                and not _is_path_inside(previous_project_dir, self.workspace)
                and self._has_reel_files(previous_project_dir)
            ):
                reply = QMessageBox.question(
                    self,
                    "导入当前工程到云端吗？",
                    "云端团队已经连接。\n\n要顺手把当前本地项目复制进云端工程大厅，并自动上传它引用的素材吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.import_project_folder(previous_project_dir)
            QMessageBox.information(self, "云端团队已连接", "已切换到云端工程大厅。之后打开 Reel 时，软件会用你的 Gmail 写入编辑锁，减少多人覆盖。")

    def refresh_workspace_controls(self):
        self.workspace_cfg = get_workspace_config()
        mode = self.workspace_cfg.get("mode", WORKSPACE_MODE_LOCAL)
        self.btn_local_workspace.setChecked(mode == WORKSPACE_MODE_LOCAL)
        self.btn_cloud_workspace.setChecked(mode == WORKSPACE_MODE_CLOUD)
        cloud_path = self.workspace_cfg.get("cloud_path", "")
        self.btn_pick_cloud_workspace.setVisible(mode == WORKSPACE_MODE_CLOUD)
        self.btn_cloud_share.setVisible(mode == WORKSPACE_MODE_CLOUD)
        if mode == WORKSPACE_MODE_CLOUD:
            label = os.path.basename(cloud_path) if cloud_path else "选择云端文件夹"
            self.btn_pick_cloud_workspace.setText(label)

    def is_cloud_workspace(self):
        return self.workspace_cfg.get("mode") == WORKSPACE_MODE_CLOUD

    def ensure_cloud_identity(self):
        identity = get_cloud_identity()
        if identity.get("email"):
            return identity

        email, ok = QInputDialog.getText(
            self,
            "云端协作身份",
            "请输入你的 Google 邮箱，用于工程编辑锁和协作记录：",
            text=identity.get("email", ""),
        )
        if not ok or not email.strip():
            QMessageBox.information(self, "需要身份", "云端协作需要先填写你的 Google 邮箱。")
            return None

        name = identity.get("name") or email.strip().split("@")[0]
        return save_cloud_identity(email.strip(), name)

    def release_active_cloud_lock(self):
        if not self.active_lock_project_path:
            return
        try:
            release_project_lock(self.workspace, self.active_lock_project_path, get_cloud_identity())
        except Exception:
            pass
        self.active_lock_project_path = ""

    def prepare_cloud_project_lock(self, path):
        if not self.is_cloud_workspace():
            return True

        identity = self.ensure_cloud_identity()
        if not identity:
            return False

        locked, lock = acquire_project_lock(self.workspace, path, identity)
        if not locked:
            owner = lock.get("name") or lock.get("email") or "其他成员"
            expires_at = lock.get("expires_at", "未知时间")
            reply = QMessageBox.warning(
                self,
                "工程正在被编辑",
                f"这个 Reel 当前由 {owner} 锁定编辑。\n锁定到期时间：{expires_at}\n\n仍然打开可能覆盖对方正在同步的修改，要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.release_active_cloud_lock()
                return True
            return False

        previous_path = self.active_lock_project_path
        if previous_path and os.path.normcase(os.path.abspath(previous_path)) != os.path.normcase(os.path.abspath(path)):
            try:
                release_project_lock(self.workspace, previous_path, identity)
            except Exception:
                pass
        self.active_lock_project_path = path
        return True

    def open_cloud_share_settings(self):
        if not self.is_cloud_workspace():
            return
        ensure_cloud_workspace(self.workspace)
        dialog = CloudShareDialog(self.workspace, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            update_manifest_from_workspace(self.workspace)
            QMessageBox.information(self, "已保存", "云端共享设置已保存到当前云端工作区。")

    def switch_workspace_mode(self, mode):
        if mode != self.workspace_cfg.get("mode"):
            self.release_active_cloud_lock()
        if mode == WORKSPACE_MODE_CLOUD and not self.workspace_cfg.get("cloud_path"):
            if not self.choose_cloud_workspace(switch_to_cloud=True):
                self.refresh_workspace_controls()
                return
        else:
            save_workspace_config(mode=mode)
        self.reload_workspace()

    def choose_cloud_workspace(self, switch_to_cloud=True):
        default_dir = self.workspace_cfg.get("cloud_path") or os.path.expanduser("~")
        previous_project_dir = self.project_data.get("project_dir", "") if isinstance(self.project_data, dict) else ""
        folder = QFileDialog.getExistingDirectory(self, "选择云端协作工作区文件夹", default_dir)
        if not folder:
            return False
        if os.path.abspath(folder) != os.path.abspath(self.workspace):
            self.release_active_cloud_lock()
        os.makedirs(folder, exist_ok=True)
        mode = WORKSPACE_MODE_CLOUD if switch_to_cloud else self.workspace_cfg.get("mode", WORKSPACE_MODE_CLOUD)
        save_workspace_config(mode=mode, cloud_path=folder)
        self.reload_workspace()
        if (
            switch_to_cloud
            and previous_project_dir
            and os.path.isdir(previous_project_dir)
            and not _is_path_inside(previous_project_dir, folder)
            and self._has_reel_files(previous_project_dir)
        ):
            reply = QMessageBox.question(
                self,
                "导入当前工程到云端吗？",
                "检测到当前加载的工程还在本地工作区。\n\n要复制这个项目文件夹到云端工程大厅，并自动把素材放入 assets 等待 Google Drive 同步上传吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.import_project_folder(previous_project_dir)
        return True

    def reload_workspace(self):
        self.workspace_cfg = get_workspace_config()
        self.workspace = get_active_workspace()
        os.makedirs(self.workspace, exist_ok=True)
        self.current_folder = ""
        self.current_reel_dir = ""
        self.active_lock_project_path = ""
        self.lbl_folder_title.setText("请在左侧选择一个项目...")
        if self.is_cloud_workspace():
            ensure_cloud_workspace(self.workspace)
            update_manifest_from_workspace(self.workspace)
        self.refresh_workspace_controls()
        self.refresh_folders()

    def sync_current_project_label(self):
        p_name = self.project_data.get("project_name", "") if isinstance(self.project_data, dict) else ""
        if p_name: self.lbl_current.setText(f"当前加载 Reel: {p_name}")
        else: self.lbl_current.setText("当前加载 Reel: 无")

    def _folder_rel_from_item(self, item):
        if not item:
            return ""
        return item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)

    def _folder_item_label(self, item):
        return item.text(0) if item else ""

    def _first_folder_item(self):
        return self.folder_list.topLevelItem(0) if self.folder_list.topLevelItemCount() else None

    def _add_home_folder_item(self):
        item = QTreeWidgetItem(["主目录"])
        item.setData(0, Qt.ItemDataRole.UserRole, PROJECT_HOME_NODE)
        item.setToolTip(0, "最近使用的 Reel 和工作区一级文件夹")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled)
        self.folder_list.addTopLevelItem(item)
        return item

    def _find_folder_item(self, rel_or_name):
        needle = os.path.normcase(str(rel_or_name or "").strip())
        if not needle:
            return None

        def walk(item):
            rel = str(self._folder_rel_from_item(item) or "")
            if os.path.normcase(rel) == needle or os.path.normcase(os.path.basename(rel)) == needle:
                return item
            for idx in range(item.childCount()):
                found = walk(item.child(idx))
                if found:
                    return found
            return None

        for idx in range(self.folder_list.topLevelItemCount()):
            found = walk(self.folder_list.topLevelItem(idx))
            if found:
                return found
        return None

    def _add_folder_tree_item(self, rel_path, nodes):
        parent_rel = os.path.dirname(rel_path)
        item = QTreeWidgetItem([os.path.basename(rel_path)])
        item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
        item.setToolTip(0, rel_path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        if parent_rel and parent_rel in nodes:
            nodes[parent_rel].addChild(item)
        else:
            self.folder_list.addTopLevelItem(item)
        nodes[rel_path] = item
        return item

    def refresh_folders(self, select_name=None):
        if self.is_cloud_workspace():
            try:
                ensure_cloud_workspace(self.workspace)
                update_manifest_from_workspace(self.workspace)
            except Exception:
                pass
        self._refreshing_folder_list = True
        self.folder_list.blockSignals(True)
        self.folder_list.clear()
        home_item = self._add_home_folder_item()
        folders = get_project_folder_paths(self.workspace, recursive=True, max_depth=2)
        query = getattr(self, "folder_filter", "")
        if query:
            matched = [f for f in folders if query in f.lower()]
            visible = set()
            for rel_path in matched:
                parts = rel_path.split(os.sep)
                for idx in range(1, len(parts) + 1):
                    visible.add(os.path.join(*parts[:idx]))
            folders = [f for f in folders if f in visible]
        nodes = {}
        for f in folders:
            item = self._add_folder_tree_item(f, nodes)
            item.setToolTip(0, f"{f}\n双击可重命名；右键可新建子文件夹、复制、打包、删除")
        self.folder_list.blockSignals(False)
        self.folder_list.expandAll()
        self._refreshing_folder_list = False

        self.refresh_workspace_summary()
        if select_name:
            item = self._find_folder_item(select_name)
            if item:
                self.folder_list.setCurrentItem(item)
                self.on_folder_selected(item)
                return
        self.folder_list.setCurrentItem(home_item)
        self.show_project_home()

    def on_folder_item_changed(self, item):
        if self._refreshing_folder_list or not item:
            return
        old_name = self._folder_rel_from_item(item)
        if old_name == PROJECT_HOME_NODE:
            item.setText(0, "主目录")
            return
        new_name = item.text(0).strip()
        if not old_name or new_name == os.path.basename(old_name):
            return
        renamed = self.rename_folder_by_name(old_name, new_name, show_dialogs=True)
        if renamed:
            self.refresh_folders(select_name=renamed)
        else:
            self.folder_list.blockSignals(True)
            item.setText(0, os.path.basename(old_name))
            self.folder_list.blockSignals(False)

    def show_folder_context_menu(self, pos):
        item = self.folder_list.itemAt(pos)
        if item:
            self.folder_list.setCurrentItem(item)
            self.on_folder_selected(item)
        menu = QMenu(self)
        act_new = menu.addAction("新建顶层项目/分类")
        act_new_child = menu.addAction("在此新建子文件夹")
        act_rename = menu.addAction("重命名")
        act_copy = menu.addAction("复制项目")
        act_package = menu.addAction("打包共享")
        menu.addSeparator()
        act_audit = menu.addAction("体检当前项目")
        act_delete = menu.addAction("删除")
        has_folder = bool(self.current_folder and os.path.isdir(self.current_folder))
        act_new_child.setEnabled(has_folder)
        for action in (act_rename, act_copy, act_package, act_audit, act_delete):
            action.setEnabled(has_folder)
        action = menu.exec(self.folder_list.mapToGlobal(pos))
        if action == act_new:
            self.create_new_folder()
        elif action == act_new_child:
            self.create_child_folder()
        elif action == act_rename and item:
            self.folder_list.editItem(item, 0)
        elif action == act_copy:
            self.copy_current_folder()
        elif action == act_package:
            self.package_current_folder()
        elif action == act_audit:
            self.show_current_folder_audit()
        elif action == act_delete:
            self.delete_current_folder()

    def import_dropped_paths(self, paths, target_folder_name=""):
        if target_folder_name == PROJECT_HOME_NODE:
            target_folder_name = ""
        target_folder = os.path.join(self.workspace, target_folder_name) if target_folder_name else ""
        for path in paths:
            if os.path.isdir(path):
                self.import_project_folder(path)
                return
            if path.lower().endswith(".scomp"):
                if target_folder and os.path.isdir(target_folder):
                    self.copy_reel_to_folder(path, target_folder)
                    return
                self.import_project_folder(os.path.dirname(path))
                return

    def create_new_folder(self):
        name, ok = QInputDialog.getText(self, "新建项目/分类", "请输入新文件夹名称；需要二级分类可写成：分类/项目")
        if ok and name.strip():
            safe_rel = self._safe_relative_folder(name.strip())
            path = os.path.join(self.workspace, safe_rel)
            if not os.path.exists(path):
                os.makedirs(path)
                self.refresh_folders(select_name=safe_rel)
            else:
                QMessageBox.warning(self, "提示", "项目文件夹已存在！")

    def _safe_folder_name(self, name):
        safe_name = "".join(c for c in (name or "").strip() if c not in r'\/:*?"<>|')
        return safe_name or "导入项目"

    def _safe_relative_folder(self, value):
        parts = [self._safe_folder_name(part) for part in re.split(r"[\\/]+", value or "") if part.strip()]
        return os.path.join(*parts) if parts else self._safe_folder_name(value)

    def create_child_folder(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个父文件夹。")
            return
        parent_rel = os.path.relpath(self.current_folder, self.workspace)
        name, ok = QInputDialog.getText(self, "新建子文件夹", f"在「{parent_rel}」下面新建：")
        if not ok or not name.strip():
            return
        safe_rel = self._safe_relative_folder(name.strip())
        path = os.path.join(self.current_folder, safe_rel)
        if os.path.exists(path):
            QMessageBox.warning(self, "提示", "同名子文件夹已存在。")
            return
        try:
            os.makedirs(path, exist_ok=False)
            rel_path = os.path.relpath(path, self.workspace)
            self.refresh_folders(select_name=rel_path)
        except Exception as e:
            QMessageBox.critical(self, "新建失败", str(e))

    def _unique_workspace_folder(self, base_name):
        safe_name = self._safe_folder_name(base_name)
        target = os.path.join(self.workspace, safe_name)
        n = 2
        while os.path.exists(target):
            target = os.path.join(self.workspace, f"{safe_name}-{n}")
            n += 1
        return target

    def _unique_reel_path(self, folder_path, base_name):
        safe_name = "".join(c for c in (base_name or "").strip() if c not in r'\/:*?"<>|').strip() or "Reel"
        target = os.path.join(folder_path, f"{safe_name}.scomp")
        n = 2
        while os.path.exists(target):
            target = os.path.join(folder_path, f"{safe_name}-{n}.scomp")
            n += 1
        return target

    def _has_reel_files(self, folder_path):
        try:
            return bool(get_reels_in_folder(folder_path, recursive=True))
        except Exception:
            return False

    def import_project_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "导入项目文件夹", os.getcwd())
        if folder:
            self.import_project_folder(folder)

    def cloudify_project_folder_assets(self, folder_path):
        copied = 0
        missing = []
        if not self.is_cloud_workspace():
            return copied, missing
        for reel_path in get_reels_in_folder(folder_path, recursive=True):
            try:
                project = load_project(reel_path)
                _, report = sync_project_assets_to_project_dir(project)
                copied += len(report.get("copied", []))
                missing.extend(report.get("missing", []))
            except Exception:
                continue
        return copied, missing

    def import_project_folder(self, folder_path):
        folder_path = os.path.abspath(folder_path)
        if not os.path.isdir(folder_path):
            return
        if not self._has_reel_files(folder_path):
            QMessageBox.warning(self, "无法导入", "这个文件夹里没有找到 .scomp 工程文件。请拖入项目文件夹，或拖入某个 .scomp 所在的文件夹。")
            return

        workspace_abs = os.path.abspath(self.workspace)
        parent_abs = os.path.abspath(os.path.dirname(folder_path))
        if parent_abs == workspace_abs:
            self.refresh_folders(select_name=os.path.basename(folder_path))
            QMessageBox.information(self, "已定位项目", "这个项目已经在工作区里，已为你选中。")
            return

        target = self._unique_workspace_folder(os.path.basename(folder_path))
        try:
            shutil.copytree(folder_path, target)
            copied_assets, missing_assets = self.cloudify_project_folder_assets(target)
            folder_name = os.path.basename(target)
            self.refresh_folders(select_name=folder_name)
            cloud_note = ""
            if copied_assets:
                cloud_note += f"\n\n已自动复制 {copied_assets} 个素材到工程 assets，Google Drive 会继续同步上传。"
            if missing_assets:
                cloud_note += f"\n\n有 {len(missing_assets)} 个素材找不到，其他成员可能无法打开。"
            QMessageBox.information(self, "导入成功", f"项目文件夹已导入工作区：\n{target}{cloud_note}")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def _rewrite_copied_folder_project_paths(self, source_folder, target_folder):
        source_abs = os.path.abspath(source_folder)
        target_abs = os.path.abspath(target_folder)

        def rewrite_path(value):
            if not value:
                return value
            try:
                value_abs = os.path.abspath(value)
                if os.path.commonpath([value_abs, source_abs]) == source_abs:
                    return os.path.join(target_abs, os.path.relpath(value_abs, source_abs))
            except Exception:
                pass
            return value

        for reel_path in get_reels_in_folder(target_abs, recursive=True):
            try:
                project = load_project(reel_path)
                project["project_path"] = reel_path
                project["project_dir"] = os.path.dirname(reel_path)
                edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
                for clip in edit_state.get("video_clips", []) or []:
                    if isinstance(clip, dict):
                        clip["path"] = rewrite_path(clip.get("path", ""))
                if edit_state.get("audio_path"):
                    edit_state["audio_path"] = rewrite_path(edit_state.get("audio_path", ""))
                save_project(reel_path, project)
            except Exception:
                continue

    def copy_current_folder(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个项目文件夹。")
            return
        old_name = os.path.basename(self.current_folder)
        default_name = f"{old_name}_副本"
        new_name, ok = QInputDialog.getText(self, "复制项目", "复制为新的项目文件夹：", text=default_name)
        if not ok or not new_name.strip():
            return
        target = self._unique_workspace_folder(new_name)
        try:
            shutil.copytree(self.current_folder, target)
            self._rewrite_copied_folder_project_paths(self.current_folder, target)
            folder_name = os.path.basename(target)
            self.refresh_folders(select_name=folder_name)
            QMessageBox.information(self, "复制完成", f"已复制项目文件夹：\n{folder_name}")
        except Exception as e:
            QMessageBox.critical(self, "复制失败", str(e))

    def copy_reel_to_folder(self, reel_path, target_folder, ask_name=False):
        if not os.path.exists(reel_path) or not os.path.isdir(target_folder):
            return ""
        try:
            source_project = load_project(reel_path)
        except Exception as e:
            QMessageBox.critical(self, "复制失败", str(e))
            return ""
        base_name = os.path.splitext(os.path.basename(reel_path))[0]
        if ask_name:
            base_name, ok = QInputDialog.getText(self, "复制 Reel", "复制为：", text=f"{base_name}_副本")
            if not ok or not base_name.strip():
                return ""
        target_path = self._unique_reel_path(target_folder, base_name)
        target_name = os.path.splitext(os.path.basename(target_path))[0]
        try:
            shutil.copy2(reel_path, target_path)
            cover_rel = source_project.get("cover_img", "")
            old_cover = os.path.join(os.path.dirname(reel_path), cover_rel) if cover_rel else reel_path.replace(".scomp", "_cover.jpg")
            if old_cover and os.path.exists(old_cover):
                new_cover_rel = f"{target_name}_cover.jpg"
                shutil.copy2(old_cover, os.path.join(target_folder, new_cover_rel))
            else:
                new_cover_rel = source_project.get("cover_img", "")
            project = load_project(target_path)
            project["project_name"] = target_name
            project["project_path"] = target_path
            project["project_dir"] = target_folder
            if new_cover_rel:
                project["cover_img"] = new_cover_rel
            source_folder = os.path.abspath(os.path.dirname(reel_path))
            target_folder_abs = os.path.abspath(target_folder)

            def copy_internal_asset(value):
                if not value:
                    return value
                try:
                    value_abs = os.path.abspath(value)
                    if os.path.commonpath([value_abs, source_folder]) != source_folder:
                        return value
                    rel = os.path.relpath(value_abs, source_folder)
                    dest = os.path.join(target_folder_abs, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if os.path.isdir(value_abs):
                        shutil.copytree(value_abs, dest, dirs_exist_ok=True)
                    elif os.path.exists(value_abs) and not os.path.exists(dest):
                        shutil.copy2(value_abs, dest)
                    return dest
                except Exception:
                    return value

            edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
            for clip in edit_state.get("video_clips", []) or []:
                if isinstance(clip, dict):
                    clip["path"] = copy_internal_asset(clip.get("path", ""))
            if edit_state.get("audio_path"):
                edit_state["audio_path"] = copy_internal_asset(edit_state.get("audio_path", ""))
            save_project(target_path, project)
            self.refresh_reels_grid()
            return target_path
        except Exception as e:
            QMessageBox.critical(self, "复制失败", str(e))
            return ""

    def duplicate_reel(self, path):
        if not path or not os.path.exists(path):
            return
        target_folder = os.path.dirname(path)
        new_path = self.copy_reel_to_folder(path, target_folder, ask_name=True)
        if new_path:
            QMessageBox.information(self, "复制完成", f"已复制 Reel：\n{os.path.basename(new_path)}")

    def rename_reel(self, path):
        if not path or not os.path.exists(path):
            return
        old_name = os.path.splitext(os.path.basename(path))[0]
        new_name, ok = QInputDialog.getText(self, "重命名 Reel", "请输入新的 Reel 名称：", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        safe_name = "".join(c for c in new_name.strip() if c not in r'\/:*?"<>|').strip()
        if not safe_name:
            QMessageBox.warning(self, "提示", "名称不能为空。")
            return
        folder = os.path.dirname(path)
        new_path = os.path.join(folder, f"{safe_name}.scomp")
        if os.path.exists(new_path):
            QMessageBox.warning(self, "提示", "同名 Reel 已存在。")
            return
        try:
            if self.active_lock_project_path and os.path.normcase(os.path.abspath(self.active_lock_project_path)) == os.path.normcase(os.path.abspath(path)):
                self.release_active_cloud_lock()
            os.rename(path, new_path)
            old_cover = path.replace(".scomp", "_cover.jpg")
            new_cover = os.path.join(folder, f"{safe_name}_cover.jpg")
            project = load_project(new_path)
            project["project_name"] = safe_name
            project["project_path"] = new_path
            project["project_dir"] = folder
            if os.path.exists(old_cover):
                os.rename(old_cover, new_cover)
                project["cover_img"] = os.path.basename(new_cover)
            save_project(new_path, project)
            if self.project_data and os.path.normcase(os.path.abspath(self.project_data.get("project_path", ""))) == os.path.normcase(os.path.abspath(path)):
                self.project_data = load_project(new_path)
                self.sync_current_project_to_main()
                self.sync_current_project_label()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", str(e))

    def package_current_folder(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个项目文件夹。")
            return

        folder_name = os.path.basename(self.current_folder)
        default_path = os.path.join(self.workspace, f"{folder_name}.scompkg.zip")
        zip_path, _ = QFileDialog.getSaveFileName(self, "打包当前项目文件夹", default_path, "Subtitle Composer 工程包 (*.zip)")
        if not zip_path:
            return
        if not zip_path.lower().endswith(".zip"):
            zip_path += ".zip"

        try:
            self._zip_project_folder(self.current_folder, zip_path)
            QMessageBox.information(self, "打包完成", f"工程包已生成：\n{zip_path}\n\n包内已附带 subtitle_composer_audit.txt / .json，方便接收方先检查缺素材和字体授权状态。")
        except Exception as e:
            QMessageBox.critical(self, "打包失败", str(e))

    def _zip_project_folder(self, folder_path, zip_path):
        folder_path = os.path.abspath(folder_path)
        zip_path = os.path.abspath(zip_path)
        root_name = os.path.basename(folder_path)
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_path):
                for filename in files:
                    file_path = os.path.abspath(os.path.join(root, filename))
                    if file_path == zip_path:
                        continue
                    rel_path = os.path.relpath(file_path, folder_path)
                    arc_name = os.path.join(root_name, rel_path)
                    zf.write(file_path, arc_name)
            try:
                audit_report = scan_folder(folder_path, workspace=self.workspace)
                zf.writestr(os.path.join(root_name, "subtitle_composer_audit.json").replace("\\", "/"), scan_to_json(audit_report))
                zf.writestr(os.path.join(root_name, "subtitle_composer_audit.txt").replace("\\", "/"), format_scan_report(audit_report))
                font_families = []
                for project in audit_report.get("projects", []):
                    for row in project.get("fonts", {}).get("fonts", []):
                        family = str(row.get("font", "") or "").strip()
                        if family:
                            font_families.append(family)
                for file_path, arc_rel in font_package_entries_for_families(font_families):
                    zf.write(file_path, os.path.join(root_name, arc_rel).replace("\\", "/"))
            except Exception:
                pass

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path) or path.lower().endswith(".scomp"):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.import_project_folder(path)
                event.acceptProposedAction()
                return
            if path.lower().endswith(".scomp"):
                self.import_project_folder(os.path.dirname(path))
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def rename_folder_by_name(self, old_name, new_name, show_dialogs=True):
        old_name = str(old_name or "").strip()
        safe_name = self._safe_folder_name(new_name)
        if not old_name or not safe_name or safe_name == os.path.basename(old_name):
            return ""
        old_path = os.path.join(self.workspace, old_name)
        new_path = os.path.join(os.path.dirname(old_path), safe_name)
        if not os.path.isdir(old_path):
            if show_dialogs:
                QMessageBox.warning(self, "提示", "原项目文件夹不存在。")
            return ""
        if os.path.exists(new_path):
            if show_dialogs:
                QMessageBox.warning(self, "提示", "该项目名称已存在！")
            return ""
        try:
            if self.active_lock_project_path and os.path.abspath(self.active_lock_project_path).startswith(os.path.abspath(old_path) + os.sep):
                self.release_active_cloud_lock()
            os.rename(old_path, new_path)
            if self.project_data and os.path.normcase(os.path.abspath(self.project_data.get("project_dir", ""))) == os.path.normcase(os.path.abspath(old_path)):
                old_scomp = self.project_data.get("project_path", "")
                new_scomp = old_scomp.replace(old_path, new_path, 1)
                if os.path.exists(new_scomp):
                    self.project_data = load_project(new_scomp)
                    self.sync_current_project_to_main()
                    self.sync_current_project_label()
            self.current_folder = new_path
            self.current_reel_dir = new_path
            return os.path.relpath(new_path, self.workspace)
        except Exception as e:
            if show_dialogs:
                QMessageBox.critical(self, "重命名失败", str(e))
            return ""

    # 👑 新增：重命名项目
    def rename_current_folder(self):
        if not self.current_folder: return
        old_name = os.path.basename(self.current_folder)
        old_rel = os.path.relpath(self.current_folder, self.workspace)
        new_name, ok = QInputDialog.getText(self, "重命名项目", "请输入新的项目名称：", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            renamed = self.rename_folder_by_name(old_rel, new_name, show_dialogs=True)
            if renamed:
                self.refresh_folders(select_name=renamed)

    # 👑 新增：删除项目
    def delete_current_folder(self):
        if not self.current_folder: return
        folder_name = os.path.basename(self.current_folder)
        reply = QMessageBox.warning(self, '移动到垃圾桶', f'确认把项目【{folder_name}】及其所有内容移动到垃圾桶吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if self.active_lock_project_path and os.path.abspath(self.active_lock_project_path).startswith(os.path.abspath(self.current_folder) + os.sep):
                    self.release_active_cloud_lock()
                self._move_path_to_trash(self.current_folder)

                # 如果正在加载的 Reel 被删了，清理大盘数据
                if self.project_data and self.project_data.get("project_dir", "") == self.current_folder:
                    self.project_data = {}
                    self.sync_current_project_label()
                    self.sync_current_project_to_main()

                self.current_folder = ""
                self.current_reel_dir = ""
                self.lbl_folder_title.setText("请在左侧选择一个项目...")
                self.refresh_folders()
            except Exception as e:
                QMessageBox.critical(self, "删除失败", str(e))

    def on_folder_selected(self, item):
        if not item: return
        rel_path = self._folder_rel_from_item(item)
        if rel_path == PROJECT_HOME_NODE:
            self.show_project_home()
            return
        self.current_folder = os.path.join(self.workspace, rel_path)
        self.current_reel_dir = self.current_folder
        self.remember_recent_folder(self.current_folder)
        self.selected_reel_paths.clear()
        self._last_selected_reel_path = ""
        self.update_reel_folder_title()
        self.refresh_workspace_summary()
        self.refresh_reels_grid()

    def update_reel_folder_title(self):
        if not self.current_folder:
            self.lbl_folder_title.setText("请在左侧选择一个项目...")
            return
        project_name = os.path.basename(self.current_folder)
        if self.current_reel_dir and os.path.normcase(os.path.abspath(self.current_reel_dir)) != os.path.normcase(os.path.abspath(self.current_folder)):
            self.lbl_folder_title.setText(f"📁 {project_name} / {os.path.basename(self.current_reel_dir)} 下的 Reels")
        else:
            self.lbl_folder_title.setText(f"📁 {project_name} 下的 Reels")

    def reel_group_dirs(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            return []
        excluded = {"assets", "fonts", "__pycache__"}
        groups = []
        try:
            for name in os.listdir(self.current_folder):
                if name.startswith(".") or name in excluded:
                    continue
                path = os.path.join(self.current_folder, name)
                if not os.path.isdir(path):
                    continue
                has_marker = os.path.exists(os.path.join(path, REEL_GROUP_MARKER))
                has_reel = bool(get_reels_in_folder(path))
                if has_marker or has_reel:
                    groups.append(path)
        except Exception:
            return []
        return sorted(groups, key=lambda item: os.path.basename(item).casefold())

    def open_reel_group(self, folder_path):
        if folder_path and os.path.isdir(folder_path):
            self.current_reel_dir = folder_path
            self.update_reel_folder_title()
            self.refresh_reels_grid()

    def open_reel_root(self):
        if self.current_folder:
            self.current_reel_dir = self.current_folder
            self.update_reel_folder_title()
            self.refresh_reels_grid()

    def create_reel_group(self):
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(self, "提示", "请先选择一个项目文件夹。")
            return
        name, ok = QInputDialog.getText(self, "新建 Reel 分组", "请输入分组文件夹名称：")
        if not ok or not name.strip():
            return
        safe_name = self._safe_folder_name(name)
        path = os.path.join(self.current_folder, safe_name)
        if os.path.exists(path):
            QMessageBox.warning(self, "提示", "同名分组已存在。")
            return
        try:
            os.makedirs(path, exist_ok=False)
            with open(os.path.join(path, REEL_GROUP_MARKER), "w", encoding="utf-8") as f:
                f.write("Subtitle Composer Reel group\n")
            self.current_reel_dir = path
            self.update_reel_folder_title()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "新建分组失败", str(e))

    def rename_reel_group(self, folder_path):
        if not folder_path or not os.path.isdir(folder_path):
            return
        old_name = os.path.basename(folder_path)
        new_name, ok = QInputDialog.getText(self, "重命名分组", "请输入新的分组名称：", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        safe_name = self._safe_folder_name(new_name)
        new_path = os.path.join(self.current_folder, safe_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "提示", "同名分组已存在。")
            return
        try:
            os.rename(folder_path, new_path)
            if os.path.normcase(os.path.abspath(self.current_reel_dir)) == os.path.normcase(os.path.abspath(folder_path)):
                self.current_reel_dir = new_path
            self.update_reel_folder_title()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "重命名分组失败", str(e))

    def delete_reel_group(self, folder_path):
        if not folder_path or not os.path.isdir(folder_path):
            return
        name = os.path.basename(folder_path)
        reel_count = len(get_reels_in_folder(folder_path, recursive=True))
        reply = QMessageBox.warning(
            self,
            "移动到垃圾桶",
            f"确认把分组【{name}】移动到垃圾桶吗？\n其中包含 {reel_count} 个 Reel。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._move_path_to_trash(folder_path)
            if os.path.normcase(os.path.abspath(self.current_reel_dir)) == os.path.normcase(os.path.abspath(folder_path)):
                self.current_reel_dir = self.current_folder
            self.update_reel_folder_title()
            self.refresh_workspace_summary()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "删除分组失败", str(e))

    def _grid_available_width(self):
        if hasattr(self, "scroll_area") and self.scroll_area.viewport():
            return max(1, self.scroll_area.viewport().width() - 18)
        return max(1, self.width() - 360)

    def _grid_card_metrics(self, compact_mode=False):
        metrics = getattr(self, "project_metrics", PROJECT_GRID_METRICS)
        available = self._grid_available_width()
        columns = compact_project_grid_columns(available, metrics)
        width = project_card_width(available, metrics)
        height = PROJECT_COMPACT_CARD_HEIGHT if compact_mode else metrics.card_height
        return max(1, columns), width, height

    def _place_grid_card(self, widget):
        self.grid_layout.addWidget(widget, self._grid_row, self._grid_col)
        self._grid_col += 1
        if self._grid_col >= self._grid_col_count:
            self._grid_col = 0
            self._grid_row += 1

    def _clear_project_grid(self):
        self._grid_generation = getattr(self, "_grid_generation", 0) + 1
        token = self._grid_generation
        self._pending_reel_records = []
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        return token

    def _configure_grid_metrics(self, compact_mode=False):
        c = self.theme_colors()
        col_count, card_w, card_h = self._grid_card_metrics(compact_mode=compact_mode)
        self.grid_layout.setSpacing(self.project_metrics.grid_gap)
        self._grid_col_count = col_count
        self._grid_row = 0
        self._grid_col = 0
        self._grid_card_width = card_w
        self._grid_card_height = card_h
        self._grid_compact_mode = bool(compact_mode)
        self._grid_theme_colors = c
        return c, col_count, card_w, card_h

    def _place_grid_section(self, title, subtitle=""):
        if self._grid_col:
            self._grid_col = 0
            self._grid_row += 1
        c = self.theme_colors()
        label = QLabel(title if not subtitle else f"{title}  ·  {subtitle}")
        label.setStyleSheet(
            f"color: {c['accent_2']}; font-size: 13px; font-weight: 900; "
            "padding: 4px 2px 2px 2px; background: transparent; border: none;"
        )
        self.grid_layout.addWidget(label, self._grid_row, 0, 1, max(1, self._grid_col_count))
        self._grid_row += 1
        self._grid_col = 0

    def _make_project_action_card(self, icon, title, subtitle, accent, callback, width, height):
        c = self.theme_colors()
        card = QFrame()
        card.setFixedSize(width, height)
        card.setStyleSheet(
            f"QFrame {{ background-color: {c['hint']}; border: 1px dashed {accent}; border-radius: 9px; }}"
            f"QFrame:hover {{ border-color: {c['accent_2']}; background-color: {c['card_hover']}; }}"
        )
        card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(f"color: {accent}; font-size: 22px; font-weight: 900; border: none; background: transparent;")
        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(f"color: {c['text']}; font-size: 13px; font-weight: 900; border: none; background: transparent;")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {c['muted']}; font-size: 10px; border: none; background: transparent;")
        layout.addStretch(1)
        layout.addWidget(icon_lbl)
        layout.addWidget(title_lbl)
        if subtitle:
            layout.addWidget(sub_lbl)
        layout.addStretch(1)
        card.mousePressEvent = lambda e, cb=callback: cb() if e.button() == Qt.MouseButton.LeftButton else None
        return card


    def _project_root_folder_for_path(self, path):
        folder = path if os.path.isdir(path or "") else os.path.dirname(path or "")
        if not folder:
            return ""
        folder = os.path.abspath(folder)
        try:
            top_folders = get_project_folder_paths(self.workspace, recursive=False)
            for rel_path in top_folders:
                top_path = os.path.abspath(os.path.join(self.workspace, rel_path))
                if os.path.isdir(top_path) and _is_path_inside(folder, top_path):
                    return top_path
        except Exception:
            pass
        return folder if os.path.isdir(folder) and _is_path_inside(folder, self.workspace) else ""

    def _recent_folder_paths(self):
        try:
            config = load_app_config()
        except Exception:
            config = {}
        raw = config.get(PROJECT_RECENT_FOLDERS_KEY, [])
        if not raw:
            raw = [os.path.dirname(path) for path in config.get(PROJECT_RECENT_REELS_KEY, []) if isinstance(path, str)]
        paths = []
        seen = set()
        for path in raw if isinstance(raw, list) else []:
            root = self._project_root_folder_for_path(path)
            if not root:
                continue
            key = os.path.normcase(os.path.abspath(root))
            if key in seen:
                continue
            seen.add(key)
            paths.append(root)
            if len(paths) >= PROJECT_RECENT_LIMIT:
                break
        return paths

    def remember_recent_folder(self, folder_path):
        root = self._project_root_folder_for_path(folder_path)
        if not root:
            return
        try:
            data = load_app_config()
            current = data.get(PROJECT_RECENT_FOLDERS_KEY, [])
            paths = [root]
            paths.extend(p for p in current if isinstance(p, str))
            clean = []
            seen = set()
            for item in paths:
                normalized = self._project_root_folder_for_path(item)
                if not normalized:
                    continue
                key = os.path.normcase(os.path.abspath(normalized))
                if key in seen:
                    continue
                seen.add(key)
                clean.append(normalized)
                if len(clean) >= PROJECT_RECENT_LIMIT:
                    break
            data[PROJECT_RECENT_FOLDERS_KEY] = clean
            save_app_config(data)
        except Exception:
            pass
    def _recent_reel_paths(self):
        try:
            raw = load_app_config().get(PROJECT_RECENT_REELS_KEY, [])
        except Exception:
            raw = []
        paths = []
        seen = set()
        for path in raw if isinstance(raw, list) else []:
            if not isinstance(path, str) or not path.strip() or not os.path.exists(path):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
            if len(paths) >= PROJECT_RECENT_LIMIT:
                break
        return paths

    def remember_recent_reel(self, path):
        if not path:
            return
        self.remember_recent_folder(os.path.dirname(path))
        try:
            data = load_app_config()
            current = data.get(PROJECT_RECENT_REELS_KEY, [])
            paths = [path]
            paths.extend(p for p in current if isinstance(p, str))
            clean = []
            seen = set()
            for item in paths:
                if not item or not os.path.exists(item):
                    continue
                key = os.path.normcase(os.path.abspath(item))
                if key in seen:
                    continue
                seen.add(key)
                clean.append(item)
                if len(clean) >= PROJECT_RECENT_LIMIT:
                    break
            data[PROJECT_RECENT_REELS_KEY] = clean
            save_app_config(data)
        except Exception:
            pass
    def _top_level_project_folder_paths(self):
        folders = get_project_folder_paths(self.workspace, recursive=False)
        query = getattr(self, "reel_filter", "").strip().lower()
        results = []
        for rel_path in folders:
            if query and query not in rel_path.lower():
                continue
            folder_path = os.path.join(self.workspace, rel_path)
            if os.path.isdir(folder_path):
                results.append(folder_path)
        return results

    def open_project_folder_path(self, folder_path):
        if not folder_path or not os.path.isdir(folder_path):
            return
        rel_path = os.path.relpath(folder_path, self.workspace)
        item = self._find_folder_item(rel_path)
        if item:
            self.folder_list.setCurrentItem(item)
        self.current_folder = folder_path
        self.current_reel_dir = folder_path
        self.remember_recent_folder(folder_path)
        self.selected_reel_paths.clear()
        self._last_selected_reel_path = ""
        self.update_reel_folder_title()
        self.refresh_workspace_summary()
        self.refresh_reels_grid()

    def show_project_home(self):
        self.current_folder = ""
        self.current_reel_dir = ""
        self.selected_reel_paths.clear()
        self._last_selected_reel_path = ""
        self._visible_reel_paths = []
        self._reel_cards = {}
        token = self._clear_project_grid()
        c, _, card_w, card_h = self._configure_grid_metrics(compact_mode=False)
        action_h = min(PROJECT_ACTION_CARD_HEIGHT, card_h)
        recent_folders = self._recent_folder_paths()
        folder_paths = self._top_level_project_folder_paths()
        if hasattr(self, "lbl_folder_title"):
            self.lbl_folder_title.setText("🏠 主目录 / 最近访问")
        if hasattr(self, "lbl_reel_summary"):
            self.lbl_reel_summary.setText(f"最近访问 {len(recent_folders)} 个文件夹 | 主目录 {len(folder_paths)} 个一级文件夹")
        self.refresh_workspace_summary()

        self._place_grid_section("最近访问", "最近打开的项目文件夹会排在前面")
        if recent_folders:
            for folder_path in recent_folders:
                if token != getattr(self, "_grid_generation", 0):
                    return
                count = count_reels_fast(folder_path, recursive=True)
                folder_card = ReelFolderCard(folder_path, count, c, card_width=card_w)
                folder_card.clicked.connect(self.open_project_folder_path)
                folder_card.reel_dropped.connect(self.move_reel_to_folder)
                self._place_grid_card(folder_card)
        else:
            self._place_grid_card(
                self._make_project_action_card(
                    "↗",
                    "还没有最近文件夹",
                    "进入项目文件夹后会出现在这里",
                    c["muted"],
                    lambda: None,
                    card_w,
                    action_h,
                )
            )

        self._place_grid_section("主目录", "一级文件夹，进入后再管理里面的项目")
        self._place_grid_card(
            self._make_project_action_card("+", "新建文件夹", "分类/项目", c["accent"], self.create_new_folder, card_w, action_h)
        )
        self._place_grid_card(
            self._make_project_action_card("⇥", "导入项目", "拖入或选择文件夹", c.get("ok", c.get("accent_2", c["accent"])), self.import_project_folder_dialog, card_w, action_h)
        )
        for folder_path in folder_paths:
            count = count_reels_fast(folder_path, recursive=True)
            folder_card = ReelFolderCard(folder_path, count, c, card_width=card_w)
            folder_card.clicked.connect(self.open_project_folder_path)
            folder_card.reel_dropped.connect(self.move_reel_to_folder)
            self._place_grid_card(folder_card)
        self._sync_reel_selection_ui()
    def _render_reel_batch(self, token):
        if token != getattr(self, "_grid_generation", 0):
            return
        pending = list(getattr(self, "_pending_reel_records", []) or [])
        if not pending:
            self._sync_reel_selection_ui()
            return
        compact_mode = bool(getattr(self, "_grid_compact_mode", False))
        batch_size = 34 if compact_mode else 12
        batch = pending[:batch_size]
        self._pending_reel_records = pending[batch_size:]
        c = getattr(self, "_grid_theme_colors", self.theme_colors())
        width = int(getattr(self, "_grid_card_width", 184))
        height = int(getattr(self, "_grid_card_height", self.project_metrics.card_height))
        for path, p_data in batch:
            try:
                if compact_mode:
                    card = ReelCompactCard(path, p_data, c, card_width=width)
                else:
                    if p_data is None:
                        p_data = load_project(path)
                    card = ReelCard(p_data, card_width=width, card_height=height)
                    card.apply_theme(c)
                card.clicked.connect(self.load_and_enter_project)
                card.selection_clicked.connect(self.on_reel_selection_clicked)
                card.delete_clicked.connect(self.delete_reel)
                card.rename_clicked.connect(self.rename_reel)
                card.duplicate_clicked.connect(self.duplicate_reel)
                self._reel_cards[path] = card
                card.set_selected(path in self.selected_reel_paths)
                self._place_grid_card(card)
            except Exception:
                continue
        self._sync_reel_selection_ui()
        if self._pending_reel_records:
            QTimer.singleShot(1, lambda t=token: self._render_reel_batch(t))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_resize_refresh_timer"):
            self._resize_refresh_timer.start(180)

    def trash_root(self):
        root = os.path.join(self.workspace, TRASH_DIR_NAME)
        os.makedirs(root, exist_ok=True)
        return root

    def _trash_bucket_dir(self, label):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[\\/:*?"<>|]+', "_", str(label or "item")).strip(" ._") or "item"
        base = os.path.join(self.trash_root(), f"{stamp}_{safe}")
        candidate = base
        n = 2
        while os.path.exists(candidate):
            candidate = f"{base}_{n}"
            n += 1
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def _move_path_to_trash(self, path):
        if not path or not os.path.exists(path):
            return ""
        bucket = self._trash_bucket_dir(os.path.basename(path))
        dest = os.path.join(bucket, os.path.basename(path))
        shutil.move(path, dest)
        return dest

    def _trash_reel_file(self, path):
        if not path or not os.path.exists(path):
            return ""
        if self.active_lock_project_path and os.path.normcase(os.path.abspath(self.active_lock_project_path)) == os.path.normcase(os.path.abspath(path)):
            self.release_active_cloud_lock()
        bucket = self._trash_bucket_dir(os.path.splitext(os.path.basename(path))[0])
        dest = os.path.join(bucket, os.path.basename(path))
        cover_candidates = [path.replace(".scomp", "_cover.jpg")]
        try:
            data = load_project(path)
            cover_rel = data.get("cover_img", "")
            if cover_rel:
                cover_candidates.append(os.path.join(os.path.dirname(path), cover_rel))
        except Exception:
            pass
        shutil.move(path, dest)
        moved_covers = set()
        for cover_path in cover_candidates:
            if cover_path and os.path.exists(cover_path) and cover_path not in moved_covers:
                moved_covers.add(cover_path)
                shutil.move(cover_path, os.path.join(bucket, os.path.basename(cover_path)))
        return dest

    def open_trash_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.trash_root()))

    def _selected_visible_reel_paths(self):
        return [
            path
            for path in getattr(self, "_visible_reel_paths", [])
            if path in self.selected_reel_paths and os.path.exists(path)
        ]

    def _normalize_project_music_paths(self, paths):
        clean = []
        for path in paths or []:
            p = str(path or "").strip()
            if p and p.lower().endswith(PROJECT_AUDIO_EXTS) and os.path.exists(p) and p not in clean:
                clean.append(p)
        return clean

    def _normalize_project_media_paths(self, paths):
        clean = []
        for path in paths or []:
            p = str(path or "").strip()
            if p and p.lower().endswith(PROJECT_MEDIA_EXTS) and os.path.exists(p) and p not in clean:
                clean.append(p)
        return clean

    def _asset_assign_mode_label(self, mode):
        return next((label for label, value in PROJECT_MUSIC_ASSIGN_MODES if value == mode), "顺序循环")

    def _select_asset_assign_mode(self, asset_count, asset_name):
        if asset_count <= 1:
            return "first"
        labels = [label for label, _ in PROJECT_MUSIC_ASSIGN_MODES]
        choice, ok = QInputDialog.getItem(
            self,
            f"{asset_name}分配方式",
            f"多个{asset_name}如何分配到选中的 Reel？",
            labels,
            0,
            False,
        )
        if not ok:
            return ""
        return dict(PROJECT_MUSIC_ASSIGN_MODES).get(choice, "cycle")

    def _music_assign_mode_label(self, mode):
        return self._asset_assign_mode_label(mode)

    def _select_music_assign_mode(self, music_count):
        return self._select_asset_assign_mode(music_count, "配乐")

    def _music_path_for_reel_order(self, music_paths, order, mode):
        if not music_paths:
            return ""
        if mode == "random":
            return random.choice(music_paths)
        if mode == "first":
            return music_paths[0]
        return music_paths[int(order or 0) % len(music_paths)]

    def _media_path_for_reel_order(self, media_paths, order, mode):
        if not media_paths:
            return ""
        if mode == "random":
            return random.choice(media_paths)
        if mode == "first":
            return media_paths[0]
        return media_paths[int(order or 0) % len(media_paths)]

    def _replace_reel_video(self, reel_path, media_path):
        project = load_project(reel_path)
        edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
        media_dur = float(get_video_stream_duration(media_path) or get_exact_duration(media_path) or 0.0)
        try:
            project_duration = float(edit_state.get("duration", 0.0) or 0.0)
        except Exception:
            project_duration = 0.0
        target_end = max(1.0, project_duration or media_dur or 1.0)
        clips = edit_state.get("video_clips", [])
        if not isinstance(clips, list):
            clips = []
        if clips and isinstance(clips[0], dict):
            clip = clips[0]
            try:
                old_start = float(clip.get("start", 0.0) or 0.0)
            except Exception:
                old_start = 0.0
            try:
                old_end = float(clip.get("end", 0.0) or 0.0)
            except Exception:
                old_end = 0.0
            clip["path"] = media_path
            clip["dur"] = media_dur
            clip["start"] = old_start
            clip["end"] = max(old_start + 0.1, old_end or target_end)
        else:
            clips = [{"path": media_path, "start": 0.0, "end": target_end, "dur": media_dur}]
        edit_state["video_clips"] = clips
        project["timeline"] = copy.deepcopy(clips)
        project.setdefault("media_files", {})["video_clips"] = copy.deepcopy(clips)
        return save_project(reel_path, project)

    def replace_selected_reels_video_dialog(self):
        reel_paths = self._selected_visible_reel_paths()
        if not reel_paths:
            return QMessageBox.information(self, "未选择", "先在工程面板里按 Ctrl 或 Shift 选择要换画面的 Reel。")
        if self.is_cloud_workspace() and not self.ensure_cloud_identity():
            return

        media_paths, _ = QFileDialog.getOpenFileNames(self, "选择替换画面（可多选）", "", project_media_file_filter())
        media_paths = self._normalize_project_media_paths(media_paths)
        if not media_paths:
            return
        mode = self._select_asset_assign_mode(len(media_paths), "画面素材")
        if not mode:
            return
        media_preview = "、".join(os.path.basename(path) for path in media_paths[:3])
        if len(media_paths) > 3:
            media_preview += f" 等 {len(media_paths)} 个"
        reply = QMessageBox.question(
            self,
            "批量替换画面",
            f"将为 {len(reel_paths)} 个 Reel 替换主画面素材。\n\n画面池：{media_preview}\n分配方式：{self._asset_assign_mode_label(mode)}\n\n会保留字幕、配音、配乐和原工程时长设置。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success, errors = self._apply_selected_reel_updates(
            reel_paths,
            lambda order, reel_path: self._replace_reel_video(reel_path, self._media_path_for_reel_order(media_paths, order, mode)),
        )
        self._finish_selected_reel_batch_update(success, errors, "批量换画面完成")

    def _replace_reel_music(self, reel_path, music_path):
        project = load_project(reel_path)
        edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
        music_dur = float(get_exact_duration(music_path) or 0.0)
        try:
            project_duration = float(edit_state.get("duration", 0.0) or 0.0)
        except Exception:
            project_duration = 0.0
        edit_state["music_path"] = music_path
        edit_state["music_dur"] = music_dur
        edit_state["music_match_duration"] = max(1.0, project_duration or music_dur or 1.0)
        edit_state["music_loop"] = True
        try:
            music_volume = int(float(edit_state.get("music_volume", 35) or 35))
        except Exception:
            music_volume = 35
        edit_state["music_volume"] = max(0, min(100, music_volume))
        project.setdefault("media_files", {})["music_path"] = music_path
        return save_project(reel_path, project)

    def _load_project_style_presets(self):
        presets = {}
        loaded = read_json_file(PROJECT_STYLE_PRESETS_FILE, default={})
        if isinstance(loaded, dict):
            presets.update(loaded)
        return merge_built_in_style_presets(presets)

    def _apply_style_to_reel(self, reel_path, preset_style, preset_position):
        project = load_project(reel_path)
        edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
        edit_state.setdefault("default_style", {})
        edit_state["default_style"].update(copy.deepcopy(preset_style))
        if preset_position:
            edit_state["default_pos_x"] = preset_position["pos_x"]
            edit_state["default_pos_y"] = preset_position["pos_y"]
        subs = edit_state.get("subs_data")
        if not isinstance(subs, list):
            subs = project.get("subs_data", []) if isinstance(project.get("subs_data"), list) else []
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            sub.setdefault("style", {})
            sub["style"].update(copy.deepcopy(preset_style))
            if preset_position:
                sub["pos_x"] = preset_position["pos_x"]
                sub["pos_y"] = preset_position["pos_y"]
        edit_state["subs_data"] = subs
        project["subs_data"] = copy.deepcopy(subs)
        return save_project(reel_path, project)

    def apply_style_to_selected_reels_dialog(self):
        reel_paths = self._selected_visible_reel_paths()
        if not reel_paths:
            return QMessageBox.information(self, "未选择", "先在工程面板里选择要套样式的 Reel。")
        presets = self._load_project_style_presets()
        if not presets:
            return QMessageBox.information(self, "没有预设", "还没有可用的字幕样式预设。先在精修里保存一个样式预设。")
        names = list(presets.keys())
        preset_name, ok = QInputDialog.getItem(self, "批量套字幕样式", "选择要应用的样式预设：", names, 0, False)
        if not ok or not preset_name:
            return
        preset_style, preset_position = split_project_style_preset(presets.get(preset_name, {}))
        if not preset_style:
            return QMessageBox.warning(self, "预设不可用", "这个样式预设没有可应用的样式字段。")
        reply = QMessageBox.question(
            self,
            "批量套字幕样式",
            f"将把「{preset_name}」应用到 {len(reel_paths)} 个 Reel 的现有字幕和默认字幕样式。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        success, errors = self._apply_selected_reel_updates(
            reel_paths,
            lambda order, reel_path: self._apply_style_to_reel(reel_path, preset_style, preset_position),
        )
        self._finish_selected_reel_batch_update(success, errors, "批量套样式完成")

    def _built_in_project_signature_presets(self):
        base = default_signature_config(None)
        return {
            "右上角柔光玻璃": copy.deepcopy(base),
            "右上角纯色小标": {
                **copy.deepcopy(base),
                "style": {
                    **copy.deepcopy(base["style"]),
                    "bg_mode": "block",
                    "bg_alpha": 58,
                    "bg_radius": 14,
                    "bg_padding": 8,
                    "bg_pad_left": 14,
                    "bg_pad_right": 14,
                    "bg_pad_top": 4,
                    "bg_pad_bottom": 5,
                },
            },
            "右上角无底透明字": {
                **copy.deepcopy(base),
                "style": {
                    **copy.deepcopy(base["style"]),
                    "bg_mode": "none",
                    "bg_alpha": 0,
                    "stroke_width": 2,
                    "shadow_alpha": 70,
                },
            },
        }

    def _load_project_signature_presets(self):
        presets = self._built_in_project_signature_presets()
        saved = read_json_file(PROJECT_SIGNATURE_PRESETS_FILE, default={})
        if isinstance(saved, dict):
            presets.update(saved)
        return presets

    def _apply_signature_to_reel(self, reel_path, mode, preset_signature=None, replacement_text=None):
        project = load_project(reel_path)
        edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
        default_style = edit_state.get("default_style", {}) if isinstance(edit_state.get("default_style"), dict) else {}
        existing_signature = normalize_signature_config(edit_state.get("signature"), default_style)

        if mode == "disable":
            signature = existing_signature
            signature["enabled"] = False
        elif mode == "text_only":
            signature = existing_signature
            signature["text"] = str(replacement_text or "").strip()
            signature["enabled"] = bool(signature["text"])
        else:
            signature = normalize_signature_config(copy.deepcopy(preset_signature or {}), default_style)
            if replacement_text is not None:
                signature["text"] = str(replacement_text or "").strip()
            elif existing_signature.get("text") and not signature.get("text"):
                signature["text"] = existing_signature.get("text", "")
            signature["enabled"] = True

        edit_state["signature"] = signature
        return save_project(reel_path, project)

    def apply_signature_to_selected_reels_dialog(self):
        reel_paths = self._selected_visible_reel_paths()
        if not reel_paths:
            return QMessageBox.information(self, "未选择", "先在工程面板里选择要替换署名的 Reel。")

        presets = self._load_project_signature_presets()
        template_labels = [f"套模板：{name}" for name in presets.keys()]
        choices = ["只替换文字（保留每个 Reel 原署名样式）", "关闭署名"] + template_labels
        choice, ok = QInputDialog.getItem(self, "批量换署名", "选择批量署名方式：", choices, 0, False)
        if not ok or not choice:
            return

        mode = "template"
        preset_name = ""
        preset_signature = None
        replacement_text = None
        summary = ""

        if choice == "关闭署名":
            mode = "disable"
            summary = "关闭署名"
        elif choice.startswith("只替换文字"):
            mode = "text_only"
            text, ok = QInputDialog.getText(self, "批量换署名", "输入新的署名文字：")
            if not ok:
                return
            replacement_text = text.strip()
            if not replacement_text:
                return QMessageBox.warning(self, "需要文字", "只替换文字模式需要输入署名文字。")
            summary = f"署名文字：{replacement_text}"
        else:
            preset_name = choice.replace("套模板：", "", 1)
            preset_signature = presets.get(preset_name)
            if not preset_signature:
                return QMessageBox.warning(self, "模板不可用", "没有找到这个署名模板。")
            default_text = str(normalize_signature_config(copy.deepcopy(preset_signature)).get("text", "") or "")
            text, ok = QInputDialog.getText(
                self,
                "批量换署名",
                "新的署名文字（留空=保留每个 Reel 原文字；没有原文字时使用模板文字）：",
                text=default_text,
            )
            if not ok:
                return
            replacement_text = text.strip() if text.strip() else None
            summary = f"套模板：{preset_name}"
            if replacement_text is not None:
                summary += f"\n署名文字：{replacement_text}"
            else:
                summary += "\n署名文字：保留每个 Reel 原文字"

        reply = QMessageBox.question(
            self,
            "批量换署名",
            f"将为 {len(reel_paths)} 个 Reel 执行：\n\n{summary}\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success, errors = self._apply_selected_reel_updates(
            reel_paths,
            lambda order, reel_path: self._apply_signature_to_reel(reel_path, mode, preset_signature, replacement_text),
        )
        self._finish_selected_reel_batch_update(success, errors, "批量换署名完成")
    def _update_reel_caption_modes(self, reel_path, chunk_mode, timing_mode):
        project = load_project(reel_path)
        edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
        edit_state["chunk_mode"] = chunk_mode
        if timing_mode:
            edit_state["timing_mode"] = timing_mode
        return save_project(reel_path, project)

    def update_selected_reels_caption_modes_dialog(self):
        reel_paths = self._selected_visible_reel_paths()
        if not reel_paths:
            return QMessageBox.information(self, "未选择", "先在工程面板里选择要统一听译模式的 Reel。")
        chunk_mode, ok = QInputDialog.getItem(self, "批量听译模式", "选择断句 / 字数模式：", PROJECT_CHUNK_MODES, 2, False)
        if not ok or not chunk_mode:
            return
        timing_choices = ["保持原时间模式"] + PROJECT_TIMING_MODES
        timing_mode, ok = QInputDialog.getItem(self, "批量听译模式", "选择时间模式：", timing_choices, 0, False)
        if not ok:
            return
        timing_value = "" if timing_mode == "保持原时间模式" else timing_mode
        reply = QMessageBox.question(
            self,
            "批量听译模式",
            f"将为 {len(reel_paths)} 个 Reel 设置：\n\n断句：{chunk_mode}\n时间：{timing_mode}\n\n这会影响后续 AI 听译/重新打轴，不会擅自重写现有字幕时间轴。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        success, errors = self._apply_selected_reel_updates(
            reel_paths,
            lambda order, reel_path: self._update_reel_caption_modes(reel_path, chunk_mode, timing_value),
        )
        self._finish_selected_reel_batch_update(success, errors, "批量听译模式完成")

    def _apply_selected_reel_updates(self, reel_paths, updater):
        success = 0
        errors = []
        current_path = self.project_data.get("project_path", "") if isinstance(self.project_data, dict) else ""
        current_path_abs = os.path.normcase(os.path.abspath(current_path)) if current_path else ""
        updated_current = False

        for order, reel_path in enumerate(reel_paths):
            try:
                updated_project = updater(order, reel_path)
                success += 1
                if current_path_abs and os.path.normcase(os.path.abspath(reel_path)) == current_path_abs:
                    self.project_data = updated_project
                    updated_current = True
            except Exception as e:
                errors.append(f"{os.path.basename(reel_path)}: {e}")

        if updated_current:
            self.sync_current_project_to_main()
            self.sync_current_project_label()
        if self.is_cloud_workspace():
            try:
                update_manifest_from_workspace(self.workspace)
            except Exception:
                pass
        self.refresh_reels_grid()
        return success, errors

    def _finish_selected_reel_batch_update(self, success, errors, title):
        msg = f"已处理 {success} 个 Reel。"
        if errors:
            msg += f"\n\n有 {len(errors)} 个失败：\n" + "\n".join(errors[:8])
        QMessageBox.information(self, title, msg)

    def replace_selected_reels_music_dialog(self):
        reel_paths = self._selected_visible_reel_paths()
        if not reel_paths:
            return QMessageBox.information(self, "未选择", "先在工程面板里按 Ctrl 或 Shift 选择要换配乐的 Reel。")
        if self.is_cloud_workspace() and not self.ensure_cloud_identity():
            return

        music_paths, _ = QFileDialog.getOpenFileNames(self, "选择替换配乐（可多选）", "", project_audio_file_filter())
        music_paths = self._normalize_project_music_paths(music_paths)
        if not music_paths:
            return

        mode = self._select_music_assign_mode(len(music_paths))
        if not mode:
            return

        music_preview = "、".join(os.path.basename(path) for path in music_paths[:3])
        if len(music_paths) > 3:
            music_preview += f" 等 {len(music_paths)} 首"
        reply = QMessageBox.question(
            self,
            "批量替换配乐",
            f"将为 {len(reel_paths)} 个 Reel 替换配乐。\n\n配乐池：{music_preview}\n分配方式：{self._music_assign_mode_label(mode)}\n\n会保留每个工程原来的配乐音量，并自动匹配工程时长。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success = 0
        errors = []
        current_path = self.project_data.get("project_path", "") if isinstance(self.project_data, dict) else ""
        current_path_abs = os.path.normcase(os.path.abspath(current_path)) if current_path else ""
        updated_current = False
        for order, reel_path in enumerate(reel_paths):
            music_path = self._music_path_for_reel_order(music_paths, order, mode)
            try:
                updated_project = self._replace_reel_music(reel_path, music_path)
                success += 1
                if current_path_abs and os.path.normcase(os.path.abspath(reel_path)) == current_path_abs:
                    self.project_data = updated_project
                    updated_current = True
            except Exception as e:
                errors.append(f"{os.path.basename(reel_path)}: {e}")

        if updated_current:
            self.sync_current_project_to_main()
            self.sync_current_project_label()
        if self.is_cloud_workspace():
            try:
                update_manifest_from_workspace(self.workspace)
            except Exception:
                pass
        self.refresh_reels_grid()

        msg = f"已替换 {success} 个 Reel 的配乐。"
        if errors:
            msg += f"\n\n有 {len(errors)} 个失败：\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "批量换配乐完成", msg)

    def _sync_reel_selection_ui(self):
        visible = set(getattr(self, "_visible_reel_paths", []))
        self.selected_reel_paths.intersection_update(visible)
        for path, card in getattr(self, "_reel_cards", {}).items():
            card.set_selected(path in self.selected_reel_paths)
        count = len(self.selected_reel_paths)
        if hasattr(self, "btn_replace_video_selected"):
            self.btn_replace_video_selected.setEnabled(count > 0)
            self.btn_replace_video_selected.setText(f"换画面({count})" if count else "换画面")
        if hasattr(self, "btn_replace_music_selected"):
            self.btn_replace_music_selected.setEnabled(count > 0)
            self.btn_replace_music_selected.setText(f"换配乐({count})" if count else "换配乐")
        if hasattr(self, "btn_apply_style_selected"):
            self.btn_apply_style_selected.setEnabled(count > 0)
            self.btn_apply_style_selected.setText(f"套样式({count})" if count else "套样式")
        if hasattr(self, "btn_apply_signature_selected"):
            self.btn_apply_signature_selected.setEnabled(count > 0)
            self.btn_apply_signature_selected.setText(f"\u6362\u7f72\u540d({count})" if count else "\u6362\u7f72\u540d")
        if hasattr(self, "btn_caption_mode_selected"):
            self.btn_caption_mode_selected.setEnabled(count > 0)
            self.btn_caption_mode_selected.setText(f"听译模式({count})" if count else "听译模式")
        if hasattr(self, "btn_move_selected"):
            self.btn_move_selected.setEnabled(count > 0)
        if hasattr(self, "btn_trash_selected"):
            self.btn_trash_selected.setEnabled(count > 0)
            self.btn_trash_selected.setText(f"删除选中({count})" if count else "删除选中")

    def on_reel_selection_clicked(self, path, modifiers):
        if not path:
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._last_selected_reel_path in self._visible_reel_paths:
            self.selected_reel_paths.update(
                contiguous_range_ids(self._visible_reel_paths, self._last_selected_reel_path, path)
            )
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            if path in self.selected_reel_paths:
                self.selected_reel_paths.remove(path)
            else:
                self.selected_reel_paths.add(path)
            self._last_selected_reel_path = path
        else:
            self.selected_reel_paths = {path}
            self._last_selected_reel_path = path
        self._sync_reel_selection_ui()

    def delete_selected_reels(self):
        paths = [path for path in self._visible_reel_paths if path in self.selected_reel_paths and os.path.exists(path)]
        if not paths:
            return
        reply = QMessageBox.warning(
            self,
            "移动到垃圾桶",
            f"确认把 {len(paths)} 个 Reel 移动到垃圾桶吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            for path in paths:
                self._trash_reel_file(path)
                if self.project_data.get("project_path") == path:
                    self.project_data = {}
                    self.sync_current_project_label()
                    self.sync_current_project_to_main()
            self.selected_reel_paths.clear()
            self.refresh_workspace_summary()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def move_selected_reels_dialog(self):
        paths = [path for path in self._visible_reel_paths if path in self.selected_reel_paths and os.path.exists(path)]
        if not paths:
            return QMessageBox.information(self, "未选择", "按住 Ctrl 或 Shift 点击 Reel 后再移动。")
        targets = []
        if self.current_folder and os.path.isdir(self.current_folder):
            targets.append((f"{os.path.basename(self.current_folder)} / 根目录", self.current_folder))
            for folder_path in self.reel_group_dirs():
                targets.append((f"{os.path.basename(self.current_folder)} / {os.path.basename(folder_path)}", folder_path))
        if not targets:
            return QMessageBox.warning(self, "没有目标", "请先选择一个工程文件夹。")
        labels = [label for label, _ in targets]
        choice, ok = QInputDialog.getItem(self, "移动选中 Reel", "移动到:", labels, 0, False)
        if not ok:
            return
        target = dict(targets).get(choice)
        if not target:
            return
        for path in paths:
            self.move_reel_to_folder(path, target)
        self.selected_reel_paths.clear()
        self.refresh_reels_grid()

    def move_reel_to_folder(self, reel_path, target_folder):
        if not reel_path or not target_folder or not os.path.exists(reel_path) or not os.path.isdir(target_folder):
            return
        if self.current_folder and not _is_path_inside(reel_path, self.current_folder):
            self.copy_reel_to_folder(reel_path, target_folder, ask_name=False)
            self.refresh_workspace_summary()
            self.refresh_reels_grid()
            return
        source_folder = os.path.dirname(reel_path)
        if os.path.normcase(os.path.abspath(source_folder)) == os.path.normcase(os.path.abspath(target_folder)):
            return
        target_path = self._unique_reel_path(target_folder, os.path.splitext(os.path.basename(reel_path))[0])
        target_name = os.path.splitext(os.path.basename(target_path))[0]
        try:
            project = load_project(reel_path)
            source_abs = os.path.abspath(source_folder)
            target_abs = os.path.abspath(target_folder)

            def copy_internal_asset(value):
                if not value:
                    return value
                try:
                    value_abs = os.path.abspath(value)
                    if os.path.commonpath([value_abs, source_abs]) != source_abs:
                        return value
                    rel = os.path.relpath(value_abs, source_abs)
                    dest = os.path.join(target_abs, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if os.path.exists(value_abs) and not os.path.exists(dest):
                        shutil.copy2(value_abs, dest)
                    return dest
                except Exception:
                    return value

            old_cover_rel = project.get("cover_img", "")
            old_cover = os.path.join(source_folder, old_cover_rel) if old_cover_rel else reel_path.replace(".scomp", "_cover.jpg")
            new_cover_rel = f"{target_name}_cover.jpg"
            if old_cover and os.path.exists(old_cover):
                shutil.copy2(old_cover, os.path.join(target_folder, new_cover_rel))
                project["cover_img"] = new_cover_rel
            edit_state = project.setdefault("room_state", {}).setdefault("edit_room", {})
            for clip in edit_state.get("video_clips", []) or []:
                if isinstance(clip, dict):
                    clip["path"] = copy_internal_asset(clip.get("path", ""))
            if edit_state.get("audio_path"):
                edit_state["audio_path"] = copy_internal_asset(edit_state.get("audio_path", ""))
            if edit_state.get("music_path"):
                edit_state["music_path"] = copy_internal_asset(edit_state.get("music_path", ""))
            project["project_path"] = target_path
            project["project_dir"] = target_folder
            project["project_name"] = target_name
            save_project(target_path, project)
            os.remove(reel_path)
            if self.project_data and os.path.normcase(os.path.abspath(self.project_data.get("project_path", ""))) == os.path.normcase(os.path.abspath(reel_path)):
                self.project_data = load_project(target_path)
                self.sync_current_project_to_main()
                self.sync_current_project_label()
            self.refresh_workspace_summary()
            self.refresh_reels_grid()
        except Exception as e:
            QMessageBox.critical(self, "移动 Reel 失败", str(e))

    def refresh_reels_grid(self):
        token = self._clear_project_grid()

        if not self.current_folder or not os.path.exists(self.current_folder):
            self.show_project_home()
            return

        if not self.current_reel_dir or not os.path.isdir(self.current_reel_dir):
            self.current_reel_dir = self.current_folder
        self.update_reel_folder_title()
        root_view = os.path.normcase(os.path.abspath(self.current_reel_dir)) == os.path.normcase(os.path.abspath(self.current_folder))
        query = getattr(self, "reel_filter", "")
        reels_paths = get_reels_in_folder(self.current_reel_dir)
        if query:
            total_paths = get_reels_in_folder(self.current_folder, recursive=True)
            total_reel_count = len(total_paths)
            candidate_paths = total_paths
        else:
            total_reel_count = count_reels_fast(self.current_folder, recursive=True)
            candidate_paths = reels_paths
        reel_records = []
        compact_mode = (not query) and len(candidate_paths) >= getattr(self, "performance_reel_threshold", 60)
        for path in candidate_paths:
            if compact_mode:
                reel_records.append((path, None))
                continue
            try:
                p_data = load_project(path)
                rel_path = os.path.relpath(path, self.current_folder)
                haystack = f"{p_data.get('project_name', '')} {os.path.basename(path)} {rel_path}".lower()
                if query and query not in haystack:
                    continue
                reel_records.append((path, p_data))
            except Exception:
                if not query or query in os.path.basename(path).lower():
                    reel_records.append((path, None))
        self._visible_reel_paths = [path for path, _ in reel_records]
        self._reel_cards = {}
        if hasattr(self, "lbl_reel_summary"):
            folder_name = os.path.basename(self.current_folder)
            view_name = "全部分组搜索" if query else (os.path.basename(self.current_reel_dir) if not root_view else "根目录")
            suffix = f" | 搜索: {query}" if query else ""
            perf_hint = " | 轻量浏览" if compact_mode else ""
            self.lbl_reel_summary.setText(f"{folder_name} / {view_name}: {len(reel_records)} / {total_reel_count} 个 Reel{suffix}{perf_hint}")
        c, col_count, card_w, card_h = self._configure_grid_metrics(compact_mode=compact_mode)
        action_h = min(PROJECT_ACTION_CARD_HEIGHT, card_h)

        if not root_view:
            self._place_grid_card(
                self._make_project_action_card(
                    "↩",
                    "返回根目录",
                    "",
                    c["accent"],
                    self.open_reel_root,
                    card_w,
                    action_h,
                )
            )

        self._place_grid_card(
            self._make_project_action_card(
                "+",
                "新建 Reel",
                "从空白工程开始",
                c["accent_2"],
                self.create_new_reel,
                card_w,
                action_h,
            )
        )

        if root_view:
            self._place_grid_card(
                self._make_project_action_card(
                    "▣",
                    "新建分组",
                    "整理 Reels",
                    c["accent"],
                    self.create_reel_group,
                    card_w,
                    action_h,
                )
            )

        self._place_grid_card(
            self._make_project_action_card(
                "✣",
                "批量创建",
                "表格/素材生成",
                c["warn"],
                self.open_batch_project_builder,
                card_w,
                action_h,
            )
        )

        if root_view and not query:
            for folder_path in self.reel_group_dirs():
                folder_card = ReelFolderCard(folder_path, count_reels_fast(folder_path, recursive=True), c, card_width=card_w)
                folder_card.clicked.connect(self.open_reel_group)
                folder_card.reel_dropped.connect(self.move_reel_to_folder)
                folder_card.rename_clicked.connect(self.rename_reel_group)
                folder_card.delete_clicked.connect(self.delete_reel_group)
                self._place_grid_card(folder_card)
        self._pending_reel_records = list(reel_records)
        QTimer.singleShot(0, lambda t=token: self._render_reel_batch(t))
        self._sync_reel_selection_ui()

    def create_new_reel(self):
        if not self.current_folder: return
        target_dir = self.current_reel_dir if self.current_reel_dir and os.path.isdir(self.current_reel_dir) else self.current_folder
        if self.is_cloud_workspace() and not self.ensure_cloud_identity():
            return
        name, ok = QInputDialog.getText(self, "新建 Reel", "给你的新 Reel 起个名字：")
        if ok and name.strip():
            try:
                self.project_data = create_reel(target_dir, name.strip(), "edit_room")
                project_path = self.project_data.get("project_path", "")
                if self.is_cloud_workspace() and project_path and not self.prepare_cloud_project_lock(project_path):
                    return
                self.remember_recent_reel(project_path)
                self.sync_current_project_to_main()
                self.refresh_reels_grid()
                self.sync_current_project_label()
                parent = self.parent_window()
                if parent: parent.switch_room(1)
            except Exception as e:
                QMessageBox.critical(self, "创建失败", str(e))

    def open_batch_project_builder(self):
        target_dir = self.current_reel_dir if self.current_reel_dir and os.path.isdir(self.current_reel_dir) else self.current_folder
        if not target_dir or not os.path.isdir(target_dir):
            QMessageBox.warning(self, "请选择工程", "请先在左侧选择一个工程文件夹，或先新建一个工程。")
            return
        parent = self.parent_window()
        if not parent or not hasattr(parent, "room_batch"):
            QMessageBox.warning(self, "无法打开", "没有找到批量创建房间。")
            return
        if self.is_cloud_workspace() and not self.ensure_cloud_identity():
            return
        parent.room_batch.prepare_project_builder(target_dir, os.path.basename(target_dir))
        parent.switch_room(2)

    def load_and_enter_project(self, path):
        if not self.prepare_cloud_project_lock(path):
            return
        try:
            self.project_data = load_project(path)
            if self.is_cloud_workspace():
                self.project_data, report = sync_project_assets_to_project_dir(self.project_data)
                if report.get("copied"):
                    QMessageBox.information(
                        self,
                        "素材已云端化",
                        f"已自动复制 {len(report['copied'])} 个本机素材到当前工程 assets。\nGoogle Drive 会在后台继续同步上传。"
                    )
            self.remember_recent_reel(path)
            self.sync_current_project_to_main()
            self.sync_current_project_label()
            parent = self.parent_window()
            if parent: parent.switch_room(1)
        except Exception as e:
            QMessageBox.critical(self, "载入失败", str(e))

    def delete_reel(self, path):
        if path in self.selected_reel_paths and len(self.selected_reel_paths) > 1:
            return self.delete_selected_reels()
        reply = QMessageBox.warning(self, '移动到垃圾桶', '确认把该 Reel 移动到垃圾桶吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._trash_reel_file(path)
                self.refresh_workspace_summary()
                self.refresh_reels_grid()

                # 如果删除的刚好是当前加载的，则清空引用
                if self.project_data.get("project_path") == path:
                    self.project_data = {}
                    self.sync_current_project_label()
                    self.sync_current_project_to_main()
            except Exception as e:
                QMessageBox.critical(self, "删除失败", str(e))

    def sync_current_project_to_main(self):
        parent = self.parent_window()
        if not parent: return
        parent.project = self.project_data
        if hasattr(parent, "reload_rooms_from_project"):
            parent.reload_rooms_from_project()
