# ==========================================
# 文件名: room_deliver.py (稳定版)
# ==========================================
import os
import json
import tempfile
import re
import threading
import subprocess
import shutil
import copy
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QProgressBar, QTextEdit, QFileDialog, QMessageBox, QDoubleSpinBox,
    QDialog, QTreeWidget, QTreeWidgetItem, QScrollArea, QGridLayout, QCheckBox, QSplitter,
    QTabBar, QInputDialog, QComboBox
)
from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QPixmap, QCursor
from core import get_ffmpeg_cmd, get_ffprobe_cmd
from app_theme import apply_tinted_styles
from room_theme_bridge import apply_room_theme_bridge
from app_config import EXPORT_RENDER_QUALITY_OPTIONS, get_export_render_quality, get_output_resolution, load_app_config, resolution_to_size, save_app_config, set_export_render_quality
from app_storage import read_json_file, resolve_user_file, write_json_file
from render_config import build_video_encoder_args, get_render_profile
from render_pipeline_model import (
    build_looped_assembly_segments,
    ffconcat_file_entry,
    ffconcat_inout_entry,
    ffmpeg_canvas_source,
    ffmpeg_exact_layer_filter,
    ffmpeg_layer_overlay_xy,
    ffmpeg_layer_scale_filter,
    ffmpeg_video_mask_filter,
)
from render_timing import active_subtitles_for_frame, build_subtitle_frame_schedule, render_tail_padding_seconds, subtitle_continuous_fps, subtitle_event_fps, subtitle_supersample
from render_performance import export_render_profile, simplify_signature_for_export, simplify_subtitle_for_export, summarize_project_render_cost
from playwright.sync_api import sync_playwright

from font_assets import font_face_css
from ui_components import design_frame_times, get_exact_duration, get_video_dimensions, get_video_stream_duration, render_design_html, render_signature_html, render_subtitle_html
from project_io import load_project, get_project_folder_paths, get_reels_in_folder
from workspace_config import WORKSPACE_MODE_CLOUD, get_active_workspace, get_workspace_config
from project_audit import audit_project, format_project_audit_report
from font_registry import STATUS_NONCOMMERCIAL
from job_control import CooperativeJobControl
from canva_connect import upload_asset
from render_range import normalize_render_range, set_render_range

CACHE_FILE = resolve_user_file("sh_v8_project_cache.json", legacy_root=tempfile.gettempdir(), kind="cache")
SUBTITLE_SUPERSAMPLE = subtitle_supersample()
EXPORT_QUEUE_BACKUPS_FILE = resolve_user_file("export_queue_backups.json", legacy_root=os.getcwd(), kind="state")
EXPORT_FORMAT_MP4 = "mp4"
EXPORT_FORMAT_MP4_NO_SUBS = "mp4_no_subs"
EXPORT_FORMAT_CANVA_WEBM = "canva_webm"


def safe_export_queue_name(value, fallback="queue"):
    text = re.sub(r'[\\/:*?"<>|]+', "_", str(value or "").strip())
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return text or fallback


def get_browser_path():
    if os.name == 'nt':
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    else:
        paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def chromium_render_args():
    if os.environ.get("SUBTITLE_FORCE_SOFTWARE_RENDERING", "").strip() == "1":
        return ["--disable-gpu", "--disable-gpu-compositing", "--disable-gpu-rasterization"]
    return ["--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--num-raster-threads=4"]


def launch_render_browser(playwright):
    kwargs = {"headless": True, "args": chromium_render_args()}
    b_path = get_browser_path()
    if b_path:
        kwargs["executable_path"] = b_path
    return playwright.chromium.launch(**kwargs)


def clip_speed_value(clip):
    try:
        value = float((clip or {}).get("speed", 1.0) or 1.0)
    except Exception:
        value = 1.0
    return max(0.05, min(8.0, value))


def atempo_chain(speed):
    speed = max(0.05, min(8.0, float(speed or 1.0)))
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.000")
        speed /= 2.0
    while speed < 0.5:
        parts.append("atempo=0.500")
        speed /= 0.5
    parts.append(f"atempo={speed:.3f}")
    return ",".join(parts)


class ProjectPickCard(QFrame):
    def __init__(self, project_data, checked=False, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}
        self.project_path = self.project_data.get("project_path", "")
        self.init_ui(checked)

    def init_ui(self, checked):
        self.setFixedSize(180, 245)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QFrame { background-color: #1e1e2e; border: 1px solid #313244; border-radius: 10px; }
            QFrame:hover { border: 2px solid #89b4fa; background-color: #242438; }
            QLabel { border: none; }
            QCheckBox { border: none; color: #cdd6f4; font-weight: bold; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        top_row = QHBoxLayout()
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(checked)
        top_row.addStretch()
        top_row.addWidget(self.checkbox)
        layout.addLayout(top_row)

        cover = QLabel()
        cover.setFixedSize(164, 145)
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover.setStyleSheet("background-color: #11111b; color: #6c7086; border-radius: 6px; font-weight: bold;")
        cover_rel = self.project_data.get("cover_img", "")
        project_dir = self.project_data.get("project_dir", "")
        cover_path = os.path.join(project_dir, cover_rel) if project_dir and cover_rel else ""
        if cover_path and os.path.exists(cover_path):
            pixmap = QPixmap(cover_path)
            cover.setPixmap(pixmap.scaled(164, 145, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            cover.setText("无封面")
        layout.addWidget(cover)

        name = QLabel(self.project_data.get("project_name", "未命名 Reel"))
        name.setWordWrap(True)
        name.setStyleSheet("color: #cdd6f4; font-size: 13px; font-weight: bold;")
        layout.addWidget(name)

        date = QLabel(self.project_data.get("updated_at", "").split(" ")[0])
        date.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(date)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)


class ProjectPickerDialog(QDialog):
    def __init__(self, workspace, selected_paths=None, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        os.makedirs(self.workspace, exist_ok=True)
        self.selected = {}
        self.cards = []
        for path in selected_paths or []:
            self.selected[self._key(path)] = path
        self.init_ui()
        self.refresh_folders()

    def init_ui(self):
        self.setWindowTitle("选择批量导出的工程")
        self.resize(980, 680)
        self.setStyleSheet("background-color: #11111b; color: #cdd6f4;")
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("工程大厅选择")
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #cdd6f4;")
        self.lbl_count = QLabel()
        self.lbl_count.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        header.addWidget(title)
        header.addWidget(self.lbl_count)
        header.addStretch()
        main.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background-color: #313244; width: 2px; }")
        self.folder_list = QTreeWidget()
        self.folder_list.setHeaderHidden(True)
        self.folder_list.setStyleSheet("""
            QTreeWidget { background: #181825; border: 1px solid #313244; border-radius: 8px; padding: 6px; outline: none; }
            QTreeWidget::item { padding: 8px; margin: 2px 0; border-radius: 6px; color: #a6adc8; font-weight: bold; }
            QTreeWidget::item:hover { background-color: #242438; color: #cdd6f4; }
            QTreeWidget::item:selected { background-color: #89b4fa; color: #11111b; }
        """)
        self.folder_list.itemClicked.connect(lambda item, column: self.on_folder_selected(item))
        splitter.addWidget(self.folder_list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid_layout.setSpacing(14)
        scroll.setWidget(self.grid_widget)
        splitter.addWidget(scroll)
        splitter.setSizes([220, 760])
        main.addWidget(splitter, stretch=1)

        actions = QHBoxLayout()
        btn_select_folder = QPushButton("选择当前层")
        btn_select_tree = QPushButton("含子文件夹全选")
        btn_clear = QPushButton("清空选择")
        btn_cancel = QPushButton("取消")
        btn_ok = QPushButton("确认选择")
        for btn in [btn_select_folder, btn_select_tree, btn_clear, btn_cancel]:
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 8px 14px; border-radius: 6px;")
        btn_ok.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; padding: 8px 18px; border-radius: 6px;")
        btn_select_folder.clicked.connect(lambda: self.select_current_folder(recursive=False))
        btn_select_tree.clicked.connect(lambda: self.select_current_folder(recursive=True))
        btn_clear.clicked.connect(self.clear_selection)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        actions.addWidget(btn_select_folder)
        actions.addWidget(btn_select_tree)
        actions.addWidget(btn_clear)
        actions.addStretch()
        actions.addWidget(btn_cancel)
        actions.addWidget(btn_ok)
        main.addLayout(actions)
        self.update_count()

    def _key(self, path):
        return os.path.normcase(os.path.abspath(path))

    def _folder_rel_from_item(self, item):
        if not item:
            return ""
        return item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)

    def _folder_path_from_item(self, item):
        rel_path = self._folder_rel_from_item(item)
        return os.path.join(self.workspace, rel_path) if rel_path else ""

    def _add_folder_item(self, rel_path, nodes):
        parent_rel = os.path.dirname(rel_path)
        label = os.path.basename(rel_path)
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
        item.setToolTip(0, rel_path)
        if parent_rel and parent_rel in nodes:
            nodes[parent_rel].addChild(item)
        else:
            self.folder_list.addTopLevelItem(item)
        nodes[rel_path] = item
        return item

    def refresh_folders(self):
        self.folder_list.clear()
        folders = get_project_folder_paths(self.workspace, recursive=True, max_depth=4)
        nodes = {}
        for folder in folders:
            self._add_folder_item(folder, nodes)
        self.folder_list.expandAll()
        if folders:
            first = self.folder_list.topLevelItem(0)
            self.folder_list.setCurrentItem(first)
            self.load_folder(os.path.join(self.workspace, self._folder_rel_from_item(first)))

    def on_folder_selected(self, item):
        if item:
            self.load_folder(self._folder_path_from_item(item))

    def load_folder(self, folder_path):
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        self.cards = []

        paths = get_reels_in_folder(folder_path, recursive=False)
        if not paths:
            child_count = len(get_reels_in_folder(folder_path, recursive=True))
            empty_text = "这个文件夹当前层没有 Reel 工程"
            if child_count:
                empty_text += f"\n子文件夹里有 {child_count} 个工程，可点左侧子文件夹或用「含子文件夹全选」。"
            empty = QLabel(empty_text)
            empty.setStyleSheet("color: #6c7086; font-size: 15px; padding: 20px;")
            self.grid_layout.addWidget(empty, 0, 0)
            return

        row, col, col_count = 0, 0, 4
        for path in paths:
            try:
                project = load_project(path)
            except Exception:
                continue
            card = ProjectPickCard(project, checked=self._key(path) in self.selected)
            card.checkbox.toggled.connect(lambda checked, p=path: self.set_selected(p, checked))
            self.cards.append(card)
            self.grid_layout.addWidget(card, row, col)
            col += 1
            if col >= col_count:
                col = 0
                row += 1
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(self.grid_widget, self._theme_colors)

    def apply_theme(self, colors, theme_key=None):
        self._theme_colors = colors
        self._theme_key = theme_key or ""
        apply_tinted_styles(self, colors)
        apply_room_theme_bridge(self, colors)

    def _polish_deliver_ui(self):
        text_pairs = {
            "btn_new_export_queue": "新增队列",
            "btn_delete_export_queue": "删除队列",
            "btn_save_export_queue": "保存队列",
            "btn_load_export_queue": "调用队列",
            "btn_select_batch_projects": "从工程大厅选择",
            "btn_add_batch_files": "添加工程文件",
            "btn_add_batch_folder": "添加文件夹工程",
            "btn_clear_batch_queue": "清空队列",
            "btn_select_batch_output": "选择批量成品目录",
            "btn_batch_render": "导出当前队列",
            "btn_all_queue_render": "导出全部队列",
            "btn_export_pause": "暂停",
            "btn_export_cancel": "取消",
            "btn_render": "开始导出当前工程",
        }
        for attr, text in text_pairs.items():
            widget = getattr(self, attr, None)
            if widget:
                widget.setText(text)
                widget.setMinimumHeight(36)
        for label_attr in ("lbl_info", "lbl_batch_projects", "lbl_batch_output", "lbl_export_run_state", "lbl_export_queue_total"):
            label = getattr(self, label_attr, None)
            if label:
                label.setWordWrap(True)
        if hasattr(self, "chk_render_range"):
            self.chk_render_range.setText("仅导出范围")
            self.chk_render_range.setMinimumHeight(30)
        if hasattr(self, "spin_duration"):
            self.spin_duration.setMinimumWidth(130)
        if hasattr(self, "spin_render_start"):
            self.spin_render_start.setMinimumWidth(100)
        if hasattr(self, "spin_render_end"):
            self.spin_render_end.setMinimumWidth(100)
        if hasattr(self, "export_queue_tabs"):
            self.export_queue_tabs.setUsesScrollButtons(True)
            self.export_queue_tabs.setElideMode(Qt.TextElideMode.ElideRight)
            self.export_queue_tabs.setMinimumHeight(36)
        if hasattr(self, "log_console"):
            self.log_console.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def set_selected(self, path, checked):
        key = self._key(path)
        if checked:
            self.selected[key] = path
        else:
            self.selected.pop(key, None)
        self.update_count()

    def select_current_folder(self, recursive=False):
        item = self.folder_list.currentItem()
        folder_path = self._folder_path_from_item(item) if item else ""
        paths = get_reels_in_folder(folder_path, recursive=recursive) if folder_path else []
        if recursive:
            for path in paths:
                self.selected[self._key(path)] = path
            for card in self.cards:
                card.checkbox.blockSignals(True)
                card.checkbox.setChecked(self._key(card.project_path) in self.selected)
                card.checkbox.blockSignals(False)
            self.update_count()
            return
        for card in self.cards:
            card.checkbox.setChecked(True)

    def clear_selection(self):
        self.selected.clear()
        for card in self.cards:
            card.checkbox.blockSignals(True)
            card.checkbox.setChecked(False)
            card.checkbox.blockSignals(False)
        self.update_count()

    def update_count(self):
        self.lbl_count.setText(f"已选 {len(self.selected)} 个工程")

    def selected_paths(self):
        return list(self.selected.values())


class DeliverView(QWidget):
    def __init__(self, project_data=None, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}
        self.project_state = {}
        self.render_process = None
        self.temp_dir = ""
        self.concat_path = ""
        self.out_file_path = ""
        self.batch_project_paths = []
        self.batch_output_dir = ""
        self.export_queues = []
        self.current_export_queue_index = 0
        self._switching_export_queue = False
        self.all_queue_rendering = False
        self.all_queue_index = 0
        self.all_queue_plan = []
        self.batch_rendering = False
        self.batch_render_index = 0
        self.current_batch_project_path = ""
        self.active_render_project_data = None
        self.active_render_project_state = None
        self.active_render_design_state = None
        self.active_render_duration = None
        self.active_render_range = None
        self.active_render_format = None
        self.export_render_quality = get_export_render_quality()
        self._cpu_retry_args = []
        self._cpu_retry_attempted = False
        self._render_total_started_at = 0.0
        self._render_html_started_at = 0.0
        self._render_ffmpeg_started_at = 0.0
        self._render_html_elapsed = 0.0
        self._render_ffmpeg_elapsed = 0.0
        self._render_subtitle_frame_count = 0
        self._render_frame_schedule_count = 0
        self._render_encoder_label = ""
        self._render_last_ffmpeg_speed = ""
        self.export_job_control = CooperativeJobControl()
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        left_shell = QFrame()
        left_shell.setStyleSheet("background-color: #181825; border-radius: 12px;")
        left_shell.setMinimumWidth(500)
        left_shell.setMaximumWidth(620)
        left_shell_layout = QVBoxLayout(left_shell)
        left_shell_layout.setContentsMargins(0, 0, 0, 0)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        left_panel = QFrame()
        left_panel.setStyleSheet("background: transparent; border: none;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)
        left_scroll.setWidget(left_panel)
        left_shell_layout.addWidget(left_scroll)
        self.left_shell = left_shell
        self.left_panel = left_panel
        left_layout.addWidget(QLabel("📦 渲染交付设置 (Deliver)", styleSheet="font-size: 18px; font-weight: bold; color: #cdd6f4;"))
        left_layout.addSpacing(20)
        self.lbl_info = QLabel("等待加载工程...")
        self.lbl_info.setStyleSheet("color: #a6e3a1; font-size: 14px; line-height: 1.5;")
        left_layout.addWidget(self.lbl_info)
        left_layout.addSpacing(20)

        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("⏱️ 目标导出时长 (秒):", styleSheet="color: #f9e2af; font-weight: bold;"))
        self.spin_duration = QDoubleSpinBox()
        self.spin_duration.setRange(1.0, 36000.0)
        self.spin_duration.setStyleSheet("background: #313244; color: white; padding: 5px; font-size: 14px; border-radius: 3px;")
        dur_row.addWidget(self.spin_duration)
        left_layout.addLayout(dur_row)
        range_row = QHBoxLayout()
        self.chk_render_range = QCheckBox("ä»…å¯¼å‡ºèŒƒå›´")
        self.chk_render_range.setStyleSheet("color: #cdd6f4; font-weight: bold;")
        self.spin_render_start = QDoubleSpinBox()
        self.spin_render_start.setRange(0.0, 36000.0)
        self.spin_render_start.setSuffix("s")
        self.spin_render_start.setStyleSheet("background: #313244; color: white; padding: 5px; border-radius: 3px;")
        self.spin_render_end = QDoubleSpinBox()
        self.spin_render_end.setRange(0.001, 36000.0)
        self.spin_render_end.setSuffix("s")
        self.spin_render_end.setStyleSheet("background: #313244; color: white; padding: 5px; border-radius: 3px;")
        for widget in (self.chk_render_range, self.spin_render_start, self.spin_render_end):
            range_row.addWidget(widget)
        self.chk_render_range.toggled.connect(self._capture_render_range_controls)
        self.spin_render_start.valueChanged.connect(self._capture_render_range_controls)
        self.spin_render_end.valueChanged.connect(self._capture_render_range_controls)
        left_layout.addLayout(range_row)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("\u5bfc\u51fa\u683c\u5f0f:", styleSheet="color: #a6e3a1; font-weight: bold;"))
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItem("MP4 \u6210\u7247\uff08\u89c6\u9891+\u5b57\u5e55+\u97f3\u9891\uff09", EXPORT_FORMAT_MP4)
        self.export_format_combo.addItem("MP4 \u65e0\u5b57\u5e55\uff08\u89c6\u9891+\u97f3\u9891\uff0c\u53bb\u5176\u4ed6\u8f6f\u4ef6\u52a0\u5b57\u5e55\uff09", EXPORT_FORMAT_MP4_NO_SUBS)
        self.export_format_combo.addItem("Canva \u900f\u660e\u5b57\u5e55 WebM\uff08\u4ec5\u6587\u5b57/\u8bbe\u8ba1\u5c42\uff09", EXPORT_FORMAT_CANVA_WEBM)
        self.export_format_combo.setToolTip("MP4 \u65e0\u5b57\u5e55\u53ea\u5bfc\u51fa\u89c6\u9891/\u97f3\u9891\uff1bWebM \u900f\u660e\u5b57\u5e55\u5c42\u53ef\u62d6\u5165 Canva \u4f5c\u4e3a\u900f\u660e\u89c6\u9891\u7d20\u6750\u53e0\u52a0\u3002")
        self.export_format_combo.setStyleSheet("background: #313244; color: #cdd6f4; padding: 5px; font-size: 13px; border-radius: 3px; font-weight: bold;")
        format_row.addWidget(self.export_format_combo, stretch=1)
        left_layout.addLayout(format_row)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("导出性能:", styleSheet="color: #f9e2af; font-weight: bold;"))
        self.export_quality_combo = QComboBox()
        self.export_quality_combo.addItems(EXPORT_RENDER_QUALITY_OPTIONS)
        self.export_quality_combo.setCurrentText(self.export_render_quality)
        self.export_quality_combo.setToolTip("标准高清保留原截图质量；清晰快速会降低字幕透明层采样压力；极速出片用于长视频/批量先跑通。")
        self.export_quality_combo.setStyleSheet("background: #313244; color: #cdd6f4; padding: 5px; font-size: 13px; border-radius: 3px; font-weight: bold;")
        self.export_quality_combo.currentTextChanged.connect(self.on_export_render_quality_changed)
        quality_row.addWidget(self.export_quality_combo, stretch=1)
        left_layout.addLayout(quality_row)

        left_layout.addWidget(QLabel("✅ 多轨道时间推演 / 混音器 / 画面缩放\n底层核心已全量挂载！", styleSheet="color: #89b4fa; margin-top: 15px;"))
        batch_frame = QFrame()
        batch_frame.setStyleSheet("background-color: #11111b; border: 1px solid #313244; border-radius: 8px; margin-top: 12px;")
        batch_layout = QVBoxLayout(batch_frame)
        batch_layout.setContentsMargins(12, 12, 12, 12)
        batch_layout.setSpacing(8)
        batch_layout.addWidget(QLabel("导出队列", styleSheet="font-size: 15px; font-weight: bold; color: #f9e2af; border: none;"))
        self.export_queue_tabs = QTabBar()
        self.export_queue_tabs.setMovable(False)
        self.export_queue_tabs.setExpanding(False)
        self.export_queue_tabs.setStyleSheet("""
            QTabBar::tab { background: #181825; color: #a6adc8; padding: 7px 10px; margin-right: 4px; border-radius: 6px; font-weight: bold; min-width: 72px; }
            QTabBar::tab:selected { background: #89b4fa; color: #11111b; }
        """)
        self.export_queue_tabs.currentChanged.connect(self.switch_export_queue)
        batch_layout.addWidget(self.export_queue_tabs)
        queue_manage_row = QHBoxLayout()
        queue_manage_row.setSpacing(6)
        self.btn_new_export_queue = QPushButton("新增队列")
        self.btn_delete_export_queue = QPushButton("删除队列")
        self.btn_save_export_queue = QPushButton("保存队列")
        self.btn_load_export_queue = QPushButton("调用队列")
        for btn in (self.btn_new_export_queue, self.btn_delete_export_queue, self.btn_save_export_queue, self.btn_load_export_queue):
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 6px; border-radius: 5px;")
            btn.setMinimumHeight(30)
            queue_manage_row.addWidget(btn)
        self.btn_new_export_queue.clicked.connect(self.add_export_queue)
        self.btn_delete_export_queue.clicked.connect(self.delete_current_export_queue)
        self.btn_save_export_queue.clicked.connect(self.save_current_export_queue_backup)
        self.btn_load_export_queue.clicked.connect(self.load_export_queue_backup)
        batch_layout.addLayout(queue_manage_row)
        self.lbl_export_queue_total = QLabel("总开关: 0 个队列 / 0 个工程")
        self.lbl_export_queue_total.setStyleSheet("color: #a6e3a1; border: none; font-weight: bold;")
        batch_layout.addWidget(self.lbl_export_queue_total)
        self.lbl_batch_projects = QLabel("未选择工程")
        self.lbl_batch_projects.setWordWrap(True)
        self.lbl_batch_projects.setStyleSheet("color: #a6adc8; border: none;")
        self.lbl_batch_output = QLabel("输出目录: 未选择")
        self.lbl_batch_output.setWordWrap(True)
        self.lbl_batch_output.setStyleSheet("color: #a6adc8; border: none;")
        self.btn_select_batch_projects = QPushButton("从工程大厅选择")
        self.btn_select_batch_projects.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_select_batch_projects.setMinimumHeight(32)
        self.btn_select_batch_projects.clicked.connect(self.select_batch_projects)
        self.btn_add_batch_files = QPushButton("添加工程文件")
        self.btn_add_batch_files.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_add_batch_files.setMinimumHeight(32)
        self.btn_add_batch_files.clicked.connect(self.select_batch_project_files)
        self.btn_add_batch_folder = QPushButton("添加文件夹工程")
        self.btn_add_batch_folder.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_add_batch_folder.setMinimumHeight(32)
        self.btn_add_batch_folder.clicked.connect(self.select_batch_project_folder)
        self.btn_clear_batch_queue = QPushButton("清空队列")
        self.btn_clear_batch_queue.setStyleSheet("background-color: #45475a; color: #cdd6f4; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_clear_batch_queue.setMinimumHeight(32)
        self.btn_clear_batch_queue.clicked.connect(self.clear_batch_queue)
        self.btn_select_batch_output = QPushButton("选择批量成品目录")
        self.btn_select_batch_output.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_select_batch_output.setMinimumHeight(32)
        self.btn_select_batch_output.clicked.connect(self.select_batch_output_dir)
        self.btn_batch_render = QPushButton("当前队列导出")
        self.btn_batch_render.setMinimumHeight(36)
        self.btn_batch_render.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 8px; border-radius: 5px;")
        self.btn_batch_render.clicked.connect(self.start_batch_render)
        self.btn_all_queue_render = QPushButton("🚀 全部队列导出")
        self.btn_all_queue_render.setMinimumHeight(38)
        self.btn_all_queue_render.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; padding: 8px; border-radius: 6px;")
        self.btn_all_queue_render.clicked.connect(self.start_all_export_queues)
        batch_layout.addWidget(self.lbl_batch_projects)
        batch_layout.addWidget(self.lbl_batch_output)
        batch_layout.addWidget(self.btn_select_batch_projects)
        queue_btn_row = QHBoxLayout()
        queue_btn_row.setSpacing(6)
        queue_btn_row.addWidget(self.btn_add_batch_files)
        queue_btn_row.addWidget(self.btn_add_batch_folder)
        batch_layout.addLayout(queue_btn_row)
        batch_layout.addWidget(self.btn_clear_batch_queue)
        batch_layout.addWidget(self.btn_select_batch_output)
        batch_layout.addWidget(self.btn_batch_render)
        batch_layout.addWidget(self.btn_all_queue_render)
        export_control_row = QHBoxLayout()
        export_control_row.setSpacing(6)
        self.lbl_export_run_state = QLabel("导出状态：空闲")
        self.lbl_export_run_state.setStyleSheet("color: #a6adc8; border: none; font-weight: bold;")
        self.btn_export_pause = QPushButton("暂停")
        self.btn_export_pause.setToolTip("当前工程导出完成后暂停，不会强行打断正在压制的文件。")
        self.btn_export_cancel = QPushButton("取消")
        self.btn_export_cancel.setToolTip("当前工程导出完成后停止后续队列。")
        self.btn_export_pause.setEnabled(False)
        self.btn_export_cancel.setEnabled(False)
        self.btn_export_pause.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_export_cancel.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; padding: 7px; border-radius: 5px;")
        self.btn_export_pause.clicked.connect(self.toggle_export_pause)
        self.btn_export_cancel.clicked.connect(self.request_export_cancel)
        export_control_row.addWidget(self.lbl_export_run_state, stretch=1)
        export_control_row.addWidget(self.btn_export_pause)
        export_control_row.addWidget(self.btn_export_cancel)
        batch_layout.addLayout(export_control_row)
        left_layout.addWidget(batch_frame)
        left_layout.addStretch()

        self.btn_render = QPushButton("🚀 开始压制导出成片")
        self.btn_render.setFixedHeight(55)
        self.btn_render.setStyleSheet("background-color: #f38ba8; color: #11111b; font-size: 16px; font-weight: bold; border-radius: 8px;")
        self.btn_render.clicked.connect(self.start_render)
        left_layout.addWidget(self.btn_render)
        main_layout.addWidget(left_shell)

        right_panel = QFrame()
        right_panel.setStyleSheet("background-color: #1e1e2e; border-radius: 10px;")
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("📋 压制日志 (Render Log)", styleSheet="font-size: 16px; font-weight: bold; color: #89b4fa;"))
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas; font-size: 13px; border: none; padding: 10px;")
        right_layout.addWidget(self.log_console)
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("QProgressBar { border: 2px solid #313244; border-radius: 5px; text-align: center; color: white; font-weight: bold; } QProgressBar::chunk { background-color: #a6e3a1; }")
        self.progress_bar.setValue(0)
        right_layout.addWidget(self.progress_bar)
        main_layout.addWidget(right_panel, stretch=1)
        self._polish_deliver_ui()
        self._init_default_export_queues()

    def apply_theme(self, colors, theme_key=None):
        self._theme_colors = colors
        self._theme_key = theme_key or ""
        apply_tinted_styles(self, colors)
        apply_room_theme_bridge(self, colors)

    def _polish_deliver_ui(self):
        text_pairs = {
            "btn_new_export_queue": "新增队列",
            "btn_delete_export_queue": "删除队列",
            "btn_save_export_queue": "保存队列",
            "btn_load_export_queue": "调用队列",
            "btn_select_batch_projects": "从工程大厅选择",
            "btn_add_batch_files": "添加工程文件",
            "btn_add_batch_folder": "添加文件夹工程",
            "btn_clear_batch_queue": "清空队列",
            "btn_select_batch_output": "选择批量成品目录",
            "btn_batch_render": "导出当前队列",
            "btn_all_queue_render": "导出全部队列",
            "btn_export_pause": "暂停",
            "btn_export_cancel": "取消",
            "btn_render": "开始导出当前工程",
        }
        for attr, text in text_pairs.items():
            widget = getattr(self, attr, None)
            if widget:
                widget.setText(text)
                widget.setMinimumHeight(36)
        for label_attr in ("lbl_info", "lbl_batch_projects", "lbl_batch_output", "lbl_export_run_state", "lbl_export_queue_total"):
            label = getattr(self, label_attr, None)
            if label:
                label.setWordWrap(True)
        if hasattr(self, "chk_render_range"):
            self.chk_render_range.setText("仅导出范围")
            self.chk_render_range.setMinimumHeight(30)
        if hasattr(self, "spin_duration"):
            self.spin_duration.setMinimumWidth(130)
        if hasattr(self, "spin_render_start"):
            self.spin_render_start.setMinimumWidth(100)
        if hasattr(self, "spin_render_end"):
            self.spin_render_end.setMinimumWidth(100)
        if hasattr(self, "export_queue_tabs"):
            self.export_queue_tabs.setUsesScrollButtons(True)
            self.export_queue_tabs.setElideMode(Qt.TextElideMode.ElideRight)
            self.export_queue_tabs.setMinimumHeight(36)
        if hasattr(self, "log_console"):
            self.log_console.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def _safe_float(self, value, default=0.0):
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return default

    def _safe_render_duration(self, requested=None, state=None, design_state=None):
        state = state if isinstance(state, dict) else (self.project_state if isinstance(self.project_state, dict) else {})
        durations = []

        for clip in state.get("video_clips", []) or []:
            durations.append(self._safe_float(clip.get("end"), 0.0))

        for sub in state.get("subs_data", []) or []:
            durations.append(self._safe_float(sub.get("end"), 0.0))

        design_state = design_state if isinstance(design_state, dict) else (getattr(self, "design_state", {}) or {})
        design_pages = design_state.get("pages", []) if isinstance(design_state, dict) else []
        if isinstance(design_pages, list) and design_pages:
            durations.append(sum(max(0.0, self._safe_float(page.get("duration"), 0.0)) for page in design_pages if isinstance(page, dict)))

        a_path = state.get("audio_path", "")
        if a_path:
            a_trim = state.get("a_trim") or []
            if len(a_trim) >= 2:
                durations.append(max(0.0, self._safe_float(a_trim[1], 0.0)))
            else:
                durations.append(get_exact_duration(a_path))

        music_path = state.get("music_path", "")
        if music_path:
            music_target = self._safe_float(state.get("music_match_duration"), 0.0)
            if music_target <= 0:
                music_target = self._safe_float(state.get("music_dur"), 0.0)
            if music_target <= 0 and os.path.exists(music_path):
                music_target = get_exact_duration(music_path)
            if music_target > 0:
                durations.append(music_target)

        content_dur = max(durations) if durations else 0.0
        guarded_dur = content_dur + render_tail_padding_seconds() if content_dur > 0 else 1.0
        if requested is not None:
            guarded_dur = max(guarded_dur, self._safe_float(requested, 0.0))
        return max(1.0, guarded_dur)

    def _summarize_project_state(self):
        clips = self.project_state.get("video_clips", [])
        a_path = self.project_state.get("audio_path", "")
        music_path = self.project_state.get("music_path", "")
        dur = self.project_state.get("duration", 10.0)
        sub_count = len(self.project_state.get("subs_data", []))
        v_info = f"{len(clips)} 个弹性复合片段" if clips else "未导入"
        a_name = os.path.basename(a_path) if a_path else "未导入"
        music_name = os.path.basename(music_path) if music_path else "未导入"
        info = f"🎥 视频源: {v_info}\n🎵 音频源: {a_name}\n🎼 配乐: {music_name}\n📝 独立字幕片段: {sub_count} 个"
        self.lbl_info.setText(info)
        try:
            dur_value = float(str(dur or 10.0).replace(",", "."))
        except Exception:
            dur_value = 10.0
        self.spin_duration.setValue(self._safe_render_duration(dur_value))
        self._sync_render_range_controls()

    def _current_export_format(self):
        combo = getattr(self, "export_format_combo", None)
        if combo is None:
            return EXPORT_FORMAT_MP4
        value = combo.currentData()
        return value if value in {EXPORT_FORMAT_MP4, EXPORT_FORMAT_MP4_NO_SUBS, EXPORT_FORMAT_CANVA_WEBM} else EXPORT_FORMAT_MP4

    def _render_export_format(self):
        return self.active_render_format or self._current_export_format()

    def _current_export_quality(self):
        combo = getattr(self, "export_quality_combo", None)
        if combo is not None:
            text = combo.currentText()
        else:
            text = getattr(self, "export_render_quality", "")
        if text not in EXPORT_RENDER_QUALITY_OPTIONS:
            text = get_export_render_quality()
        self.export_render_quality = text
        return text

    def on_export_render_quality_changed(self, text):
        self.export_render_quality = set_export_render_quality(text)
        combo = getattr(self, "export_quality_combo", None)
        if combo is not None and combo.currentText() != self.export_render_quality:
            combo.blockSignals(True)
            combo.setCurrentText(self.export_render_quality)
            combo.blockSignals(False)
        self.log_safe(f"导出性能模式: {self.export_render_quality}", "#89b4fa")

    def _export_quality_profile(self):
        return export_render_profile(
            self._current_export_quality(),
            default_scale=SUBTITLE_SUPERSAMPLE,
            default_event_fps=subtitle_event_fps(),
            default_continuous_fps=subtitle_continuous_fps(),
        )

    def _is_canva_transparent_export(self, export_format=None):
        return (export_format or self._render_export_format()) == EXPORT_FORMAT_CANVA_WEBM

    def _is_no_subtitle_export(self, export_format=None):
        return (export_format or self._render_export_format()) == EXPORT_FORMAT_MP4_NO_SUBS

    def _export_output_ext(self, export_format=None):
        return ".webm" if self._is_canva_transparent_export(export_format) else ".mp4"

    def _export_save_filter(self, export_format=None):
        if self._is_canva_transparent_export(export_format):
            return "Canva Transparent WebM (*.webm)"
        return "MP4 Files (*.mp4)"

    def _normalize_export_output_path(self, file_path, export_format=None):
        ext = self._export_output_ext(export_format)
        root, current_ext = os.path.splitext(file_path or "")
        if not root:
            return file_path
        if current_ext.lower() != ext:
            return root + ext
        return file_path

    def _has_overlay_content(self, project_state=None, design_state=None):
        project_state = project_state if isinstance(project_state, dict) else self.project_state
        design_state = design_state if isinstance(design_state, dict) else self.design_state
        if project_state.get("subs_data"):
            return True
        signature = project_state.get("signature", {})
        if isinstance(signature, dict) and signature.get("enabled") and str(signature.get("text", "")).strip():
            return True
        if isinstance(design_state, dict):
            for page in design_state.get("pages", []) or []:
                if isinstance(page, dict) and page.get("layers"):
                    return True
        return False

    def _freeze_render_job(self, project_data=None, project_state=None, design_state=None):
        project_data = project_data if isinstance(project_data, dict) else self.project_data
        project_state = project_state if isinstance(project_state, dict) else self.project_state
        design_state = design_state if isinstance(design_state, dict) else getattr(self, "design_state", {})
        self.active_render_project_data = copy.deepcopy(project_data or {})
        self.active_render_project_state = copy.deepcopy(project_state or {})
        self.active_render_design_state = copy.deepcopy(design_state or {})
        self.active_render_duration = self._safe_render_duration(
            float(self.spin_duration.value()),
            self.active_render_project_state,
            self.active_render_design_state,
        )
        self.active_render_range = normalize_render_range(self.active_render_project_state, self.active_render_duration)
        self.active_render_duration = self.active_render_range["duration"]
        self.active_render_format = self._current_export_format()

    def _clear_render_job(self):
        self.active_render_project_data = None
        self.active_render_project_state = None
        self.active_render_design_state = None
        self.active_render_duration = None
        self.active_render_range = None
        self.active_render_format = None

    def _render_project_state(self):
        if isinstance(self.active_render_project_state, dict):
            return self.active_render_project_state
        return self.project_state if isinstance(self.project_state, dict) else {}

    def _render_design_state(self):
        if isinstance(self.active_render_design_state, dict):
            return self.active_render_design_state
        return getattr(self, "design_state", {}) or {}

    def _render_duration(self, fallback_state=None, fallback_design_state=None):
        if self.active_render_duration is not None:
            return float(self.active_render_duration)
        total_duration = self._safe_render_duration(float(self.spin_duration.value()), fallback_state, fallback_design_state)
        return normalize_render_range(fallback_state or self.project_state, total_duration)["duration"]

    def _render_total_duration(self, fallback_state=None, fallback_design_state=None):
        return self._safe_render_duration(float(self.spin_duration.value()), fallback_state, fallback_design_state)

    def _current_render_range(self, fallback_state=None, fallback_design_state=None):
        if isinstance(self.active_render_range, dict):
            return self.active_render_range
        total_duration = self._render_total_duration(fallback_state, fallback_design_state)
        return normalize_render_range(fallback_state or self.project_state, total_duration)

    def _sync_render_range_controls(self):
        if not hasattr(self, "chk_render_range"):
            return
        total_duration = self._safe_render_duration(self.project_state.get("duration", 1.0), self.project_state, self.design_state)
        render_range = normalize_render_range(self.project_state, total_duration)
        self.chk_render_range.blockSignals(True)
        self.spin_render_start.blockSignals(True)
        self.spin_render_end.blockSignals(True)
        self.chk_render_range.setChecked(render_range["enabled"])
        self.spin_render_start.setMaximum(max(0.0, total_duration))
        self.spin_render_end.setMaximum(max(0.001, total_duration))
        self.spin_render_start.setValue(render_range["start"])
        self.spin_render_end.setValue(render_range["end"])
        self.spin_render_start.setEnabled(render_range["enabled"])
        self.spin_render_end.setEnabled(render_range["enabled"])
        self.chk_render_range.blockSignals(False)
        self.spin_render_start.blockSignals(False)
        self.spin_render_end.blockSignals(False)

    def _capture_render_range_controls(self, *_args):
        if not hasattr(self, "chk_render_range"):
            return
        total_duration = self._safe_render_duration(float(self.spin_duration.value()), self.project_state, self.design_state)
        render_range = set_render_range(
            self.project_state,
            enabled=self.chk_render_range.isChecked(),
            start=self.spin_render_start.value(),
            end=self.spin_render_end.value(),
            total_duration=total_duration,
        )
        self.spin_render_start.setEnabled(render_range["enabled"])
        self.spin_render_end.setEnabled(render_range["enabled"])

    def _project_state_score(self, state):
        if not isinstance(state, dict):
            return 0
        clips = state.get("video_clips", []) or []
        subs = state.get("subs_data", []) or []
        score = len(clips) * 1000 + len(subs)
        if state.get("audio_path"):
            score += 100
        return score

    def _project_candidates(self):
        parent = self.parent()
        candidates = []
        for project in (
            self.project_data,
            getattr(parent, "project", None) if parent else None,
            getattr(getattr(parent, "room_project", None), "project_data", None) if parent else None,
        ):
            if not isinstance(project, dict):
                continue
            state = dict(project.get("room_state", {}).get("edit_room", {}))
            candidates.append((self._project_state_score(state), state, project))

            project_path = project.get("project_path", "")
            if project_path and os.path.exists(project_path):
                try:
                    loaded_project = load_project(project_path)
                    loaded_state = dict(loaded_project.get("room_state", {}).get("edit_room", {}))
                    candidates.append((self._project_state_score(loaded_state), loaded_state, loaded_project))
                except Exception:
                    pass

        if os.path.exists(CACHE_FILE):
            try:
                cached_state = read_json_file(CACHE_FILE, default={})
                if isinstance(cached_state, dict):
                    candidates.append((self._project_state_score(cached_state), cached_state, self.project_data))
            except Exception:
                pass
        return candidates

    def load_project_data(self):
        try:
            candidates = self._project_candidates()
            if candidates:
                _, state, project = max(candidates, key=lambda item: item[0])
                self.project_state = state
                self.design_state = dict(project.get("room_state", {}).get("design_room", {})) if isinstance(project, dict) else {}
                if isinstance(project, dict):
                    self.project_data = project
                    parent = self.parent()
                    if parent is not None and hasattr(parent, "project") and self._project_state_score(state) > 0:
                        parent.project = project
            else:
                self.project_state = {}
                self.design_state = {}
            self._summarize_project_state()
        except Exception:
            self.project_state = {}
            self.design_state = {}
            self.lbl_info.setText("❌ 工程数据读取失败")

    def log_safe(self, msg, color="#cdd6f4"):
        QTimer.singleShot(0, lambda: self._log_msg(msg, color))

    def _log_msg(self, msg, color):
        self.log_console.append(f"<span style='color:{color}'>{msg}</span>")
        self.log_console.verticalScrollBar().setValue(self.log_console.verticalScrollBar().maximum())

    def _reset_render_perf_stats(self):
        self._render_total_started_at = time.monotonic()
        self._render_html_started_at = 0.0
        self._render_ffmpeg_started_at = 0.0
        self._render_html_elapsed = 0.0
        self._render_ffmpeg_elapsed = 0.0
        self._render_subtitle_frame_count = 0
        self._render_frame_schedule_count = 0
        self._render_encoder_label = ""
        self._render_last_ffmpeg_speed = ""

    def _log_render_cost_summary(self, project_state, design_state):
        try:
            summary = summarize_project_render_cost(project_state, design_state)
            level = summary.get("level", "轻")
            subs = summary.get("subtitle_count", 0)
            styles = summary.get("style_count", 0)
            score = summary.get("score", 0)
            color = "#f9e2af" if score >= 4 else "#a6e3a1"
            self.log_safe(f"性能体检: 复杂度 {level} / 字幕 {subs} 条 / 样式 {styles} 组 / 分数 {score}", color)
            for note in summary.get("notes", [])[:5]:
                self.log_safe(f"  - {note}", "#f9e2af")
        except Exception as e:
            self.log_safe(f"性能体检跳过: {e}", "#a6adc8")

    def _log_render_perf_summary(self, exit_code):
        try:
            now = time.monotonic()
            total_started = float(getattr(self, "_render_total_started_at", 0.0) or 0.0)
            ffmpeg_started = float(getattr(self, "_render_ffmpeg_started_at", 0.0) or 0.0)
            if ffmpeg_started:
                self._render_ffmpeg_elapsed = max(0.0, now - ffmpeg_started)
            total_elapsed = max(0.0, now - total_started) if total_started else 0.0
            html_elapsed = max(0.0, float(getattr(self, "_render_html_elapsed", 0.0) or 0.0))
            ffmpeg_elapsed = max(0.0, float(getattr(self, "_render_ffmpeg_elapsed", 0.0) or 0.0))
            duration = max(0.001, float(getattr(self, "active_render_duration", 0.0) or self.spin_duration.value() or 0.001))
            total_speed = duration / total_elapsed if total_elapsed > 0 else 0.0
            ffmpeg_speed = duration / ffmpeg_elapsed if ffmpeg_elapsed > 0 else 0.0
            frame_count = int(getattr(self, "_render_subtitle_frame_count", 0) or 0)
            schedule_count = int(getattr(self, "_render_frame_schedule_count", 0) or 0)
            status = "完成" if exit_code == 0 else f"失败 {exit_code}"
            self.log_safe(
                f"耗时统计({status}): 字幕层 {html_elapsed:.1f}s / FFmpeg {ffmpeg_elapsed:.1f}s / 总计 {total_elapsed:.1f}s / 总速度 {total_speed:.2f}x",
                "#89b4fa" if exit_code == 0 else "#f38ba8",
            )
            if frame_count or schedule_count:
                frame_rate = frame_count / html_elapsed if html_elapsed > 0 else 0.0
                self.log_safe(f"字幕截图: 实际截图 {frame_count} 张 / 时间片 {schedule_count} 段 / 截图吞吐 {frame_rate:.1f} 张/秒", "#89b4fa")
            if ffmpeg_elapsed > 0:
                tail = f"，FFmpeg 报告 speed={self._render_last_ffmpeg_speed}" if getattr(self, "_render_last_ffmpeg_speed", "") else ""
                self.log_safe(f"FFmpeg 合成速度: {ffmpeg_speed:.2f}x{tail}", "#89b4fa")
            if exit_code == 0 and total_elapsed > 0:
                if html_elapsed > max(4.0, ffmpeg_elapsed * 1.2):
                    self.log_safe("慢点判断: 主要慢在字幕层截图。优先切“导出性能=清晰快速/极速出片”；软件会保留观感并自动收敛超大模糊、过厚3D和高亮拖尾。", "#f9e2af")
                elif ffmpeg_elapsed > max(4.0, html_elapsed * 1.2):
                    self.log_safe("慢点判断: 主要慢在 FFmpeg 合成。透明 WebM、素材解码、画面蒙版或电源平衡模式都会影响这里。", "#f9e2af")
                else:
                    self.log_safe("慢点判断: 字幕截图和 FFmpeg 都有耗时，属于综合负载。", "#a6adc8")
        except Exception as e:
            self.log_safe(f"耗时统计失败: {e}", "#a6adc8")

    def _cpu_render_profile(self):
        cpu_count = os.cpu_count() or 4
        return {
            "encoder": "libx264",
            "encoder_label": "CPU x264 安全模式",
            "cpu_threads": max(1, min(16, cpu_count - 1 if cpu_count > 2 else cpu_count)),
        }

    def _retry_render_with_cpu(self):
        if self._cpu_retry_attempted or not self._cpu_retry_args:
            return False
        self._cpu_retry_attempted = True
        self.log_safe("⚠️ 硬件编码失败，已自动切换 CPU x264 安全模式重试一次。", "#f9e2af")
        self._render_encoder_label = "CPU x264 安全模式"
        self._render_ffmpeg_started_at = time.monotonic()
        self._render_last_ffmpeg_speed = ""
        self.render_process = QProcess(self)
        self.render_process.readyReadStandardError.connect(self.on_render_ready_read_error)
        self.render_process.finished.connect(self.on_render_finished)
        self.render_process.start(get_ffmpeg_cmd(), self._cpu_retry_args)
        return True

    def update_progress_safe(self, val):
        QTimer.singleShot(0, lambda: self.progress_bar.setValue(int(val)))

    @property
    def export_pause_requested(self):
        return self.export_job_control.pause_requested

    @export_pause_requested.setter
    def export_pause_requested(self, value):
        self.export_job_control.pause_requested = bool(value)

    @property
    def export_cancel_requested(self):
        return self.export_job_control.cancel_requested

    @export_cancel_requested.setter
    def export_cancel_requested(self, value):
        self.export_job_control.cancel_requested = bool(value)

    @property
    def export_finish_reason(self):
        return self.export_job_control.finish_reason

    @export_finish_reason.setter
    def export_finish_reason(self, value):
        self.export_job_control.finish_reason = str(value or "completed")

    def _set_export_run_controls(self, running=None, state_text=None):
        active = self.batch_rendering if running is None else bool(running)
        if hasattr(self, "btn_export_pause"):
            self.btn_export_pause.setEnabled(active and not self.export_cancel_requested)
            self.btn_export_pause.setText("继续" if self.export_pause_requested else "暂停")
        if hasattr(self, "btn_export_cancel"):
            self.btn_export_cancel.setEnabled(active and not self.export_cancel_requested)
        if hasattr(self, "lbl_export_run_state"):
            if state_text is None:
                if self.export_cancel_requested:
                    state_text = "导出状态：取消请求已收到，当前工程完成后停止"
                elif self.export_pause_requested:
                    state_text = "导出状态：暂停请求已收到，当前工程完成后停住"
                elif active:
                    state_text = "导出状态：导出中"
                else:
                    state_text = "导出状态：空闲"
            self.lbl_export_run_state.setText(state_text)

    def _reset_export_control_flags(self):
        self.export_job_control.reset("export")
        self._set_export_run_controls(True)

    def toggle_export_pause(self):
        if not self.batch_rendering:
            return
        paused = self.export_job_control.toggle_pause()
        if paused:
            self.log_safe("已请求暂停：当前工程导出完成后停住。", "#f9e2af")
        else:
            self.log_safe("已继续导出队列。", "#a6e3a1")
            QTimer.singleShot(0, self._resume_paused_export)
        self._set_export_run_controls(True)

    def request_export_cancel(self):
        if not self.batch_rendering or self.export_cancel_requested:
            return
        waiting_between_projects = not self.current_batch_project_path
        self.export_job_control.request_cancel()
        self.log_safe("已请求取消：当前工程导出完成后停止后续队列。", "#f38ba8")
        self._set_export_run_controls(True)
        if waiting_between_projects:
            QTimer.singleShot(0, self._finish_batch_render_cancelled)

    def _resume_paused_export(self):
        if not self.batch_rendering or self.export_pause_requested or self.export_cancel_requested:
            return
        if self.all_queue_rendering and not self.current_batch_project_path:
            self.batch_rendering = False
            self._start_next_export_queue_from_plan()
        elif not self.current_batch_project_path:
            self._start_next_batch_render()

    def _new_export_queue_state(self, name=None):
        return {
            "name": name or f"队列 {len(self.export_queues) + 1}",
            "paths": [],
            "output_dir": "",
        }

    def _init_default_export_queues(self):
        if not self.export_queues:
            self.export_queues = [self._new_export_queue_state("队列 1")]
            self.current_export_queue_index = 0
        self._refresh_export_queue_tabs()
        self._apply_export_queue_state(self.export_queues[self.current_export_queue_index])

    def _capture_current_export_queue_state(self):
        if self._switching_export_queue or not self.export_queues:
            return
        idx = max(0, min(self.current_export_queue_index, len(self.export_queues) - 1))
        state = self.export_queues[idx]
        state["paths"] = list(self.batch_project_paths)
        state["output_dir"] = self.batch_output_dir

    def _apply_export_queue_state(self, state):
        self._switching_export_queue = True
        self.batch_project_paths = list(state.get("paths", []))
        self.batch_output_dir = state.get("output_dir", "")
        self.lbl_batch_output.setText(f"输出目录: {self.batch_output_dir}" if self.batch_output_dir else "输出目录: 未选择")
        self._refresh_batch_queue_label()
        self._switching_export_queue = False
        self._refresh_export_queue_total()

    def _refresh_export_queue_tabs(self):
        if not hasattr(self, "export_queue_tabs"):
            return
        self.export_queue_tabs.blockSignals(True)
        while self.export_queue_tabs.count():
            self.export_queue_tabs.removeTab(0)
        for idx, state in enumerate(self.export_queues):
            total = len(state.get("paths", []))
            self.export_queue_tabs.addTab(f"{state.get('name', f'队列 {idx + 1}')} ({total})")
        if self.export_queues:
            self.export_queue_tabs.setCurrentIndex(max(0, min(self.current_export_queue_index, len(self.export_queues) - 1)))
        self.export_queue_tabs.blockSignals(False)
        self._refresh_export_queue_total()

    def _refresh_export_queue_total(self):
        if not hasattr(self, "lbl_export_queue_total"):
            return
        self._capture_current_export_queue_state()
        active_queues = [q for q in self.export_queues if q.get("paths")]
        total_projects = sum(len(q.get("paths", [])) for q in active_queues)
        self.lbl_export_queue_total.setText(f"总开关: {len(active_queues)} 个队列 / {total_projects} 个工程")

    def switch_export_queue(self, index):
        if self._switching_export_queue or index < 0 or index >= len(self.export_queues):
            return
        if self.batch_rendering:
            return
        self._capture_current_export_queue_state()
        self.current_export_queue_index = index
        self._apply_export_queue_state(self.export_queues[index])

    def add_export_queue(self):
        if self.batch_rendering:
            return
        self._capture_current_export_queue_state()
        self.export_queues.append(self._new_export_queue_state())
        self.current_export_queue_index = len(self.export_queues) - 1
        self._refresh_export_queue_tabs()
        self._apply_export_queue_state(self.export_queues[self.current_export_queue_index])

    def delete_current_export_queue(self):
        if self.batch_rendering:
            return QMessageBox.warning(self, "正在导出", "导出进行中不能删除队列。")
        if len(self.export_queues) <= 1:
            self.export_queues[0] = self._new_export_queue_state("队列 1")
            self.current_export_queue_index = 0
        else:
            self.export_queues.pop(self.current_export_queue_index)
            self.current_export_queue_index = max(0, min(self.current_export_queue_index, len(self.export_queues) - 1))
        self._refresh_export_queue_tabs()
        self._apply_export_queue_state(self.export_queues[self.current_export_queue_index])

    def _load_export_queue_backups(self):
        data = read_json_file(EXPORT_QUEUE_BACKUPS_FILE, default=[])
        return data if isinstance(data, list) else []

    def _save_export_queue_backups(self, backups):
        write_json_file(EXPORT_QUEUE_BACKUPS_FILE, backups, indent=2)

    def save_current_export_queue_backup(self):
        self._capture_current_export_queue_state()
        if not self.export_queues:
            return
        name, ok = QInputDialog.getText(self, "保存导出队列", "备份名称:", text=self.export_queues[self.current_export_queue_index].get("name", "导出队列"))
        if not ok or not name.strip():
            return
        backups = self._load_export_queue_backups()
        state = copy.deepcopy(self.export_queues[self.current_export_queue_index])
        state["backup_name"] = name.strip()
        backups = [b for b in backups if b.get("backup_name") != name.strip()]
        backups.append(state)
        self._save_export_queue_backups(backups)
        self.log_safe(f"已保存导出队列备份：{name.strip()}", "#a6e3a1")

    def load_export_queue_backup(self):
        backups = self._load_export_queue_backups()
        if not backups:
            return QMessageBox.information(self, "没有备份", "还没有保存过导出队列。")
        labels = [b.get("backup_name") or b.get("name") or f"备份 {i + 1}" for i, b in enumerate(backups)]
        choice, ok = QInputDialog.getItem(self, "调用导出队列", "选择备份:", labels, 0, False)
        if not ok:
            return
        self._capture_current_export_queue_state()
        state = copy.deepcopy(backups[labels.index(choice)])
        state["name"] = state.get("backup_name") or state.get("name") or f"队列 {len(self.export_queues) + 1}"
        state["paths"] = self._normalize_batch_project_paths(state.get("paths", []))
        state["output_dir"] = state.get("output_dir", "")
        self.export_queues.append(state)
        self.current_export_queue_index = len(self.export_queues) - 1
        self._refresh_export_queue_tabs()
        self._apply_export_queue_state(state)

    def _runnable_export_queue_plan(self):
        self._capture_current_export_queue_state()
        plan = []
        for idx, state in enumerate(self.export_queues):
            paths = self._normalize_batch_project_paths(state.get("paths", []))
            output_dir = state.get("output_dir", "")
            if paths:
                plan.append({"index": idx, "name": state.get("name", f"队列 {idx + 1}"), "paths": paths, "output_dir": output_dir})
        return plan

    def start_all_export_queues(self):
        if self.batch_rendering:
            return
        plan = self._runnable_export_queue_plan()
        if not plan:
            return QMessageBox.warning(self, "队列为空", "请先在至少一个导出队列里添加工程。")
        missing_output = [item["name"] for item in plan if not item.get("output_dir")]
        if missing_output:
            return QMessageBox.warning(self, "缺少输出目录", "以下队列还没有选择输出目录：\n" + "\n".join(missing_output[:8]))
        self.all_queue_rendering = True
        self.all_queue_index = 0
        self.all_queue_plan = plan
        self._reset_export_control_flags()
        self.log_console.clear()
        self.progress_bar.setValue(0)
        total_projects = sum(len(item["paths"]) for item in plan)
        self.log_safe(f"总开关启动：{len(plan)} 个队列 / {total_projects} 个工程。", "#a6e3a1")
        self._start_next_export_queue_from_plan()

    def _start_next_export_queue_from_plan(self):
        if self.export_cancel_requested:
            self._finish_batch_render_cancelled()
            return
        if self.export_pause_requested:
            self.batch_rendering = True
            self.current_batch_project_path = ""
            self._set_export_run_controls(True)
            self.log_safe("导出已暂停，点击“继续”后进入下一个队列。", "#f9e2af")
            return
        if self.all_queue_index >= len(self.all_queue_plan):
            self.all_queue_rendering = False
            self.all_queue_plan = []
            self.set_batch_queue_controls_enabled(True)
            self.btn_render.setEnabled(True)
            self.btn_batch_render.setEnabled(True)
            self._set_export_run_controls(False)
            self.progress_bar.setValue(100)
            self._refresh_export_queue_tabs()
            self.log_safe("全部导出队列已完成。", "#a6e3a1")
            QMessageBox.information(self, "全部队列完成", "所有导出队列已经处理完成。")
            return
        item = self.all_queue_plan[self.all_queue_index]
        self.current_export_queue_index = item["index"]
        self._refresh_export_queue_tabs()
        self.batch_project_paths = list(item["paths"])
        self.batch_output_dir = item["output_dir"]
        self.lbl_batch_output.setText(f"输出目录: {self.batch_output_dir}")
        self._refresh_batch_queue_label()
        self.log_safe(f"开始队列 {self.all_queue_index + 1}/{len(self.all_queue_plan)}：{item['name']}（{len(item['paths'])} 个工程）", "#f9e2af")
        self.start_batch_render(clear_log=False)

    def set_batch_queue_controls_enabled(self, enabled):
        for name in (
            "btn_select_batch_projects",
            "btn_add_batch_files",
            "btn_add_batch_folder",
            "btn_clear_batch_queue",
            "btn_select_batch_output",
            "btn_new_export_queue",
            "btn_delete_export_queue",
            "btn_save_export_queue",
            "btn_load_export_queue",
            "btn_all_queue_render",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(bool(enabled))

    def current_workspace(self):
        parent = self.parent()
        while parent is not None and not hasattr(parent, "room_project"):
            parent = parent.parent()
        room_project = getattr(parent, "room_project", None) if parent else None
        if room_project and getattr(room_project, "workspace", ""):
            return room_project.workspace
        return get_active_workspace()

    def select_batch_projects(self):
        workspace = self.current_workspace()
        if not workspace or not os.path.isdir(workspace):
            return QMessageBox.warning(self, "提示", "当前工作区不可用，请先在工程大厅选择本地或云端工作区。")
        dialog = ProjectPickerDialog(workspace, self.batch_project_paths, self)
        if hasattr(self, "_theme_colors"):
            dialog.apply_theme(self._theme_colors, getattr(self, "_theme_key", ""))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = get_workspace_config()
        source_label = "云端工程大厅" if cfg.get("mode") == WORKSPACE_MODE_CLOUD else "工程大厅"
        self.set_batch_projects(dialog.selected_paths(), source_label=source_label, append=True)

    def _normalize_batch_project_paths(self, paths):
        valid_paths = []
        seen = set()
        for path in paths or []:
            if not path or not os.path.exists(path) or not path.lower().endswith(".scomp"):
                continue
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            valid_paths.append(os.path.abspath(path))
        return valid_paths

    def _refresh_batch_queue_label(self, source_label="", active_index=None):
        total = len(self.batch_project_paths)
        if total:
            folders = len({os.path.normcase(os.path.dirname(path)) for path in self.batch_project_paths})
            label = f"队列 {total} 个工程 · {folders} 个文件夹"
            if active_index is not None and 0 <= active_index < total:
                label += f" · 当前 {active_index + 1}/{total}"
            if source_label:
                label += f" · 新增: {source_label}"
            self.lbl_batch_projects.setText(label)
        else:
            self.lbl_batch_projects.setText("队列为空")

    def set_batch_projects(self, paths, source_label="", output_dir="", append=False):
        incoming = self._normalize_batch_project_paths(paths)
        valid_paths = list(self.batch_project_paths) if append else []
        seen = {os.path.normcase(os.path.abspath(path)) for path in valid_paths}
        added_count = 0
        for path in incoming:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            valid_paths.append(path)
            added_count += 1

        self.batch_project_paths = valid_paths
        if valid_paths:
            self._refresh_batch_queue_label(source_label)
            if output_dir:
                self.batch_output_dir = output_dir
                self.lbl_batch_output.setText(f"输出目录: {output_dir}")
            if append:
                self.log_safe(f"导出队列新增 {added_count} 个工程，当前共 {len(valid_paths)} 个。", "#89b4fa")
            else:
                self.log_safe(f"已设置 {len(valid_paths)} 个批量导出工程。", "#89b4fa")
        else:
            self._refresh_batch_queue_label()
        self._capture_current_export_queue_state()
        self._refresh_export_queue_tabs()

    def set_export_queues_from_batch_groups(self, groups, source_label="", default_output_dir="", append=False):
        normalized = []
        source_groups = [group for group in (groups or []) if isinstance(group, dict)]
        multi_group = len(source_groups) > 1
        for idx, group in enumerate(source_groups, start=1):
            paths = self._normalize_batch_project_paths(group.get("paths", []))
            if not paths:
                continue
            name = str(group.get("name") or f"队列 {idx}")
            output_dir = str(group.get("output_dir") or "").strip()
            if not output_dir and default_output_dir:
                output_dir = os.path.join(default_output_dir, safe_export_queue_name(name)) if multi_group else default_output_dir
            normalized.append({
                "name": name,
                "paths": paths,
                "output_dir": output_dir,
            })
        if not normalized:
            return self.set_batch_projects([], source_label=source_label, output_dir=default_output_dir, append=append)
        self._capture_current_export_queue_state()
        if append:
            self.export_queues.extend(normalized)
            self.current_export_queue_index = len(self.export_queues) - len(normalized)
        else:
            self.export_queues = normalized
            self.current_export_queue_index = 0
        self._refresh_export_queue_tabs()
        self._apply_export_queue_state(self.export_queues[self.current_export_queue_index])
        total = sum(len(item.get("paths", [])) for item in normalized)
        label = source_label or "批量建工程"
        self.log_safe(f"已同步 {len(normalized)} 个导出队列 / {total} 个工程（来源：{label}）。", "#89b4fa")

    def select_batch_project_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加工程文件到导出队列", "", "Subtitle Composer Projects (*.scomp)")
        if paths:
            self.set_batch_projects(paths, source_label="工程文件", append=True)

    def select_batch_project_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "添加文件夹里的工程到导出队列")
        if not folder:
            return
        paths = get_reels_in_folder(folder, recursive=True)
        if not paths:
            return QMessageBox.information(self, "没有工程", "这个文件夹里没有找到 .scomp 工程文件。")
        paths = sorted(paths, key=lambda path: os.path.basename(path).lower())
        self.set_batch_projects(paths, source_label=os.path.basename(folder) or folder, append=True)

    def clear_batch_queue(self):
        if self.batch_rendering:
            return QMessageBox.warning(self, "正在导出", "导出进行中不能清空队列。")
        self.batch_project_paths = []
        self.batch_render_index = 0
        self._refresh_batch_queue_label()
        self._capture_current_export_queue_state()
        self._refresh_export_queue_tabs()
        self.log_safe("导出队列已清空。", "#a6adc8")

    def select_batch_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择批量成品输出目录")
        if d:
            self.batch_output_dir = d
            self.lbl_batch_output.setText(f"输出目录: {d}")
            self._capture_current_export_queue_state()

    def start_batch_render(self, clear_log=True):
        if self.batch_rendering:
            return
        self._capture_current_export_queue_state()
        if not self.batch_project_paths:
            return QMessageBox.warning(self, "提示", "请先从工程大厅选择要批量导出的工程。")
        if not self.batch_output_dir:
            return QMessageBox.warning(self, "提示", "请先选择批量成品输出目录。")
        os.makedirs(self.batch_output_dir, exist_ok=True)
        self.batch_rendering = True
        if not self.all_queue_rendering:
            self._reset_export_control_flags()
        self.batch_render_index = 0
        self.btn_render.setEnabled(False)
        self.btn_batch_render.setEnabled(False)
        self.set_batch_queue_controls_enabled(False)
        if clear_log:
            self.log_console.clear()
        self.progress_bar.setValue(0)
        self._refresh_batch_queue_label(active_index=0)
        self.log_safe(f"批量渲染启动，共 {len(self.batch_project_paths)} 个工程。", "#a6e3a1")
        self._start_next_batch_render()

    def _start_next_batch_render(self):
        if self.export_cancel_requested:
            self._finish_batch_render_cancelled()
            return
        if self.export_pause_requested:
            self.current_batch_project_path = ""
            self._clear_render_job()
            self._set_export_run_controls(True)
            self.log_safe("导出已暂停，点击“继续”后从下一条工程恢复。", "#f9e2af")
            return
        if self.batch_render_index >= len(self.batch_project_paths):
            self.batch_rendering = False
            self.current_batch_project_path = ""
            self._clear_render_job()
            if self.all_queue_rendering:
                queue_name = self.all_queue_plan[self.all_queue_index]["name"] if self.all_queue_index < len(self.all_queue_plan) else "队列"
                self.log_safe(f"队列完成：{queue_name}", "#a6e3a1")
                self.all_queue_index += 1
                QTimer.singleShot(0, self._start_next_export_queue_from_plan)
                return
            self.btn_render.setEnabled(True)
            self.btn_batch_render.setEnabled(True)
            self.set_batch_queue_controls_enabled(True)
            self._set_export_run_controls(False)
            self.progress_bar.setValue(100)
            self._refresh_batch_queue_label()
            self.log_safe("批量渲染全部完成。", "#a6e3a1")
            QMessageBox.information(self, "批量渲染完成", f"已处理 {len(self.batch_project_paths)} 个工程。\n输出目录:\n{self.batch_output_dir}")
            return

        project_path = self.batch_project_paths[self.batch_render_index]
        self._refresh_batch_queue_label(active_index=self.batch_render_index)
        try:
            project = load_project(project_path)
            self.project_data = project
            self.project_state = dict(project.get("room_state", {}).get("edit_room", {}))
            self.design_state = dict(project.get("room_state", {}).get("design_room", {}))
            self.log_safe(
                f"📦 读取工程: 字幕 {len(self.project_state.get('subs_data', []) or [])} / 视频 {len(self.project_state.get('video_clips', []) or [])}",
                "#89b4fa",
            )
            export_format = self._current_export_format()
            transparent_export = self._is_canva_transparent_export(export_format)
            no_subtitle_export = self._is_no_subtitle_export(export_format)
            if transparent_export:
                if not self._has_overlay_content(self.project_state, self.design_state):
                    self.log_safe(f"跳过工程: {os.path.basename(project_path)} | 没有字幕/署名/设计层可导出", "#f38ba8")
                    self.batch_render_index += 1
                    QTimer.singleShot(0, self._start_next_batch_render)
                    return
            elif no_subtitle_export:
                if not self.project_state.get("video_clips"):
                    self.log_safe(f"跳过工程: {os.path.basename(project_path)} | 缺少视频数据", "#f38ba8")
                    self.batch_render_index += 1
                    QTimer.singleShot(0, self._start_next_batch_render)
                    return
            elif not self.project_state.get("video_clips") or not self.project_state.get("subs_data"):
                self.log_safe(f"跳过工程: {os.path.basename(project_path)} | 缺少视频或字幕数据", "#f38ba8")
                self.batch_render_index += 1
                QTimer.singleShot(0, self._start_next_batch_render)
                return
            batch_audit = audit_project(project, workspace=self.current_workspace())
            if not no_subtitle_export and any(row.get("status") == STATUS_NONCOMMERCIAL for row in batch_audit.get("fonts", {}).get("fonts", [])):
                self.log_safe(f"跳过工程: {os.path.basename(project_path)} | 含非商用/禁止商用字体", "#f38ba8")
                self.batch_render_index += 1
                QTimer.singleShot(0, self._start_next_batch_render)
                return
            self.current_batch_project_path = project_path
            self._summarize_project_state()
            self.out_file_path = self._unique_batch_output_path(project)
            self._freeze_render_job(project, self.project_state, self.design_state)
            self._reset_render_perf_stats()
            self.progress_bar.setValue(0)
            self.log_safe(f"[{self.batch_render_index + 1}/{len(self.batch_project_paths)}] 开始渲染: {project.get('project_name', os.path.basename(project_path))}", "#f9e2af")
            self.log_safe(f"输出: {self.out_file_path}", "#89b4fa")
            threading.Thread(target=self.generate_html_frames, daemon=True).start()
        except Exception as e:
            self.log_safe(f"跳过工程: {os.path.basename(project_path)} | {e}", "#f38ba8")
            self._clear_render_job()
            self.batch_render_index += 1
            QTimer.singleShot(0, self._start_next_batch_render)

    def _finish_batch_render_cancelled(self):
        self.batch_rendering = False
        self.all_queue_rendering = False
        self.all_queue_plan = []
        self.current_batch_project_path = ""
        self._clear_render_job()
        self.btn_render.setEnabled(True)
        self.btn_batch_render.setEnabled(True)
        self.set_batch_queue_controls_enabled(True)
        self.export_job_control.clear_requests()
        self._set_export_run_controls(False, "导出状态：已取消")
        self._refresh_batch_queue_label()
        self._refresh_export_queue_tabs()
        self.log_safe("导出队列已取消，后续工程没有继续启动。", "#f38ba8")
        QMessageBox.information(self, "导出已取消", "当前工程已收尾，后续导出队列已停止。")

    def _unique_batch_output_path(self, project):
        raw_name = project.get("project_name") or os.path.splitext(os.path.basename(project.get("project_path", "output")))[0]
        safe_name = "".join(c for c in raw_name if c not in r'\/:*?"<>|').strip() or "output"
        export_format = self._current_export_format()
        ext = self._export_output_ext(export_format)
        if self._is_canva_transparent_export(export_format):
            suffix = "_canva_alpha"
        elif self._is_no_subtitle_export(export_format):
            suffix = "_no_subtitles"
        else:
            suffix = ""
        candidate = os.path.join(self.batch_output_dir, f"{safe_name}{suffix}{ext}")
        n = 2
        while os.path.exists(candidate):
            candidate = os.path.join(self.batch_output_dir, f"{safe_name}{suffix}-{n}{ext}")
            n += 1
        return candidate

    def _handle_render_stage_failed(self):
        if self.batch_rendering:
            try:
                if self.temp_dir:
                    shutil.rmtree(self.temp_dir)
            except Exception:
                pass
            self._clear_render_job()
            self.batch_render_index += 1
            QTimer.singleShot(0, self._start_next_batch_render)
        else:
            self._clear_render_job()
            self.btn_render.setEnabled(True)

    def start_render(self):
        self.batch_rendering = False
        self.load_project_data()
        subs = self.project_state.get("subs_data", [])
        clips = self.project_state.get("video_clips", [])
        a_path = self.project_state.get("audio_path", "")

        self.log_console.clear()
        self.progress_bar.setValue(0)
        self.log_safe(f"📊 字幕数: {len(subs)}", "#89b4fa")
        self.log_safe(f"📊 视频数: {len(clips)}", "#89b4fa")
        self.log_safe(f"📊 音频路径: {a_path or '未提供'}", "#89b4fa")

        export_format = self._current_export_format()
        transparent_export = self._is_canva_transparent_export(export_format)
        no_subtitle_export = self._is_no_subtitle_export(export_format)
        has_overlay = self._has_overlay_content(self.project_state, self.design_state)
        current_missing = (not has_overlay) if transparent_export else ((not clips) if no_subtitle_export else (not clips or not subs))

        if current_missing and self.batch_project_paths and self.batch_output_dir:
            self.log_safe("⚠️ 当前工程数据为空，已自动切换到已选择的批量导出队列。", "#f9e2af")
            return self.start_batch_render()

        if transparent_export:
            if not has_overlay:
                return QMessageBox.warning(self, "提示", "当前工程没有可导出的字幕/署名/设计层。")
            self.log_safe("🎨 Canva 透明 WebM 模式：只导出文字/署名/设计层，不合成底色视频和音频。", "#a6e3a1")
        else:
            if not clips:
                return QMessageBox.warning(self, "提示", "请先在 Edit 房间导入至少一个视频片段并保存工程！")
            if no_subtitle_export:
                self.log_safe("🎞️ MP4 无字幕模式：只合成视频和音频，不烧录字幕层。", "#a6e3a1")
            elif not subs:
                return QMessageBox.warning(self, "提示", "当前工程没有字幕数据。请先在 Edit 房间生成字幕并点“保存工程”。")
            if not a_path:
                self.log_safe("⚠️ 未检测到独立音频，将尝试使用视频原声；若原视频也无音轨，则输出静音视频。", "#f9e2af")

        try:
            audit_source = dict(self.project_data or {})
            audit_source.setdefault("room_state", {})["edit_room"] = self.project_state
            preflight = audit_project(audit_source, workspace=self.current_workspace())
            if preflight.get("warnings"):
                has_noncommercial_fonts = any(
                    row.get("status") == STATUS_NONCOMMERCIAL
                    for row in preflight.get("fonts", {}).get("fonts", [])
                )
                self.log_safe("⚠️ 导出前体检发现需要复核的素材或字体。", "#f9e2af")
                detail = format_project_audit_report(preflight, workspace=self.current_workspace())
                reply = QMessageBox.warning(
                    self,
                    "导出前体检提醒",
                    detail + "\n\n仍然继续导出吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No if ((preflight.get("missing_media") and not transparent_export) or (has_noncommercial_fonts and not no_subtitle_export)) else QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception as e:
            self.log_safe(f"⚠️ 导出前体检跳过: {e}", "#f9e2af")

        file_path, _ = QFileDialog.getSaveFileName(self, "导出最终视频", "", self._export_save_filter(export_format))
        if not file_path:
            return
        self.out_file_path = self._normalize_export_output_path(file_path, export_format)
        self._freeze_render_job(self.project_data, self.project_state, self.design_state)
        self._reset_render_perf_stats()
        self.btn_render.setEnabled(False)
        self.log_safe("🚀 [阶段 1/2] 启动全局时间推演引擎 (多轨道同频渲染)...", "#f9e2af")
        threading.Thread(target=self.generate_html_frames, daemon=True).start()

    def generate_html_frames(self):
        try:
            project_state = copy.deepcopy(self._render_project_state())
            design_state = copy.deepcopy(self._render_design_state())
            if not getattr(self, "_render_total_started_at", 0.0):
                self._reset_render_perf_stats()
            self._render_html_started_at = time.monotonic()
            self.temp_dir = tempfile.mkdtemp(prefix="subtitle_render_")
            self.concat_path = os.path.join(self.temp_dir, "subs_concat.txt").replace("\\", "/")
            blank_path = os.path.join(self.temp_dir, "blank.png").replace("\\", "/")
            subs_data = project_state.get("subs_data", [])
            signature = project_state.get("signature", {})
            total_dur = self._render_duration(project_state, design_state)
            render_range = self._current_render_range(project_state, design_state)
            render_start = float(render_range.get("start", 0.0) or 0.0)
            render_end = render_start + total_dur
            self._log_render_cost_summary(project_state, design_state)
            if self._is_no_subtitle_export():
                self.log_safe("⏭️ MP4 无字幕模式：跳过字幕层截图，直接进入视频/音频压制。", "#a6e3a1")
                self.update_progress_safe(50)
                QTimer.singleShot(0, self.start_ffmpeg_qprocess)
                return

            clips = project_state.get("video_clips", [])
            res_text = project_state.get("resolution") or get_output_resolution()
            media_path = clips[0]["path"] if clips else ""
            proj_w, proj_h = resolution_to_size(res_text, media_path, get_video_dimensions)

            quality_profile = self._export_quality_profile()
            quality_mode = str(quality_profile.get("mode") or self._current_export_quality())
            signature_render = simplify_signature_for_export(signature, quality_mode)
            render_subs_data = [
                simplify_subtitle_for_export(sub, quality_mode) if isinstance(sub, dict) else sub
                for sub in subs_data
            ]
            render_scale = float(quality_profile.get("render_scale", SUBTITLE_SUPERSAMPLE) or SUBTITLE_SUPERSAMPLE)
            event_fps = int(quality_profile.get("event_fps", subtitle_event_fps()) or subtitle_event_fps())
            continuous_fps = int(quality_profile.get("continuous_fps", subtitle_continuous_fps()) or subtitle_continuous_fps())
            if quality_mode != "标准高清":
                self.log_safe("保真提速: 保留字体/颜色/位置，自动收敛超大模糊、过厚3D和高亮拖尾。", "#a6e3a1")

            with sync_playwright() as p:
                browser = launch_render_browser(p)
                render_w = max(1, int(proj_w * render_scale))
                render_h = max(1, int(proj_h * render_scale))
                page = browser.new_page(viewport={"width": render_w, "height": render_h}, device_scale_factor=1)
                bundled_font_css = font_face_css()
                shell_html = f"""<!DOCTYPE html>
                <html>
                <head>
                    <style>
                        {bundled_font_css}
                        html, body {{
                            margin: 0; padding: 0; width: 100vw; height: 100vh; overflow: hidden;
                            background: transparent; display: flex; justify-content: center; align-items: center;
                            -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
                            -webkit-font-smoothing: antialiased;
                            -moz-osx-font-smoothing: grayscale;
                            text-rendering: optimizeLegibility;
                        }}
                        #scale-wrapper {{
                            width: 100vw; height: 100vh; position: absolute; left: 0; top: 0;
                            transform-origin: center center;
                        }}
                    </style>
                </head>
                <body>
                    <div id="scale-wrapper"></div>
                </body>
                </html>"""
                page.set_content(shell_html)
                page.evaluate("() => document.fonts && document.fonts.ready ? document.fonts.ready.then(() => true) : true")
                page.screenshot(path=blank_path, omit_background=True, scale="css")

                with open(self.concat_path, "w", encoding="utf-8") as f_concat:
                    frame_idx = 0
                    reused_frame_count = 0
                    last_html_subs = None
                    last_frame_path = ""
                    last_concat_file = blank_path
                    extra_styles = []
                    if isinstance(signature_render, dict) and signature_render.get("enabled") and str(signature_render.get("text", "")).strip():
                        extra_styles.append(signature_render.get("style", {}))
                    frame_schedule = build_subtitle_frame_schedule(
                        render_subs_data,
                        render_end,
                        extra_styles=extra_styles,
                        extra_times=design_frame_times(design_state),
                        event_fps=event_fps,
                        continuous_fps=continuous_fps,
                    )
                    frame_schedule = [
                        (max(current_time, render_start), min(current_time + duration, render_end) - max(current_time, render_start))
                        for current_time, duration in frame_schedule
                        if min(current_time + duration, render_end) > max(current_time, render_start)
                    ]
                    if not frame_schedule:
                        frame_schedule = [(render_start, total_dur)]
                    self._render_frame_schedule_count = len(frame_schedule)
                    self.log_safe(
                        f"⚡ 字幕渲染采样: {len(frame_schedule)} 段，{quality_profile.get('summary', '')}",
                        "#89b4fa",
                    )

                    def write_subtitle_frame(path, duration):
                        nonlocal last_concat_file
                        duration = max(0.001, float(duration or 0.0))
                        f_concat.write(ffconcat_file_entry(path, duration))
                        last_concat_file = path

                    for current_time, frame_duration in frame_schedule:
                        active_subs = active_subtitles_for_frame(render_subs_data, current_time, frame_duration)
                        design_html = render_design_html(design_state, current_time, proj_w, proj_h)
                        signature_html = render_signature_html(signature_render, current_time, proj_w, proj_h)
                        if not active_subs and not signature_html and not design_html:
                            write_subtitle_frame(blank_path, frame_duration)
                            self.update_progress_safe(int((((current_time + frame_duration) - render_start) / total_dur) * 50))
                            continue

                        html_subs = design_html + signature_html
                        for s, sub_time in active_subs:
                            px = s.get("pos_x", 0.0)
                            py = s.get("pos_y", 25.0)
                            trk = s.get("track", 1)
                            z_idx = 10 if trk == 0 else 5
                            base_css = f"position: absolute; left: calc(50% + {px}%); top: calc(50% + {py}%); transform: translate(-50%, -50%); z-index: {z_idx}; width: max-content; max-width: 92%;"
                            sub_html = render_subtitle_html(s, sub_time, proj_w, proj_h)
                            html_subs += f"<div style='{base_css}'>{sub_html}</div>\n"

                        if last_frame_path and html_subs == last_html_subs:
                            write_subtitle_frame(last_frame_path, frame_duration)
                            reused_frame_count += 1
                            self.update_progress_safe(int((((current_time + frame_duration) - render_start) / total_dur) * 50))
                            continue

                        page.evaluate(
                            "(html) => { const wrapper = document.getElementById('scale-wrapper'); if (wrapper) wrapper.innerHTML = html; }",
                            html_subs,
                        )
                        frame_path = os.path.join(self.temp_dir, f"f_{frame_idx}.png").replace("\\", "/")
                        page.screenshot(path=frame_path, omit_background=True, scale="css")
                        write_subtitle_frame(frame_path, frame_duration)
                        last_html_subs = html_subs
                        last_frame_path = frame_path
                        frame_idx += 1
                        self.update_progress_safe(int((((current_time + frame_duration) - render_start) / total_dur) * 50))

                    f_concat.write(ffconcat_file_entry(last_concat_file))

                browser.close()
            self._render_html_elapsed = max(0.0, time.monotonic() - self._render_html_started_at)
            self._render_subtitle_frame_count = frame_idx
            rate = frame_idx / self._render_html_elapsed if self._render_html_elapsed > 0 else 0.0
            reuse_note = f" / 复用 {reused_frame_count} 段" if reused_frame_count else ""
            self.log_safe(f"字幕层截图完成: {self._render_html_elapsed:.1f}s / 实际截图 {frame_idx} 张{reuse_note} / {rate:.1f} 张每秒", "#89b4fa")
            self.log_safe("✅ 多轨道推演截图完毕！准备混音与剪辑...", "#a6e3a1")
            QTimer.singleShot(0, self.start_ffmpeg_qprocess)
        except Exception as e:
            self.log_safe(f"❌ 绘制失败: {str(e)}", "#f38ba8")
            QTimer.singleShot(0, self._handle_render_stage_failed)

    def start_ffmpeg_qprocess(self):
        self.log_safe("🚀 [阶段 2/2] 唤醒 FFmpeg 引擎，执行混合压制...", "#f9e2af")
        project_state = self._render_project_state()
        design_state = self._render_design_state()
        clips = project_state.get("video_clips", [])
        transparent_export = self._is_canva_transparent_export()
        no_subtitle_export = self._is_no_subtitle_export()
        quality_profile = self._export_quality_profile()
        use_subtitle_layer = not no_subtitle_export
        a_path = "" if transparent_export else project_state.get("audio_path")
        music_path = "" if transparent_export else project_state.get("music_path")
        if a_path and not os.path.exists(a_path):
            self.log_safe(f"⚠️ 配音文件不存在，已跳过: {a_path}", "#f9e2af")
            a_path = ""
        if music_path and not os.path.exists(music_path):
            music_path = ""
        self._cpu_retry_args = []
        self._cpu_retry_attempted = False
        target_dur = self._render_duration(project_state, design_state)
        render_range = self._current_render_range(project_state, design_state)
        render_start = float(render_range.get("start", 0.0) or 0.0)
        render_end = render_start + target_dur
        video_track_target = max(0.001, target_dur - render_tail_padding_seconds())
        if abs(target_dur - float(self.spin_duration.value())) > 0.01:
            self.spin_duration.setValue(target_dur)

        v_scale = project_state.get("v_scale", 100) / 100.0
        v_pos_x = self._safe_float(project_state.get("v_pos_x", 0), 0.0)
        v_pos_y = self._safe_float(project_state.get("v_pos_y", 0), 0.0)
        v_vol = project_state.get("v_volume", 100) / 100.0
        video_mask_enabled = bool(project_state.get("video_mask_enabled", False)) and not transparent_export
        video_mask_color = project_state.get("video_mask_color", "#000000")
        video_mask_alpha = self._safe_float(project_state.get("video_mask_alpha", 0), 0.0) if video_mask_enabled else 0.0
        a_vol = project_state.get("a_volume", 100) / 100.0
        music_vol = project_state.get("music_volume", 35) / 100.0

        res_text = project_state.get("resolution") or get_output_resolution()
        media_path = clips[0]["path"] if clips else ""
        proj_w, proj_h = resolution_to_size(res_text, media_path, get_video_dimensions)
        def video_mask_chain(input_label, output_label):
            if not video_mask_enabled:
                return f"[{input_label}]null[{output_label}]"
            return ffmpeg_video_mask_filter(
                input_label,
                output_label,
                proj_w,
                proj_h,
                target_dur,
                color=video_mask_color,
                alpha=video_mask_alpha,
            )

        if transparent_export:
            self.log_safe("🎨 Canva 透明 WebM：跳过视频/音频轨，只编码 RGBA 透明字幕层。", "#a6e3a1")
            clips = []

        video_concat_path = ""
        assembly_video_plan = []
        has_audio = False
        clip_speeds = [clip_speed_value(clip) for clip in clips or []]
        non_default_speeds = [speed for speed in clip_speeds if abs(speed - 1.0) > 0.001]
        uniform_video_speed = 1.0
        speed_export_supported = True
        if non_default_speeds:
            unique_speeds = {round(speed, 3) for speed in clip_speeds}
            if len(unique_speeds) == 1:
                uniform_video_speed = clip_speeds[0]
                self.log_safe(f"⏩ 视频变速导出: {uniform_video_speed:.2f}x", "#89b4fa")
            else:
                speed_export_supported = False
                self.log_safe("⚠️ 当前工程包含多种视频速度，本轮导出先按 1.0x 处理；预览和时间线仍按片段速度工作。", "#f9e2af")
        use_filter_concat = len(clips or []) > 1 and any(str(clip.get("assembly_mode", "")) in {"batch_random", "audio_matched"} for clip in clips or [])
        if clips and use_filter_concat:
            remaining_track_dur = video_track_target
            for clip in clips:
                if remaining_track_dur <= 0.001:
                    break
                clip_path = clip.get("path", "")
                if not clip_path or not os.path.exists(clip_path):
                    continue
                c_start = float(clip.get("start", 0))
                c_end = float(clip.get("end", 5.0))
                overlap_start = max(c_start, render_start)
                overlap_end = min(c_end, render_end)
                if overlap_end <= overlap_start:
                    continue
                speed = 1.0 if str(clip.get("assembly_mode", "")) in {"batch_random", "audio_matched"} else clip_speed_value(clip)
                media_dur = get_video_stream_duration(clip_path) or float(clip.get("dur", 0.0) or 0.0) or get_exact_duration(clip_path) or 5.0
                media_dur = max(0.1, media_dur)
                source_in = max(0.0, float(clip.get("source_in", 0.0) or 0.0))
                source_out = float(clip.get("source_out", media_dur) or media_dur)
                source_offset = max(0.0, overlap_start - c_start)
                timeline_dur = min(max(0.001, overlap_end - overlap_start), remaining_track_dur)
                is_image = os.path.splitext(clip_path)[1].lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
                assembly_video_plan.extend(build_looped_assembly_segments(
                    clip_path,
                    timeline_dur,
                    source_in=source_in,
                    source_out=source_out,
                    source_offset=source_offset,
                    speed=speed,
                    is_image=is_image,
                ))
                remaining_track_dur -= timeline_dur
            if assembly_video_plan:
                self.log_safe(f"🎬 多素材组接导出: {len(assembly_video_plan)} 段滤镜拼接", "#89b4fa")
        elif clips:
            try:
                flags = 0x08000000 if os.name == 'nt' else 0
                res = subprocess.run([get_ffmpeg_cmd(), "-i", clips[0]["path"]], stderr=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=flags, text=True, encoding='utf-8', errors='ignore')
                if "Audio:" in res.stderr:
                    has_audio = True
            except Exception:
                pass

            video_concat_path = os.path.join(self.temp_dir, "v_blocks.txt").replace("\\", "/")
            with open(video_concat_path, "w", encoding="utf-8") as f:
                written_video_dur = 0.0

                def write_looped_clip(clip, duration, source_offset=0.0):
                    clip_path = clip.get("path", "")
                    if not clip_path or duration <= 0:
                        return 0.0
                    speed = clip_speed_value(clip) if speed_export_supported else 1.0
                    media_dur = get_video_stream_duration(clip_path) or float(clip.get("dur", 0.0) or 0.0) or get_exact_duration(clip_path) or 5.0
                    media_dur = max(0.1, media_dur)
                    source_in = max(0.0, float(clip.get("source_in", 0.0) or 0.0))
                    source_out = float(clip.get("source_out", media_dur) or media_dur)
                    source_len = max(0.1, source_out - source_in)
                    cursor = (max(0.0, float(source_offset or 0.0)) * speed) % source_len
                    remaining = duration
                    written = 0.0
                    while remaining > 0.001:
                        inpoint = source_in + cursor
                        source_part_dur = min(remaining * speed, source_out - inpoint)
                        if source_part_dur <= 0.001:
                            cursor = 0.0
                            continue
                        f.write(ffconcat_inout_entry(clip_path, inpoint, inpoint + source_part_dur))
                        timeline_part_dur = source_part_dur / speed
                        remaining -= timeline_part_dur
                        written += timeline_part_dur
                        cursor = 0.0
                    return written

                remaining_track_dur = video_track_target
                for clip in clips:
                    if remaining_track_dur <= 0.001:
                        break
                    c_start = float(clip.get("start", 0))
                    c_end = float(clip.get("end", 5.0))
                    overlap_start = max(c_start, render_start)
                    overlap_end = min(c_end, render_end)
                    if overlap_end <= overlap_start:
                        continue
                    source_offset = max(0.0, overlap_start - c_start)
                    c_dur = min(max(0.001, overlap_end - overlap_start), remaining_track_dur)
                    written_video_dur += write_looped_clip(clip, c_dur, source_offset=source_offset)
                    remaining_track_dur -= c_dur
                if clips and written_video_dur < video_track_target - 0.01:
                    fill_dur = video_track_target - written_video_dur
                    write_looped_clip(clips[0], fill_dur)
                    self.log_safe(f"🔁 视频轨短于导出时长，已自动循环补齐 {fill_dur:.1f}s。", "#a6e3a1")
            self.log_safe("🛠️ 已生成物理拼接流: 精确修剪时间点挂载完毕！", "#89b4fa")

        self.render_process = QProcess(self)
        self.render_process.readyReadStandardError.connect(self.on_render_ready_read_error)
        self.render_process.finished.connect(self.on_render_finished)

        args = ["-y"]
        input_idx = 0
        video_idx = None
        if video_concat_path:
            args.extend(["-f", "concat", "-safe", "0", "-i", video_concat_path])
            video_idx = input_idx
            input_idx += 1
        for item in assembly_video_plan:
            if item.get("is_image"):
                args.extend(["-loop", "1", "-t", f"{float(item.get('source_dur', 0.0)):.3f}", "-i", item.get("path", "")])
            else:
                args.extend([
                    "-ss", f"{float(item.get('ss', 0.0)):.3f}",
                    "-t", f"{float(item.get('source_dur', 0.0)):.3f}",
                    "-i", item.get("path", ""),
                ])
            item["input_idx"] = input_idx
            input_idx += 1
        sub_idx = None
        if use_subtitle_layer:
            args.extend(["-f", "concat", "-safe", "0", "-i", self.concat_path])
            sub_idx = input_idx
            input_idx += 1
        audio_idx = None
        if a_path:
            args.extend(["-i", a_path])
            audio_idx = input_idx
            input_idx += 1
        music_idx = None
        if music_path:
            args.extend(["-stream_loop", "-1", "-i", music_path])
            music_idx = input_idx
            input_idx += 1

        fc_parts = []
        audio_map = None

        if assembly_video_plan:
            vf_scale = ffmpeg_exact_layer_filter(v_scale, proj_w, proj_h)
            layer_x, layer_y = ffmpeg_layer_overlay_xy(v_pos_x, v_pos_y)
            segment_labels = []
            for seg_idx, item in enumerate(assembly_video_plan):
                input_id = int(item.get("input_idx"))
                speed = max(0.05, float(item.get("speed", 1.0) or 1.0))
                timeline_dur = max(0.001, float(item.get("timeline_dur", 0.0) or 0.0))
                label = f"vseg{seg_idx}"
                fc_parts.append(
                    f"[{input_id}:v]setpts=(PTS-STARTPTS)/{speed:.6f},"
                    f"{vf_scale},tpad=stop_mode=clone:stop_duration={timeline_dur:.3f},"
                    f"trim=duration={timeline_dur:.3f},setpts=PTS-STARTPTS[{label}]"
                )
                segment_labels.append(f"[{label}]")
            if len(segment_labels) == 1:
                fc_parts.append(f"{segment_labels[0]}null[vcat]")
            else:
                fc_parts.append(f"{''.join(segment_labels)}concat=n={len(segment_labels)}:v=1:a=0[vcat]")
            video_guard = f"tpad=stop_mode=clone:stop_duration={target_dur:.3f},trim=duration={target_dur:.3f},setpts=PTS-STARTPTS"
            sub_guard = f"tpad=stop_mode=clone:stop_duration={target_dur:.3f},trim=duration={target_dur:.3f},setpts=PTS-STARTPTS"
            if no_subtitle_export:
                fc_parts.append(
                    f"{ffmpeg_canvas_source(proj_w, proj_h, target_dur)};"
                    f"[vcat]{video_guard}[fg];"
                    f"[canvas][fg]overlay=x='{layer_x}':y='{layer_y}':eof_action=pass:format=auto[bg];"
                    f"{video_mask_chain('bg', 'masked')};"
                    f"[masked]format=yuv420p[outv]"
                )
            else:
                fc_parts.append(
                    f"{ffmpeg_canvas_source(proj_w, proj_h, target_dur)};"
                    f"[vcat]{video_guard}[fg];"
                    f"[canvas][fg]overlay=x='{layer_x}':y='{layer_y}':eof_action=pass:format=auto[bg];"
                    f"[{sub_idx}:v]format=rgba,scale={proj_w}:{proj_h}:flags=lanczos,{sub_guard}[sub];"
                    f"{video_mask_chain('bg', 'masked')};"
                    f"[masked][sub]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[outv]"
                )
        elif video_concat_path:
            vf_scale = ffmpeg_layer_scale_filter(v_scale, proj_w, proj_h, fit="cover")
            layer_x, layer_y = ffmpeg_layer_overlay_xy(v_pos_x, v_pos_y)
            speed_filter = f"setpts=PTS/{uniform_video_speed:.6f}," if abs(uniform_video_speed - 1.0) > 0.001 else ""
            video_guard = f"tpad=stop_mode=clone:stop_duration={target_dur:.3f},trim=duration={target_dur:.3f},setpts=PTS-STARTPTS"
            sub_guard = f"tpad=stop_mode=clone:stop_duration={target_dur:.3f},trim=duration={target_dur:.3f},setpts=PTS-STARTPTS"
            if no_subtitle_export:
                fc_parts.append(
                    f"{ffmpeg_canvas_source(proj_w, proj_h, target_dur)};"
                    f"[{video_idx}:v]{speed_filter}{vf_scale},format=rgba,{video_guard}[fg];"
                    f"[canvas][fg]overlay=x='{layer_x}':y='{layer_y}':eof_action=pass:format=auto[bg];"
                    f"{video_mask_chain('bg', 'masked')};"
                    f"[masked]format=yuv420p[outv]"
                )
            else:
                fc_parts.append(
                    f"{ffmpeg_canvas_source(proj_w, proj_h, target_dur)};"
                    f"[{video_idx}:v]{speed_filter}{vf_scale},format=rgba,{video_guard}[fg];"
                    f"[canvas][fg]overlay=x='{layer_x}':y='{layer_y}':eof_action=pass:format=auto[bg];"
                    f"[{sub_idx}:v]format=rgba,scale={proj_w}:{proj_h}:flags=lanczos,{sub_guard}[sub];"
                    f"{video_mask_chain('bg', 'masked')};"
                    f"[masked][sub]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[outv]"
                )
        else:
            if no_subtitle_export:
                fc_parts.append(f"{ffmpeg_canvas_source(proj_w, proj_h, target_dur)};[canvas]format=yuv420p[outv]")
            else:
                out_pix_fmt = "yuva420p" if transparent_export else "yuv420p"
                fc_parts.append(f"[{sub_idx}:v]format=rgba,scale={proj_w}:{proj_h}:flags=lanczos,tpad=stop_mode=clone:stop_duration={target_dur:.3f},trim=duration={target_dur:.3f},setpts=PTS-STARTPTS,format={out_pix_fmt}[outv]")

        audio_sources = []
        if video_idx is not None and has_audio:
            speed_audio = f",{atempo_chain(uniform_video_speed)}" if abs(uniform_video_speed - 1.0) > 0.001 else ""
            fc_parts.append(f"[{video_idx}:a]volume={v_vol:.3f}{speed_audio},atrim=duration={target_dur:.3f},asetpts=PTS-STARTPTS[va]")
            audio_sources.append("[va]")
        if audio_idx is not None:
            a_trim = project_state.get("a_trim") or [0.0, target_dur]
            a_start = self._safe_float(a_trim[0], 0.0) if len(a_trim) >= 1 else 0.0
            a_end = self._safe_float(a_trim[1], a_start) if len(a_trim) >= 2 else a_start + target_dur
            overlap_start = max(a_start, render_start)
            overlap_end = min(a_end, render_end)
            if overlap_end > overlap_start + 0.001:
                source_in = max(0.0, self._safe_float(project_state.get("audio_source_in"), 0.0))
                source_start = source_in + max(0.0, overlap_start - a_start)
                clip_dur = max(0.001, overlap_end - overlap_start)
                delay_ms = max(0, int(round((overlap_start - render_start) * 1000)))
                delay_filter = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
                fc_parts.append(f"[{audio_idx}:a]atrim=start={source_start:.3f}:duration={clip_dur:.3f},asetpts=PTS-STARTPTS,volume={a_vol:.3f}{delay_filter},apad,atrim=duration={target_dur:.3f},asetpts=PTS-STARTPTS[aa]")
                audio_sources.append("[aa]")
        if music_idx is not None:
            music_end = self._safe_float(project_state.get("music_match_duration"), 0.0)
            if music_end <= 0:
                music_end = self._safe_float(project_state.get("music_dur"), 0.0)
            if music_end <= 0:
                music_end = target_dur
            overlap_start = max(0.0, render_start)
            overlap_end = min(music_end, render_end)
            if overlap_end > overlap_start + 0.001:
                clip_dur = max(0.001, overlap_end - overlap_start)
                delay_ms = max(0, int(round((overlap_start - render_start) * 1000)))
                delay_filter = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
                fc_parts.append(f"[{music_idx}:a]atrim=start={overlap_start:.3f}:duration={clip_dur:.3f},asetpts=PTS-STARTPTS,volume={music_vol:.3f}{delay_filter},apad,atrim=duration={target_dur:.3f},asetpts=PTS-STARTPTS[ma]")
                audio_sources.append("[ma]")
        if len(audio_sources) == 1:
            audio_map = audio_sources[0]
        elif len(audio_sources) > 1:
            fc_parts.append(f"{''.join(audio_sources)}amix=inputs={len(audio_sources)}:duration=longest:normalize=0,atrim=duration={target_dur:.3f},asetpts=PTS-STARTPTS[aout]")
            audio_map = "[aout]"

        if fc_parts:
            args.extend(["-filter_complex", ";".join(fc_parts)])

        args.extend(["-map", "[outv]"])

        if audio_map:
            args.extend(["-map", audio_map, "-c:a", "aac", "-b:a", "192k"])
        else:
            args.append("-an")

        # 👑 极速高压引擎：MP4 保持原硬件优先；Canva WebM 固定 VP9 alpha，保留透明通道。
        if transparent_export:
            args.extend([
                "-r", "30",
                "-max_muxing_queue_size", "1024",
                "-t", str(target_dur),
                "-c:v", "libvpx-vp9",
                "-pix_fmt", "yuva420p",
                "-metadata:s:v:0", "alpha_mode=1",
                "-auto-alt-ref", "0",
                "-b:v", "0",
                "-crf", str(quality_profile.get("vp9_crf", "24")),
                "-deadline", str(quality_profile.get("vp9_deadline", "good")),
                "-cpu-used", str(quality_profile.get("vp9_cpu_used", "4")),
                "-row-mt", "1",
                "-tile-columns", str(quality_profile.get("vp9_tile_columns", "1")),
                "-threads", str(max(2, min(8, os.cpu_count() or 4))),
                self.out_file_path,
            ])
            self._cpu_retry_args = []
            encoder_label = "VP9 WebM Alpha（Canva 透明层）"
        else:
            render_profile = get_render_profile()
            encoder_label = render_profile.get("encoder_label") or render_profile.get("encoder", "CPU x264")
            base_args = list(args)
            final_args = ["-r", "30", "-max_muxing_queue_size", "1024", "-t", str(target_dur), self.out_file_path]
            encoder_quality = "deliver_fast" if str(quality_profile.get("mode") or "") == "极速出片" else "deliver"
            args.extend(build_video_encoder_args(render_profile, quality=encoder_quality))
            args.extend(final_args)
            if render_profile.get("encoder") != "libx264":
                self._cpu_retry_args = (
                    base_args
                    + build_video_encoder_args(self._cpu_render_profile(), quality=encoder_quality)
                    + final_args
                )

        self._render_encoder_label = encoder_label
        if transparent_export:
            self.log_safe("透明 WebM 使用 VP9 Alpha 编码，通常会比普通 MP4 慢很多，这是格式限制。", "#f9e2af")
        self._render_ffmpeg_started_at = time.monotonic()
        self._render_last_ffmpeg_speed = ""
        self.log_safe(f"⚙️ 渲染配置: {encoder_label}", "#89b4fa")
        self.log_safe("🧾 FFmpeg 参数已生成，开始压制...", "#89b4fa")
        self.render_process.start(get_ffmpeg_cmd(), args)

    def on_render_ready_read_error(self):
        err_out = str(self.render_process.readAllStandardError(), encoding="utf-8", errors="ignore")
        time_match = re.search(r"time=(\d+:\d+:\d+\.\d+)", err_out)
        speed_match = re.search(r"speed=\s*([0-9.]+x|N/A)", err_out)
        if speed_match:
            self._render_last_ffmpeg_speed = speed_match.group(1)
        if time_match:
            time_str = time_match.group(1)
            h, m, s = map(float, time_str.split(":"))
            curr_sec = h * 3600 + m * 60 + s
            total_sec = max(0.1, float(self.active_render_duration or self.spin_duration.value()))
            percent = 50 + int((curr_sec / total_sec) * 50)
            self.progress_bar.setValue(min(100, percent))
        if err_out.strip():
            self.log_console.append(f"<span style='color:#6c7086'>{err_out.strip()}</span>")
            self.log_console.verticalScrollBar().setValue(self.log_console.verticalScrollBar().maximum())


    def _probe_canva_alpha_export(self, output_path):
        if not output_path or not os.path.exists(output_path):
            return False, "找不到导出文件"
        try:
            flags = 0x08000000 if os.name == 'nt' else 0
            result = subprocess.run(
                [
                    get_ffprobe_cmd(),
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=pix_fmt:stream_tags=alpha_mode",
                    "-of", "json",
                    output_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=flags,
                timeout=12,
            )
            payload = json.loads(result.stdout or "{}")
            streams = payload.get("streams") or []
            stream = streams[0] if streams else {}
            pix_fmt = str(stream.get("pix_fmt") or "")
            tags = stream.get("tags") or {}
            alpha_mode = str(tags.get("alpha_mode") or tags.get("ALPHA_MODE") or "")
            has_alpha = pix_fmt.startswith("yuva") or alpha_mode == "1"
            detail = f"pix_fmt={pix_fmt or 'unknown'}, alpha_mode={alpha_mode or 'none'}"
            return has_alpha, detail
        except Exception as e:
            return False, f"透明检测失败: {e}"

    def _maybe_upload_canva_transparent_export(self):
        cfg = load_app_config()
        if not cfg.get("canva_auto_upload"):
            return
        output_path = str(getattr(self, "out_file_path", "") or "")
        if not output_path or not os.path.exists(output_path):
            self.log_safe("⚠️ Canva 自动上传跳过：找不到导出的 WebM 文件。", "#f9e2af")
            return
        if not (cfg.get("canva_access_token") or cfg.get("canva_refresh_token")):
            self.log_safe("⚠️ Canva 自动上传跳过：设置里还没有完成 Canva 授权。", "#f9e2af")
            return
        self.log_safe("☁️ Canva 自动上传已启动：正在把透明 WebM 传到素材库...", "#89b4fa")
        threading.Thread(target=self._upload_canva_transparent_export_thread, args=(output_path, cfg), daemon=True).start()

    def _upload_canva_transparent_export_thread(self, output_path, cfg):
        try:
            payload, updated_cfg = upload_asset(cfg, output_path, asset_name=os.path.basename(output_path))
            if updated_cfg != cfg:
                save_app_config(updated_cfg)
            asset_hint = ""
            if isinstance(payload, dict):
                upload_info = payload.get("asset_upload") or payload.get("job") or payload
                if isinstance(upload_info, dict):
                    asset_hint = upload_info.get("id") or upload_info.get("asset_id") or ""
            msg = "✅ Canva 上传任务已创建。" + (f" ID: {asset_hint}" if asset_hint else "")
            QTimer.singleShot(0, lambda m=msg: self.log_safe(m, "#a6e3a1"))
        except Exception as e:
            QTimer.singleShot(0, lambda err=str(e): self.log_safe(f"⚠️ Canva 自动上传失败：{err}", "#f38ba8"))

    def on_render_finished(self, exit_code, exit_status):
        if exit_code != 0:
            if self._retry_render_with_cpu():
                return
        self._log_render_perf_summary(exit_code)
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass
        if self.batch_rendering:
            project_name = os.path.basename(self.current_batch_project_path) if self.current_batch_project_path else "工程"
            if exit_code == 0:
                self.log_safe(f"✅ 完成: {project_name}", "#a6e3a1")
                if self._is_canva_transparent_export():
                    alpha_ok, alpha_detail = self._probe_canva_alpha_export(self.out_file_path)
                    if alpha_ok:
                        self.log_safe(f"✅ 透明通道检测通过：{alpha_detail}", "#a6e3a1")
                    else:
                        self.log_safe(f"⚠️ 透明通道检测未确认：{alpha_detail}。若 Canva 黑底，请先用 Chrome/Edge 本地打开验证。", "#f9e2af")
                    self._maybe_upload_canva_transparent_export()
                elif self._is_no_subtitle_export():
                    self.log_safe("✅ 无字幕 MP4 已输出：没有烧录字幕层。", "#a6e3a1")
            else:
                self.log_safe(f"❌ 失败: {project_name}，错误代码 {exit_code}", "#f38ba8")
            self.batch_render_index += 1
            QTimer.singleShot(0, self._start_next_batch_render)
            return

        finished_transparent_export = self._is_canva_transparent_export()
        finished_no_subtitle_export = self._is_no_subtitle_export()
        self._clear_render_job()
        self.btn_render.setEnabled(True)
        if exit_code == 0:
            self.progress_bar.setValue(100)
            if finished_transparent_export:
                alpha_ok, alpha_detail = self._probe_canva_alpha_export(self.out_file_path)
                if alpha_ok:
                    self.log_safe(f"🎉 Canva 透明 WebM 已成功输出，透明通道检测通过：{alpha_detail}", "#a6e3a1")
                    finish_message = "透明字幕 WebM 已导出，并检测到透明通道。可导入 Canva 作为透明视频层。若已启用 Canva 自动上传，上传会在后台继续。"
                else:
                    self.log_safe(f"⚠️ Canva 透明 WebM 已输出，但透明通道检测未确认：{alpha_detail}", "#f9e2af")
                    finish_message = "透明字幕 WebM 已导出，但本地未确认到透明通道标记。若 Canva 显示黑底，请先用 Chrome/Edge 本地打开验证，再考虑改用绿幕或 PNG 序列方案。"
                self._maybe_upload_canva_transparent_export()
                QMessageBox.information(self, "导出完成", finish_message)
            elif finished_no_subtitle_export:
                self.log_safe("🎉 无字幕 MP4 已成功输出。", "#a6e3a1")
                QMessageBox.information(self, "出片完成", "视频、音频已导出；字幕没有烧录，可到其他软件继续添加。")
            else:
                self.log_safe("🎉 渲染完美收官！视频已成功输出。", "#a6e3a1")
                QMessageBox.information(self, "出片完成", "字幕、音频、画面已按当前工程成功导出。")
        else:
            self.log_safe(f"❌ 渲染崩塌，错误代码: {exit_code}", "#f38ba8")
            QMessageBox.critical(self, "失败", "FFmpeg 渲染发生错误，请查看日志！")
