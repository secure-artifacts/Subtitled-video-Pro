# ==========================================
# 文件名: room_batch.py (终极满血修复版 - 包含静音、抗锯齿、完美表格解析与独立预览)
# ==========================================
import os
import json
import tempfile
import threading
import subprocess
import requests
import re
import shutil
import csv
import io
import copy
import random
import itertools
import math
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QToolButton, QAbstractItemView, QSplitter,
                             QLabel, QFrame, QProgressBar, QTextEdit, QFileDialog,
                             QMessageBox, QComboBox, QTabWidget, QScrollArea, QLineEdit, QDialog, QDoubleSpinBox, QTabBar, QInputDialog,
                             QSizePolicy)
from PyQt6.QtWidgets import QSlider, QSpinBox, QCheckBox
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QMetaObject, Q_ARG
from PyQt6.QtGui import QPixmap
from playwright.sync_api import sync_playwright

from core import get_ffmpeg_cmd
import media_probe
from app_theme import apply_tinted_styles
from room_theme_bridge import apply_room_theme_bridge
from app_config import get_output_resolution, load_app_config, resolution_to_size
from ai_transcription import transcribe_audio_words
from app_storage import read_json_file, resolve_user_file, write_json_file
from render_config import build_video_encoder_args, get_render_profile
from render_pipeline_model import ffconcat_file_entry, ffconcat_inout_entry, ffmpeg_canvas_source, ffmpeg_layer_overlay_xy, ffmpeg_layer_scale_filter
from render_timing import active_subtitles_for_frame, build_subtitle_frame_schedule, render_tail_padding_seconds, subtitle_supersample
# 确保导入了 get_exact_duration
from ui_components import (
    get_exact_duration, get_video_dimensions, render_signature_html, render_subtitle_html,
    get_video_stream_duration,
    rebalance_subtitle_layout, tokenize_display_text,
    normalize_word_timestamps, align_reference_text_to_timestamps,
    format_subtitle_text_spacing,
    default_signature_config, normalize_signature_config,
    should_defer_subtitle_break_for_readability,
    merge_single_word_subtitle_segments, protect_fast_subtitle_pacing,
    FAITH_WORDS
)
from project_io import create_reel, sync_project_assets_to_project_dir, update_room_state, save_project
from workspace_config import WORKSPACE_MODE_CLOUD, get_active_workspace, get_workspace_config
from job_control import CooperativeJobControl
from caption_presets import (
    LEGACY_NARRATIVE_CHUNK_MODE,
    REFERENCE_NARRATIVE_CHUNK_MODE,
    fixed_word_count_for_chunk_mode,
    pacing_merge_word_limit_for_chunk_mode,
    is_exact_single_word_chunk_mode,
    is_reference_narrative_chunk_mode,
    merge_built_in_style_presets,
    narrative_chunk_merge_words,
    narrative_chunk_word_bounds,
)

PRESETS_FILE = resolve_user_file("style_presets.json", legacy_root=os.getcwd(), kind="config")
SIGNATURE_PRESETS_FILE = resolve_user_file("signature_presets.json", legacy_root=os.getcwd(), kind="config")
BATCH_QUEUE_BACKUPS_FILE = resolve_user_file("batch_queue_backups.json", legacy_root=os.getcwd(), kind="state")
STYLE_PRESET_POSITION_KEY = "__position__"
SUBTITLE_SUPERSAMPLE = subtitle_supersample()
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
VIDEO_EXTS = (".mp4", ".mov", ".webm")
MEDIA_EXTS = VIDEO_EXTS + IMAGE_EXTS
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
TEXT_EXTS = (".txt", ".md", ".srt", ".vtt", ".ass", ".lrc")
BATCH_MUSIC_MODES = (
    ("顺序循环", "cycle"),
    ("随机分配", "random"),
    ("固定第一首", "first"),
)
BATCH_AUDIO_SORT_MODES = (
    ("文件名自然排序", "natural"),
    ("创建/生成时间从早到晚", "created"),
    ("创建/生成时间从晚到早", "created_desc"),
    ("修改时间从早到晚", "modified"),
    ("修改时间从晚到早", "modified_desc"),
)
BATCH_ASSEMBLY_PRIORITY_MODES = (
    ("自动优先", "auto"),
    ("新素材优先", "new_first"),
    ("旧素材优先", "old_first"),
    ("不设置优先级", "none"),
)
TABLE_PASTE_HEADER_ALIASES = {
    "video": ("视频", "画面", "素材", "视频路径", "画面路径", "media", "video", "video path", "media path"),
    "audio": ("音频", "配音", "声音", "音频路径", "配音路径", "audio", "voice", "audio path", "voice path"),
    "text_path": ("文本路径", "字幕文件", "文案文件", "txt", "text file", "script file", "caption file"),
    "title": ("标题", "大标题", "工程名", "项目名", "title", "name", "project"),
    "body": ("正文", "文案", "内容", "字幕", "字幕文案", "script", "text", "copy", "caption", "subtitle"),
    "tag": ("标签", "标签名", "素材标签", "分类", "备注标签", "tag", "tags", "label", "category"),
    "y": ("字幕y", "字幕Y", "y", "pos y", "position y", "subtitle y"),
}


def _compact_table_header(value):
    return re.sub(r"[\s_\-:/：|（）()\[\]【】]+", "", str(value or "").strip().lower())


def table_paste_header_key(value):
    compact = _compact_table_header(value)
    if not compact:
        return ""
    for key, aliases in TABLE_PASTE_HEADER_ALIASES.items():
        if compact in {_compact_table_header(alias) for alias in aliases}:
            return key
    return ""


def detect_table_paste_header(parts):
    header_map = {}
    for idx, cell in enumerate(parts or []):
        key = table_paste_header_key(cell)
        if key and key not in header_map:
            header_map[key] = idx
    return header_map


def prefixed_tag_value(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^(?:标签|标签名|素材标签|分类|tag|tags|label|category)\s*[:：]\s*(.+)$", raw, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if raw.startswith("#") and 1 < len(raw) <= 48 and "\n" not in raw:
        return raw[1:].strip()
    return ""

def split_style_preset(raw):
    if not isinstance(raw, dict):
        return {}, None
    if isinstance(raw.get("style"), dict):
        style = copy.deepcopy(raw.get("style") or {})
        position = raw.get("position") or raw.get(STYLE_PRESET_POSITION_KEY)
    else:
        style = {
            k: copy.deepcopy(v)
            for k, v in raw.items()
            if k not in (STYLE_PRESET_POSITION_KEY, "position")
        }
        position = raw.get(STYLE_PRESET_POSITION_KEY) or raw.get("position")
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


def built_in_signature_presets():
    base = default_signature_config()
    return {
        "右上角柔光玻璃": copy.deepcopy(base),
        "右上角纯色小标": {
            **copy.deepcopy(base),
            "style": {
                **copy.deepcopy(base.get("style", {})),
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
                **copy.deepcopy(base.get("style", {})),
                "bg_mode": "none",
                "bg_alpha": 0,
                "stroke_width": 2,
                "shadow_alpha": 70,
            },
        },
    }


def load_signature_presets_file():
    presets = built_in_signature_presets()
    saved = read_json_file(SIGNATURE_PRESETS_FILE, default={})
    if isinstance(saved, dict):
        presets.update(saved)
    return presets


def _has_ext(value, extensions):
    return str(value or "").strip().lower().endswith(extensions)


def looks_media_path(value):
    return _has_ext(value, MEDIA_EXTS)


def looks_audio_path(value):
    return _has_ext(value, AUDIO_EXTS)


def looks_text_path(value):
    return _has_ext(value, TEXT_EXTS)


def media_file_filter():
    return "Video/Image Files (*.mp4 *.mov *.webm *.jpg *.jpeg *.png)"


def audio_file_filter():
    return "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"


def file_stem(path):
    return os.path.splitext(os.path.basename(path or ""))[0]


def safe_filename_stem(value, fallback="BatchReel"):
    raw = str(value or "").strip() or fallback
    safe = "".join(c for c in raw if c not in r'\/:*?"<>|').strip(" .")
    return safe or fallback


def read_text_source(path):
    if not path or not os.path.exists(path):
        return ""
    raw = ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw = f.read()
            break
        except Exception:
            raw = ""
    if not raw:
        return ""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".srt", ".vtt", ".lrc"):
        lines = []
        for line in raw.splitlines():
            t = line.strip()
            if not t or t.isdigit() or "-->" in t or t.upper() == "WEBVTT":
                continue
            t = re.sub(r"^\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s*", "", t)
            lines.append(t)
        raw = "\n".join(lines)
    elif ext == ".ass":
        lines = []
        for line in raw.splitlines():
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) == 10:
                    lines.append(parts[-1])
        raw = "\n".join(lines)
    raw = re.sub(r"\{\\.*?\}", "", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return raw.strip()


def project_title_from_task(title, video_path):
    raw = str(title or "").strip()
    video_stem = os.path.splitext(os.path.basename(video_path or ""))[0] or "Reel"
    if not raw:
        return video_stem
    base = os.path.basename(raw)
    ext = os.path.splitext(base)[1].lower()
    if ext in TEXT_EXTS or looks_media_path(raw) or looks_audio_path(raw):
        return video_stem
    if os.path.dirname(raw):
        return os.path.splitext(base)[0] or video_stem
    return raw

def natural_sort_key(path_or_name):
    name = os.path.basename(path_or_name or "")
    stem = os.path.splitext(name)[0].strip()
    prefix = re.match(r"^\s*(\d+)(?:[\s_.\-]+|$)", stem)
    if prefix:
        return (0, int(prefix.group(1)), re.sub(r"^\s*\d+(?:[\s_.\-]+|$)", "", stem).lower())
    return (1, [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", stem)])


def file_time_sort_key(path, mode="natural"):
    mode_key = str(mode or "natural")
    descending = mode_key.endswith("_desc")
    base_mode = mode_key[:-5] if descending else mode_key
    if base_mode not in {"created", "modified"}:
        return (0, natural_sort_key(path))
    try:
        timestamp = os.path.getctime(path) if base_mode == "created" else os.path.getmtime(path)
    except Exception:
        timestamp = 0
    if descending:
        timestamp = -timestamp
    return (timestamp, natural_sort_key(path), os.path.basename(path).casefold())


def sort_audio_paths(paths, mode="natural"):
    return sorted(paths or [], key=lambda path: file_time_sort_key(path, mode))


def media_sequence_id(path_or_name):
    stem = os.path.splitext(os.path.basename(path_or_name or ""))[0].strip()
    match = re.match(r"^\s*0*(\d+)(?:[\s_.\-]+|$)", stem)
    return str(int(match.group(1))) if match else ""


def normalize_media_title(path_or_name):
    stem = os.path.splitext(os.path.basename(path_or_name or ""))[0].strip().lower()
    stem = re.sub(r"^\s*\d+(?:[\s_.\-]+|$)", "", stem)
    stem = re.sub(r"[\s_.\-]+", "", stem)
    return stem


def build_audio_lookup(input_dir, sort_mode="natural"):
    audio_paths = sort_audio_paths(
        [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.lower().endswith(AUDIO_EXTS)],
        sort_mode,
    )
    by_stem = {}
    by_seq = {}
    by_title = {}
    for full_path in audio_paths:
        name = os.path.basename(full_path)
        stem = os.path.splitext(name)[0]
        by_stem.setdefault(stem.lower(), full_path)
        seq = media_sequence_id(name)
        if seq:
            by_seq.setdefault(seq, full_path)
        title = normalize_media_title(name)
        if title:
            by_title.setdefault(title, full_path)
    return {"by_stem": by_stem, "by_seq": by_seq, "by_title": by_title}


def list_audio_paths(input_dir, sort_mode="natural"):
    return sort_audio_paths(
        [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.lower().endswith(AUDIO_EXTS)],
        sort_mode,
    )


def match_audio_for_media(video_name, audio_lookup):
    base_name = os.path.splitext(video_name)[0]
    exact = audio_lookup["by_stem"].get(base_name.lower())
    if exact:
        return exact
    seq = media_sequence_id(video_name)
    if seq and seq in audio_lookup["by_seq"]:
        return audio_lookup["by_seq"][seq]
    title = normalize_media_title(video_name)
    if title and title in audio_lookup["by_title"]:
        return audio_lookup["by_title"][title]
    return ""


def local_get_cf_accounts():
    return load_app_config().get("cf_accounts", [])

def get_browser_path():
    if os.name == 'nt':
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        ]
    else: paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for p in paths:
        if os.path.exists(p): return p
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


def has_audio_stream(path):
    return media_probe.has_audio_stream(path)

class BatchTaskRow(QFrame):
    def __init__(self, parent_view=None, parent=None):
        super().__init__(parent)
        self.parent_view = parent_view
        self.video_path = ""
        self.audio_path = ""
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("QFrame { background-color: #1e1e2e; border: 1px solid #313244; border-radius: 6px; }")
        self.setMinimumHeight(82)
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(8, 5, 8, 5)
        row_layout.setSpacing(6)

        self.btn_vid = QPushButton("➕ 选画面")
        self.btn_vid.setFixedSize(82, 26)
        self.btn_vid.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_vid.clicked.connect(self.select_video)

        self.btn_aud = QPushButton("🎵 选配音")
        self.btn_aud.setFixedSize(82, 26)
        self.btn_aud.setStyleSheet("background-color: #cba6f7; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_aud.clicked.connect(self.select_audio)

        media_layout = QVBoxLayout()
        media_layout.setSpacing(4)
        video_row = QHBoxLayout()
        audio_row = QHBoxLayout()
        self.edit_video = QLineEdit()
        self.edit_video.setPlaceholderText("视频/图片路径，可直接粘贴复制")
        self.edit_video.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; padding: 4px;")
        self.edit_video.textChanged.connect(self._on_video_path_edited)
        self.edit_audio = QLineEdit()
        self.edit_audio.setPlaceholderText("配音路径，可复制到下一行")
        self.edit_audio.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; padding: 4px;")
        self.edit_audio.textChanged.connect(self._on_audio_path_edited)
        video_row.addWidget(self.btn_vid)
        video_row.addWidget(self.edit_video, stretch=1)
        audio_row.addWidget(self.btn_aud)
        audio_row.addWidget(self.edit_audio, stretch=1)
        media_layout.addLayout(video_row)
        media_layout.addLayout(audio_row)
        row_layout.addLayout(media_layout, stretch=2)

        # 👑 新增：独立的高度调节器
        y_layout = QVBoxLayout()
        y_label = QLabel("字幕Y值", styleSheet="color: #a6adc8; font-size: 10px; border: none;")
        self.spin_y = QDoubleSpinBox()
        self.spin_y.setRange(-50.0, 50.0)
        self.spin_y.setDecimals(1)
        self.spin_y.setSingleStep(1.0)
        self.spin_y.setValue(25.0) # 默认在靠下的位置
        self.spin_y.setStyleSheet("background-color: #313244; color: #cdd6f4; border: 1px solid #45475a;")
        self.spin_y.setFixedWidth(76)
        y_layout.addWidget(y_label)
        y_layout.addWidget(self.spin_y)
        row_layout.addLayout(y_layout)

        meta_layout = QVBoxLayout()
        meta_layout.setSpacing(4)
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("大标题 (可选)")
        self.txt_title.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; padding: 4px;")
        self.txt_title.setFixedWidth(112)
        self.txt_title.setFixedHeight(24)
        self.txt_tag = QLineEdit()
        self.txt_tag.setPlaceholderText("标签")
        self.txt_tag.setToolTip("工程标签：例如 老人素材 / 小孩素材 / 海边 / 室内。只用于精修顶部提示，不参与字幕文案。")
        self.txt_tag.setStyleSheet("background-color: #11111b; color: #f9e2af; border: 1px solid #313244; padding: 4px;")
        self.txt_tag.setFixedWidth(112)
        self.txt_tag.setFixedHeight(24)
        meta_layout.addWidget(self.txt_title)
        meta_layout.addWidget(self.txt_tag)
        row_layout.addLayout(meta_layout)

        self.txt_content = QTextEdit()
        self.txt_content.setPlaceholderText("详细正文文案 (支持多行/不填则盲听)")
        self.txt_content.setStyleSheet("background-color: #11111b; color: #a6adc8; border: 1px solid #313244; padding: 5px;")
        self.txt_content.setFixedHeight(52)
        row_layout.addWidget(self.txt_content, stretch=1)

        # 👑 新增：预览按钮
        self.btn_preview = QPushButton("👁️ 预览")
        self.btn_preview.setFixedSize(58, 32)
        self.btn_preview.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_preview.clicked.connect(self.preview_frame)
        row_layout.addWidget(self.btn_preview)

        self.lbl_status = QLabel("待处理")
        self.lbl_status.setFixedWidth(54)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("color: #a6adc8; border: none;")
        row_layout.addWidget(self.lbl_status)

        self.btn_del = QPushButton("X")
        self.btn_del.setToolTip("删除这一行")
        self.btn_del.setFixedSize(32, 32)
        self.btn_del.setStyleSheet("background-color: #f36f8e; color: #2b0b12; font-size: 15px; font-weight: 900; border-radius: 6px; border: none;")
        self.btn_del.clicked.connect(self.deleteLater)
        row_layout.addWidget(self.btn_del)
        self.apply_compact_theme()

    def apply_compact_theme(self):
        self.btn_del.setText("X")
        self.btn_del.setStyleSheet("background-color: #f36f8e; color: #2b0b12; font-size: 15px; font-weight: 900; border-radius: 6px; border: none;")
        self.btn_preview.setStyleSheet("background-color: #f9d17a; color: #111315; font-weight: 900; border-radius: 6px; border: none;")

    def select_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择画面", "", media_file_filter())
        if path:
            self.set_video_path(path)

    def select_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择配音", "", audio_file_filter())
        if path:
            self.set_audio_path(path)
            if self.parent_view and hasattr(self.parent_view, "_sidecar_text_for_audio") and not self.txt_content.toPlainText().strip():
                sidecar_text = self.parent_view._sidecar_text_for_audio(path)
                if sidecar_text:
                    self.txt_content.setPlainText(sidecar_text)

    def set_video_path(self, path):
        self.video_path = (path or "").strip()
        self.edit_video.setText(self.video_path)
        if self.video_path:
            self.btn_vid.setText("✅ 画面")
            self.btn_vid.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 4px;")

    def set_audio_path(self, path):
        self.audio_path = (path or "").strip()
        self.edit_audio.setText(self.audio_path)
        if self.audio_path:
            self.btn_aud.setText("✅ 配音")
            self.btn_aud.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 4px;")

    def _on_video_path_edited(self, text):
        self.video_path = text.strip()
        if self.video_path:
            self.btn_vid.setText("✅ 画面")
            self.btn_vid.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 4px;")
        else:
            self.btn_vid.setText("➕ 选画面")
            self.btn_vid.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")

    def _on_audio_path_edited(self, text):
        self.audio_path = text.strip()
        if self.audio_path:
            self.btn_aud.setText("✅ 配音")
            self.btn_aud.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 4px;")
        else:
            self.btn_aud.setText("🎵 选配音")
            self.btn_aud.setStyleSheet("background-color: #cba6f7; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")

    def sync_paths_from_fields(self):
        self.video_path = self.edit_video.text().strip()
        self.audio_path = self.edit_audio.text().strip()

    # 👑 核心魔法：单行截取中间帧预览
    def preview_frame(self):
        self.sync_paths_from_fields()
        if not self.video_path:
            return QMessageBox.warning(self, "提示", "请先选择画面！")

        self.btn_preview.setText("加载中..")
        self.btn_preview.setEnabled(False)

        try:
            threading.Thread(target=self._generate_preview_thread, daemon=True).start()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"预览失败: {e}")
            self.btn_preview.setText("👁️ 预览")
            self.btn_preview.setEnabled(True)

    def _generate_preview_thread(self):
        try:
            temp_dir = tempfile.mkdtemp()
            frame_path = os.path.join(temp_dir, "preview_frame.jpg").replace("\\", "/")
            sub_path = os.path.join(temp_dir, "preview_sub.png").replace("\\", "/")

            # 1. 用 FFmpeg 提取中间那一帧
            dur = get_exact_duration(self.video_path)
            mid_time = dur / 2.0 if dur > 0 else 0
            subprocess.run([get_ffmpeg_cmd(), "-y", "-ss", str(mid_time), "-i", self.video_path, "-vframes", "1", "-q:v", "2", frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 2. 获取预设样式
            preset_style = {}
            preset_pos_x, preset_pos_y = 0.0, self.spin_y.value()
            if self.parent_view and hasattr(self.parent_view, 'preset_combo'):
                preset_style = self.parent_view._load_selected_preset_style()
                preset_pos_x, _ = self.parent_view.selected_preset_position(default_y=self.spin_y.value())
                preset_pos_y = self.spin_y.value()

            # 3. 构造一个假字幕数据
            txt = self.txt_content.toPlainText().strip()
            if not txt: txt = "这是字幕高度位置预览测试"
            txt = txt.split('\n')[0][:15]

            sub_data = {
                "text": txt,
                "words": [{"text": txt, "start": 0, "end": 1}],
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y,
                "style": preset_style
            }

            proj_w, proj_h = resolution_to_size(get_output_resolution(), self.video_path, get_video_dimensions)

            # 4. 用 Playwright 渲染透明字幕截图（带抗锯齿）
            with sync_playwright() as p:
                browser = launch_render_browser(p)
                render_scale = self.parent_view.subtitle_render_scale() if self.parent_view and hasattr(self.parent_view, "subtitle_render_scale") else SUBTITLE_SUPERSAMPLE
                render_w = int(proj_w * render_scale)
                render_h = int(proj_h * render_scale)
                page = browser.new_page(viewport={"width": render_w, "height": render_h}, device_scale_factor=1)

                px = sub_data.get("pos_x", 0.0); py = sub_data.get("pos_y", 25.0)
                base_css = f"position: absolute; left: calc(50% + {px}%); top: calc(50% + {py}%); transform: translate(-50%, -50%); z-index: 10; width: max-content; max-width: 92%;"
                sub_html = render_subtitle_html(sub_data, 0.5, proj_w, proj_h)
                html_content = f"<!DOCTYPE html><html><head><style>html, body {{ margin: 0; padding: 0; width: 100vw; height: 100vh; overflow: hidden; background: transparent; display: flex; justify-content: center; align-items: center; -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }} #scale-wrapper {{ width: 100vw; height: 100vh; position: absolute; left: 0; top: 0; filter: drop-shadow(0px 0px 0px transparent); }}</style></head><body><div id='scale-wrapper'><div style='{base_css}'>{sub_html}</div></div></body></html>"

                page.set_content(html_content)
                page.screenshot(path=sub_path, omit_background=True, scale="css")
                browser.close()

            # 5. FFmpeg 合成最终预览图
            out_preview = os.path.join(temp_dir, "final_preview.jpg").replace("\\", "/")
            preview_filter = f"[1:v]format=rgba,scale={proj_w}:{proj_h}:flags=lanczos[sub];[0:v][sub]overlay=0:0:format=auto"
            subprocess.run([get_ffmpeg_cmd(), "-y", "-i", frame_path, "-i", sub_path, "-filter_complex", preview_filter, "-vframes", "1", out_preview], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 6. 通知 UI 线程展示
            QMetaObject.invokeMethod(self, "_show_preview_dialog", Qt.ConnectionType.QueuedConnection, Q_ARG(str, out_preview))

        except Exception as e:
            print(f"预览出错: {e}")
            QMetaObject.invokeMethod(self, "_reset_preview_btn", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(str)
    def _show_preview_dialog(self, img_path):
        self.btn_preview.setText("👁️ 预览")
        self.btn_preview.setEnabled(True)
        if os.path.exists(img_path):
            dlg = QDialog(self)
            dlg.setWindowTitle("字幕位置预览 (按 ESC 退出)")
            dlg.setFixedSize(400, 711)
            dlg.setStyleSheet("background-color: #11111b;")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel()
            pixmap = QPixmap(img_path).scaled(400, 711, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(pixmap)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)
            dlg.exec()

    @pyqtSlot()
    def _reset_preview_btn(self):
        self.btn_preview.setText("👁️ 预览")
        self.btn_preview.setEnabled(True)

class BatchView(QWidget):
    sig_log = pyqtSignal(str, str)
    sig_progress = pyqtSignal(int)
    sig_file_done = pyqtSignal()
    sig_all_done = pyqtSignal()
    sig_table_row_status = pyqtSignal(int, str, str)
    sig_projects_done = pyqtSignal(int, int, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.input_dir = ""
        self.output_dir = ""
        self.project_output_dir = ""
        self.batch_music_path = ""
        self.batch_music_paths = []
        self.batch_assembly_paths = []
        self.smart_queue_groups = []
        self.multi_project_packages = []
        self.task_queue = []
        self.batch_queues = []
        self.current_queue_index = 0
        self._switching_queue = False
        self.current_idx = 0
        self.is_running = False
        self.batch_job_control = CooperativeJobControl()
        self.batch_run_kind = ""

        self.sig_log.connect(self._append_log)
        self.sig_progress.connect(self._update_progress)
        self.sig_file_done.connect(self._on_file_done)
        self.sig_all_done.connect(self._on_all_done)
        self.sig_table_row_status.connect(self._update_table_row_status)
        self.sig_projects_done.connect(self._on_projects_done)

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        queue_shell = QFrame()
        queue_shell.setStyleSheet("QFrame { background-color: #181825; border: 1px solid #313244; border-radius: 8px; }")
        queue_layout = QHBoxLayout(queue_shell)
        queue_layout.setContentsMargins(12, 8, 12, 8)
        queue_layout.setSpacing(8)

        self.queue_tabs = QTabBar()
        self.queue_tabs.setMovable(False)
        self.queue_tabs.setExpanding(False)
        self.queue_tabs.currentChanged.connect(self.switch_batch_queue)
        self.queue_tabs.setStyleSheet("""
            QTabBar::tab { background: #11111b; color: #a6adc8; padding: 8px 14px; margin-right: 4px; border-radius: 6px; font-weight: bold; }
            QTabBar::tab:selected { background: #89b4fa; color: #11111b; }
        """)
        queue_layout.addWidget(self.queue_tabs, stretch=1)

        self.lbl_queue_summary = QLabel("")
        self.lbl_queue_summary.setStyleSheet("color: #a6e3a1; font-weight: bold; border: none;")
        queue_layout.addWidget(self.lbl_queue_summary)

        btn_new_queue = QPushButton("新增队列/项目批次")
        btn_new_queue.setToolTip("多批次统一用队列：队列1放第一组音频/画面/文案，队列2放第二组，最后点全部建工程。")
        btn_delete_queue = QPushButton("删除队列")
        btn_save_queue = QPushButton("保存队列")
        btn_load_queue = QPushButton("调用队列")
        for btn in (btn_new_queue, btn_delete_queue, btn_save_queue, btn_load_queue):
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 7px 10px; border-radius: 6px;")
            queue_layout.addWidget(btn)
        btn_new_queue.clicked.connect(self.add_batch_queue)
        btn_delete_queue.clicked.connect(self.delete_current_queue)
        btn_save_queue.clicked.connect(self.save_current_queue_backup)
        btn_load_queue.clicked.connect(self.load_queue_backup)
        main_layout.addWidget(queue_shell)

        self.global_queue_panel = QFrame()
        self.global_queue_panel.setStyleSheet("QFrame { background-color: #1e1e2e; border: 1px solid #a6e3a1; border-radius: 8px; }")
        global_layout = QHBoxLayout(self.global_queue_panel)
        global_layout.setContentsMargins(12, 6, 12, 6)
        global_layout.setSpacing(8)
        global_layout.addWidget(QLabel("总开关", styleSheet="color: #a6e3a1; font-size: 16px; font-weight: 900; border: none;"))
        self.lbl_global_queue_stats = QLabel("")
        self.lbl_global_queue_stats.setStyleSheet("color: #cdd6f4; border: none;")
        global_layout.addWidget(self.lbl_global_queue_stats, stretch=1)
        self.btn_run_all_queues = QPushButton("全部建工程并导出")
        self.btn_build_all_queues = QPushButton("全部建工程")
        self.btn_run_all_queues.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; padding: 8px 18px; border-radius: 6px;")
        self.btn_build_all_queues.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 8px 18px; border-radius: 6px;")
        self.btn_run_all_queues.clicked.connect(self.start_all_queue_batches)
        self.btn_build_all_queues.clicked.connect(self.start_all_queue_project_builds)
        global_layout.addWidget(self.btn_build_all_queues)
        global_layout.addWidget(self.btn_run_all_queues)
        main_layout.addWidget(self.global_queue_panel)

        top_header = QHBoxLayout()
        top_header.addWidget(QLabel("📦 工业级批量生成引擎", styleSheet="font-size: 19px; font-weight: 900; color: #cdd6f4;"))
        top_header.addStretch()

        # 👑 音频静音控制区
        top_header.addWidget(QLabel("🎵 音频处理:", styleSheet="color: #cba6f7; font-weight: bold;"))
        self.audio_mode = QComboBox()
        self.audio_mode.addItems(["🔈 原声20% + 配音", "🔇 静音原声 (仅配音)", "🔉 混合原声与配音", "🔊 保留原声 (无视配音)"])
        self.audio_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.audio_mode)

        top_header.addWidget(QLabel("原声:", styleSheet="color: #a6adc8; font-weight: bold; margin-left: 6px;"))
        self.video_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.video_volume_slider.setRange(0, 100)
        self.video_volume_slider.setValue(20)
        self.video_volume_slider.setFixedWidth(86)
        self.video_volume_slider.setToolTip("批量生成时视频原声的音量百分比。默认 20%，适合保留一点环境声并突出配音。")
        self.video_volume_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 5px; background: #313244; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #89b4fa; border-radius: 2px; }
            QSlider::handle:horizontal { background: #f9e2af; width: 12px; margin: -4px 0; border-radius: 6px; }
        """)
        self.video_volume_spin = QSpinBox()
        self.video_volume_spin.setRange(0, 100)
        self.video_volume_spin.setValue(20)
        self.video_volume_spin.setSuffix("%")
        self.video_volume_spin.setFixedWidth(62)
        self.video_volume_spin.setToolTip(self.video_volume_slider.toolTip())
        self.video_volume_spin.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 4px; font-weight: bold; border-radius: 5px;")
        self.video_volume_slider.valueChanged.connect(self.video_volume_spin.setValue)
        self.video_volume_spin.valueChanged.connect(self.video_volume_slider.setValue)
        self.audio_mode.currentTextChanged.connect(self._on_audio_mode_changed)
        top_header.addWidget(self.video_volume_slider)
        top_header.addWidget(self.video_volume_spin)

        top_header.addWidget(QLabel("⚡ 性能:", styleSheet="color: #f9e2af; font-weight: bold; margin-left: 8px;"))
        self.performance_mode = QComboBox()
        self.performance_mode.addItems(["标准画质", "轻量模式", "极速模式"])
        self.performance_mode.setToolTip("轻量/极速会降低字幕透明层的超采样，减少内存和 CPU 占用；最终字幕锐度会略低。")
        self.performance_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.performance_mode)

        top_header.addWidget(QLabel("🎨 强制应用字幕预设:", styleSheet="color: #a6e3a1; font-weight: bold; margin-left: 15px;"))
        self.preset_combo = QComboBox()
        self.preset_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        self.preset_combo.setFixedWidth(200)
        self.preset_combo.currentIndexChanged.connect(self.apply_selected_preset_position_to_rows)
        top_header.addWidget(self.preset_combo)

        self.signature_preset_combo = QComboBox()
        self.signature_preset_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        self.signature_preset_combo.setFixedWidth(170)
        self.signature_preset_combo.currentIndexChanged.connect(lambda *_: self._capture_current_queue_state())

        top_header.addWidget(QLabel("字幕Y:", styleSheet="color: #a6adc8; font-weight: bold; margin-left: 8px;"))
        self.global_subtitle_y_spin = QDoubleSpinBox()
        self.global_subtitle_y_spin.setRange(-50.0, 50.0)
        self.global_subtitle_y_spin.setDecimals(1)
        self.global_subtitle_y_spin.setSingleStep(1.0)
        self.global_subtitle_y_spin.setValue(25.0)
        self.global_subtitle_y_spin.setFixedWidth(72)
        self.global_subtitle_y_spin.setToolTip("批量字幕默认高度。点“应用全部”会同步到所有表格行。")
        self.global_subtitle_y_spin.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 4px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.global_subtitle_y_spin)
        self.btn_apply_all_y = QPushButton("应用全部")
        self.btn_apply_all_y.setFixedHeight(30)
        self.btn_apply_all_y.setToolTip("把当前字幕Y高度应用到所有批量行")
        self.btn_apply_all_y.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; padding: 4px 10px; border-radius: 5px;")
        self.btn_apply_all_y.clicked.connect(self.apply_global_subtitle_y_to_rows)
        top_header.addWidget(self.btn_apply_all_y)

        top_header.addWidget(QLabel("✂️ AI断句:", styleSheet="color: #89b4fa; font-weight: bold; margin-left: 15px;"))
        self.chunk_mode = QComboBox()
        self.chunk_mode.addItems(["单字轰炸 (1字/句)", "智能重点短句 (3-4词为主)", "智能听译 (4-7词，适配双行按词)", REFERENCE_NARRATIVE_CHUNK_MODE, LEGACY_NARRATIVE_CHUNK_MODE, "自然短句 (1-4词)", "双词节奏 (2词/句)", "三词短句 (3词/句)", "四词短句 (4词/句)", "短句快闪 (3-5字)", "长句大段 (约10字)"])
        self.chunk_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.chunk_mode)

        top_header.addWidget(QLabel("🎚️ 时间:", styleSheet="color: #cba6f7; font-weight: bold; margin-left: 10px;"))
        self.timing_mode = QComboBox()
        self.timing_mode.addItems(["L Cut (字幕提前进入)", "J Cut (字幕稍后收尾)", "对齐声音 (按停顿)"])
        self.timing_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.timing_mode)

        top_header.addWidget(QLabel("AI\u542c\u8bd1:", styleSheet="color: #a6e3a1; font-weight: bold; margin-left: 10px;"))
        self.ai_transcription_provider_combo = QComboBox()
        self.ai_transcription_provider_combo.addItem("\u81ea\u52a8", None)
        self.ai_transcription_provider_combo.addItem("Groq \u2192 CF", ["groq", "cloudflare"])
        self.ai_transcription_provider_combo.addItem("CF \u2192 Groq", ["cloudflare", "groq"])
        self.ai_transcription_provider_combo.addItem("\u4ec5 Groq", ["groq"])
        self.ai_transcription_provider_combo.addItem("\u4ec5 CF", ["cloudflare"])
        self.ai_transcription_provider_combo.setFixedWidth(116)
        self.ai_transcription_provider_combo.setToolTip("\u5f53\u6b21\u6279\u91cf\u542c\u8bd1\u7684 AI \u670d\u52a1\uff1b\u9009\u81ea\u52a8\u65f6\u6309\u8bbe\u7f6e\u9875\u4f18\u5148\u7ea7\u8fd0\u884c\u3002")
        self.ai_transcription_provider_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.ai_transcription_provider_combo)

        self.btn_set_out_dir = QPushButton("💾 设置全局输出目录")
        self.btn_set_out_dir.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 5px 15px; border-radius: 5px; margin-left: 15px;")
        self.btn_set_out_dir.clicked.connect(self.select_output_dir)
        top_header.addWidget(self.btn_set_out_dir)

        main_layout.addLayout(top_header)

        music_row = QHBoxLayout()
        music_row.setSpacing(8)
        music_row.addWidget(QLabel("🎼 批量配乐:", styleSheet="color: #f9e2af; font-weight: bold;"))
        self.chk_batch_music = QCheckBox("启用")
        self.chk_batch_music.setToolTip("启用后，配乐池会按分配方式写入每条视频，并自动循环/裁切到最终时长。")
        self.chk_batch_music.setStyleSheet("QCheckBox { color: #cdd6f4; font-weight: bold; }")
        self.chk_batch_music.stateChanged.connect(self._on_batch_music_enabled_changed)
        music_row.addWidget(self.chk_batch_music)
        self.btn_select_batch_music = QPushButton("选择配乐池")
        self.btn_select_batch_music.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 5px 12px; border-radius: 5px;")
        self.btn_select_batch_music.clicked.connect(self.select_batch_music)
        music_row.addWidget(self.btn_select_batch_music)
        self.btn_clear_batch_music = QPushButton("清除")
        self.btn_clear_batch_music.setStyleSheet("background-color: #45475a; color: #cdd6f4; font-weight: bold; padding: 5px 10px; border-radius: 5px;")
        self.btn_clear_batch_music.clicked.connect(self.clear_batch_music)
        music_row.addWidget(self.btn_clear_batch_music)
        self.batch_music_mode_combo = QComboBox()
        for label, mode in BATCH_MUSIC_MODES:
            self.batch_music_mode_combo.addItem(label, userData=mode)
        self.batch_music_mode_combo.setFixedWidth(104)
        self.batch_music_mode_combo.setToolTip("多首配乐的分配方式。顺序循环适合批量稳定生产，随机分配适合做变化。")
        self.batch_music_mode_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.batch_music_mode_combo.currentIndexChanged.connect(lambda *_: (self._set_batch_music_controls_enabled(), self._capture_current_queue_state()))
        music_row.addWidget(self.batch_music_mode_combo)
        self.lbl_batch_music = QLabel("未选择；启用后默认匹配每条视频时长")
        self.lbl_batch_music.setStyleSheet("color: #a6adc8; font-size: 12px;")
        music_row.addWidget(self.lbl_batch_music, stretch=1)
        music_row.addWidget(QLabel("音量:", styleSheet="color: #a6adc8; font-weight: bold;"))
        self.batch_music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.batch_music_volume_slider.setRange(0, 100)
        self.batch_music_volume_slider.setValue(35)
        self.batch_music_volume_slider.setFixedWidth(110)
        self.batch_music_volume_slider.setToolTip("批量配乐音量。默认 35%，会和原声/配音一起混音。")
        self.batch_music_volume_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 5px; background: #313244; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #f9e2af; border-radius: 2px; }
            QSlider::handle:horizontal { background: #a6e3a1; width: 12px; margin: -4px 0; border-radius: 6px; }
        """)
        self.batch_music_volume_spin = QSpinBox()
        self.batch_music_volume_spin.setRange(0, 100)
        self.batch_music_volume_spin.setValue(35)
        self.batch_music_volume_spin.setSuffix("%")
        self.batch_music_volume_spin.setFixedWidth(62)
        self.batch_music_volume_spin.setToolTip(self.batch_music_volume_slider.toolTip())
        self.batch_music_volume_spin.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 4px; font-weight: bold; border-radius: 5px;")
        self.batch_music_volume_slider.valueChanged.connect(self.batch_music_volume_spin.setValue)
        self.batch_music_volume_spin.valueChanged.connect(self.batch_music_volume_slider.setValue)
        self.batch_music_volume_slider.valueChanged.connect(lambda *_: self._capture_current_queue_state())
        self.batch_music_volume_spin.valueChanged.connect(lambda *_: self._capture_current_queue_state())
        music_row.addWidget(self.batch_music_volume_slider)
        music_row.addWidget(self.batch_music_volume_spin)
        self.btn_apply_batch_music_all = QPushButton("应用全部队列")
        self.btn_apply_batch_music_all.setToolTip("把当前批量配乐和音量同步到所有队列。调完音量后可再次点击同步。")
        self.btn_apply_batch_music_all.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 5px 12px; border-radius: 5px;")
        self.btn_apply_batch_music_all.clicked.connect(self.apply_batch_music_to_all_queues)
        music_row.addWidget(self.btn_apply_batch_music_all)
        main_layout.addLayout(music_row)
        self._set_batch_music_controls_enabled(False)

        self.lbl_output = QLabel("当前输出路径: 未选择 (将默认存放在原视频同目录)")
        self.lbl_output.setStyleSheet("color: #a6adc8; font-size: 12px;")
        main_layout.addWidget(self.lbl_output)

        project_out_row = QHBoxLayout()
        self.lbl_project_output = QLabel("批量建工程目录: 未选择（默认当前工作区/批量工程_时间）")
        self.lbl_project_output.setStyleSheet("color: #a6adc8; font-size: 12px;")
        btn_project_out = QPushButton("选择工程目录")
        btn_project_out.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 5px 12px; border-radius: 5px;")
        btn_project_out.clicked.connect(self.select_project_output_dir)
        project_out_row.addWidget(self.lbl_project_output, stretch=1)
        project_out_row.addWidget(btn_project_out)
        main_layout.addLayout(project_out_row)

        signature_row = QHBoxLayout()
        signature_row.addWidget(QLabel("✒️ 批量署名模板:", styleSheet="color: #f9e2af; font-weight: bold;"))
        self.signature_preset_combo.setFixedWidth(260)
        signature_row.addWidget(self.signature_preset_combo)
        btn_refresh_signature = QPushButton("刷新署名模板")
        btn_refresh_signature.setStyleSheet("background-color: #313244; color: #f9e2af; font-weight: bold; padding: 5px 12px; border-radius: 5px;")
        btn_refresh_signature.clicked.connect(self.refresh_signature_presets)
        signature_row.addWidget(btn_refresh_signature)
        signature_row.addWidget(QLabel("精修页保存的署名模板会出现在这里，批量建工程时自动写入。", styleSheet="color: #a6adc8; font-size: 12px;"), stretch=1)
        main_layout.addLayout(signature_row)
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.tabs.setStyleSheet("""
            QTabBar::tab { background: #181825; color: #a6adc8; padding: 7px 14px; font-size: 13px; font-weight: bold; border-top-left-radius: 7px; border-top-right-radius: 7px; }
            QTabBar::tab:selected { background: #313244; color: #a6e3a1; }
            QTabWidget::pane { border: 1px solid #313244; border-radius: 8px; background: #181825; }
        """)

        self.tab_table = QWidget()
        self.init_table_tab()
        self.tabs.addTab(self.tab_table, "📑 多选排列 / 表格手工批量")

        self.tab_folder = QWidget()
        self.init_folder_tab()
        self.tabs.addTab(self.tab_folder, "📁 文件夹全自动匹配")

        main_layout.addWidget(self.tabs)

        bottom_layout = QHBoxLayout()
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(72)
        self.log_console.setStyleSheet("background-color: #11111b; color: #a6adc8; font-family: Consolas; font-size: 12px; border: 1px solid #313244; border-radius: 5px; padding: 6px;")
        bottom_layout.addWidget(self.log_console, stretch=1)
        main_layout.addLayout(bottom_layout)

        run_control_row = QHBoxLayout()
        run_control_row.setSpacing(8)
        self.lbl_batch_run_state = QLabel("批量状态：空闲")
        self.lbl_batch_run_state.setStyleSheet("color: #a6adc8; font-weight: bold;")
        self.btn_batch_pause = QPushButton("暂停")
        self.btn_batch_pause.setToolTip("当前任务完成后暂停，不会强行打断正在处理的素材。")
        self.btn_batch_cancel = QPushButton("取消")
        self.btn_batch_cancel.setToolTip("当前任务完成后停止后续队列。")
        self.btn_batch_pause.setEnabled(False)
        self.btn_batch_cancel.setEnabled(False)
        self.btn_batch_pause.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 6px 14px; border-radius: 5px;")
        self.btn_batch_cancel.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; padding: 6px 14px; border-radius: 5px;")
        self.btn_batch_pause.clicked.connect(self.toggle_batch_pause)
        self.btn_batch_cancel.clicked.connect(self.request_batch_cancel)
        run_control_row.addWidget(self.lbl_batch_run_state, stretch=1)
        run_control_row.addWidget(self.btn_batch_pause)
        run_control_row.addWidget(self.btn_batch_cancel)
        main_layout.addLayout(run_control_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid #313244; border-radius: 5px; text-align: center; color: white; font-weight: bold; } QProgressBar::chunk { background-color: #a6e3a1; }")
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.refresh_presets()
        self.refresh_signature_presets()
        self._init_default_queues()

    def apply_theme(self, colors, theme_key=None):
        self._theme_colors = colors
        self._theme_key = theme_key or ""
        apply_tinted_styles(self, colors)
        apply_room_theme_bridge(self, colors)
        for row in self._table_rows():
            row.apply_compact_theme()

    def _new_queue_state(self, name=None):
        idx = len(self.batch_queues) + 1
        return {
            "name": name or f"队列 {idx}",
            "input_dir": "",
            "output_dir": "",
            "project_output_dir": "",
            "active_tab": 0,
            "audio_mode": self.audio_mode.currentText() if hasattr(self, "audio_mode") else "",
            "audio_import_sort_mode": self._audio_import_sort_mode() if hasattr(self, "audio_import_sort_combo") else "natural",
            "video_volume": self.video_volume_percent() if hasattr(self, "video_volume_spin") else 20,
            "music_enabled": bool(getattr(self, "chk_batch_music", None) and self.chk_batch_music.isChecked()),
            "music_path": getattr(self, "batch_music_path", ""),
            "music_paths": self._current_batch_music_paths() if hasattr(self, "batch_music_paths") else [],
            "music_mode": self._batch_music_mode() if hasattr(self, "batch_music_mode_combo") else "cycle",
            "music_volume": self.music_volume_percent() if hasattr(self, "batch_music_volume_spin") else 35,
            "assembly_paths": self._current_batch_assembly_paths() if hasattr(self, "batch_assembly_paths") else [],
            "assembly_count": int(self.batch_assembly_count_spin.value()) if hasattr(self, "batch_assembly_count_spin") else 3,
            "assembly_mode": self._batch_assembly_mode() if hasattr(self, "batch_assembly_mode_combo") else "smart",
            "assembly_priority": self._batch_assembly_priority_mode() if hasattr(self, "batch_assembly_priority_combo") else "auto",
            "smart_queue_enabled": bool(getattr(self, "chk_smart_queue", None) and self.chk_smart_queue.isChecked()),
            "smart_queue_groups": self._current_smart_queue_groups() if hasattr(self, "smart_queue_groups") else [],
            "smart_queue_mode": self._smart_queue_mode() if hasattr(self, "smart_queue_mode_combo") else "cycle",
            "smart_queue_cut_mode": self._smart_queue_cut_mode() if hasattr(self, "smart_queue_cut_combo") else "single",
            "multi_project_enabled": False,
            "multi_project_packages": [],
            "performance_mode": self.performance_mode.currentText() if hasattr(self, "performance_mode") else "",
            "preset_name": self.preset_combo.currentText() if hasattr(self, "preset_combo") else "",
            "signature_preset_name": self.signature_preset_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "signature_preset_combo") else "",
            "subtitle_y": self.batch_subtitle_y() if hasattr(self, "global_subtitle_y_spin") else 25.0,
            "chunk_mode": self.chunk_mode.currentText() if hasattr(self, "chunk_mode") else "",
            "timing_mode": self.timing_mode.currentText() if hasattr(self, "timing_mode") else "",
            "table_rows": [],
        }

    def _init_default_queues(self):
        if not self.batch_queues:
            self.batch_queues.append(self._new_queue_state("队列 1"))
        self._refresh_queue_tabs()
        self.switch_batch_queue(0)

    def _table_rows_state(self):
        rows = []
        if not hasattr(self, "table_layout"):
            return rows
        for row in self._table_rows():
            row.sync_paths_from_fields()
            rows.append({
                "video": row.video_path,
                "audio": row.audio_path,
                "title": row.txt_title.text().strip(),
                "tag": row.txt_tag.text().strip(),
                "text": row.txt_content.toPlainText(),
                "pos_y": float(row.spin_y.value()),
                "status": row.lbl_status.text(),
            })
        return rows

    def _capture_current_queue_state(self):
        if self._switching_queue or not self.batch_queues:
            return
        idx = max(0, min(self.current_queue_index, len(self.batch_queues) - 1))
        state = self.batch_queues[idx]
        state.update({
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "project_output_dir": self.project_output_dir,
            "active_tab": self.tabs.currentIndex() if hasattr(self, "tabs") else 0,
            "audio_mode": self.audio_mode.currentText(),
            "audio_import_sort_mode": self._audio_import_sort_mode(),
            "video_volume": self.video_volume_percent(),
            "music_enabled": bool(self.chk_batch_music.isChecked()) if hasattr(self, "chk_batch_music") else False,
            "music_path": self.batch_music_path,
            "music_paths": self._current_batch_music_paths(),
            "music_mode": self._batch_music_mode(),
            "music_volume": self.music_volume_percent(),
            "assembly_paths": self._current_batch_assembly_paths(),
            "assembly_count": int(self.batch_assembly_count_spin.value()) if hasattr(self, "batch_assembly_count_spin") else 3,
            "assembly_mode": self._batch_assembly_mode(),
            "assembly_priority": self._batch_assembly_priority_mode(),
            "smart_queue_enabled": bool(getattr(self, "chk_smart_queue", None) and self.chk_smart_queue.isChecked()),
            "smart_queue_groups": self._current_smart_queue_groups(),
            "smart_queue_mode": self._smart_queue_mode(),
            "smart_queue_cut_mode": self._smart_queue_cut_mode(),
            "multi_project_enabled": False,
            "multi_project_packages": [],
            "performance_mode": self.performance_mode.currentText(),
            "preset_name": self.preset_combo.currentText(),
            "signature_preset_name": self.signature_preset_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "signature_preset_combo") else "",
            "subtitle_y": self.batch_subtitle_y(),
            "chunk_mode": self.chunk_mode.currentText(),
            "timing_mode": self.timing_mode.currentText(),
            "table_rows": self._table_rows_state(),
        })
        self._update_queue_stats()

    def _clear_table_rows(self):
        if not hasattr(self, "table_layout"):
            return
        while self.table_layout.count():
            item = self.table_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _load_table_rows_state(self, rows):
        self._clear_table_rows()
        rows = rows or [{}]
        for payload in rows:
            row = BatchTaskRow(parent_view=self)
            row.set_video_path(payload.get("video", ""))
            row.set_audio_path(payload.get("audio", ""))
            row.txt_title.setText(payload.get("title", ""))
            row.txt_tag.setText(payload.get("tag", ""))
            row.txt_content.setPlainText(payload.get("text", ""))
            row.spin_y.setValue(float(payload.get("pos_y", self.batch_subtitle_y()) or self.batch_subtitle_y()))
            row.lbl_status.setText(payload.get("status", "待处理"))
            self.table_layout.addWidget(row)
            if hasattr(self, "_theme_colors"):
                apply_tinted_styles(row, self._theme_colors)
                row.apply_compact_theme()

    def _apply_queue_state(self, state):
        self._switching_queue = True
        try:
            self.input_dir = state.get("input_dir", "")
            self.output_dir = state.get("output_dir", "")
            self.project_output_dir = state.get("project_output_dir", "")
            self.lbl_input.setText(self.input_dir or "未选择")
            self.lbl_output.setText(f"当前输出路径: {self.output_dir}" if self.output_dir else "当前输出路径: 未选择 (将默认存放在原视频同目录)")
            self.lbl_project_output.setText(f"批量建工程目录: {self.project_output_dir}" if self.project_output_dir else "批量建工程目录: 未选择（默认当前工作区/批量工程_时间）")
            for combo, key in ((self.audio_mode, "audio_mode"), (self.performance_mode, "performance_mode"), (self.preset_combo, "preset_name"), (self.chunk_mode, "chunk_mode"), (self.timing_mode, "timing_mode")):
                value = state.get(key, "")
                if value:
                    combo.setCurrentText(value)
            if hasattr(self, "signature_preset_combo"):
                sig_name = state.get("signature_preset_name", "")
                sig_idx = self.signature_preset_combo.findData(sig_name, Qt.ItemDataRole.UserRole)
                self.signature_preset_combo.setCurrentIndex(sig_idx if sig_idx >= 0 else 0)
            self._set_audio_import_sort_mode(state.get("audio_import_sort_mode", "natural"))
            self._set_video_volume(int(state.get("video_volume", 20)), enabled=True)
            self._set_batch_music_paths(state.get("music_paths") or state.get("music_path", ""))
            self._set_batch_music_mode(state.get("music_mode", "cycle"))
            self._set_batch_assembly_paths(state.get("assembly_paths", []))
            self._set_batch_assembly_mode(state.get("assembly_mode", "smart"))
            self._set_batch_assembly_priority_mode(state.get("assembly_priority", "auto"))
            if hasattr(self, "batch_assembly_count_spin"):
                self.batch_assembly_count_spin.blockSignals(True)
                self.batch_assembly_count_spin.setValue(max(1, int(state.get("assembly_count", 3) or 3)))
                self.batch_assembly_count_spin.blockSignals(False)
            self._set_batch_assembly_controls_enabled()
            self._set_smart_queue_groups(state.get("smart_queue_groups", []))
            self._set_smart_queue_mode(state.get("smart_queue_mode", "cycle"))
            self._set_smart_queue_cut_mode(state.get("smart_queue_cut_mode", "single"))
            if hasattr(self, "chk_smart_queue"):
                self.chk_smart_queue.blockSignals(True)
                self.chk_smart_queue.setChecked(bool(state.get("smart_queue_enabled", False)))
                self.chk_smart_queue.blockSignals(False)
            self._refresh_smart_queue_controls()
            self._set_multi_project_packages([])
            if hasattr(self, "chk_multi_project_batch"):
                self.chk_multi_project_batch.blockSignals(True)
                self.chk_multi_project_batch.setChecked(False)
                self.chk_multi_project_batch.blockSignals(False)
            self._refresh_multi_project_controls()
            if hasattr(self, "chk_batch_music"):
                self.chk_batch_music.blockSignals(True)
                self.chk_batch_music.setChecked(bool(state.get("music_enabled", False)))
                self.chk_batch_music.blockSignals(False)
            self._set_batch_music_volume(int(state.get("music_volume", 35)))
            self._set_batch_music_controls_enabled()
            self.global_subtitle_y_spin.setValue(float(state.get("subtitle_y", 25.0) or 25.0))
            self.tabs.setCurrentIndex(int(state.get("active_tab", 0) or 0))
            self._load_table_rows_state(state.get("table_rows", []))
        finally:
            self._switching_queue = False
        self._update_queue_stats()

    def _refresh_queue_tabs(self):
        self.queue_tabs.blockSignals(True)
        while self.queue_tabs.count():
            self.queue_tabs.removeTab(0)
        for state in self.batch_queues:
            self.queue_tabs.addTab(state.get("name", "队列"))
        self.queue_tabs.setCurrentIndex(self.current_queue_index)
        self.queue_tabs.blockSignals(False)
        self._update_queue_stats()

    def switch_batch_queue(self, index):
        if self._switching_queue or index < 0 or index >= len(self.batch_queues):
            return
        self._capture_current_queue_state()
        self.current_queue_index = index
        self._apply_queue_state(self.batch_queues[index])

    def add_batch_queue(self):
        if self.is_running:
            return
        self._capture_current_queue_state()
        self.batch_queues.append(self._new_queue_state())
        self.current_queue_index = len(self.batch_queues) - 1
        self._refresh_queue_tabs()
        self._apply_queue_state(self.batch_queues[self.current_queue_index])

    def delete_current_queue(self):
        if self.is_running or len(self.batch_queues) <= 1:
            return
        self.batch_queues.pop(self.current_queue_index)
        self.current_queue_index = max(0, min(self.current_queue_index, len(self.batch_queues) - 1))
        self._refresh_queue_tabs()
        self._apply_queue_state(self.batch_queues[self.current_queue_index])

    def _queue_task_count(self, state):
        if int(state.get("active_tab", 0) or 0) == 1 and state.get("input_dir"):
            try:
                return len([f for f in os.listdir(state["input_dir"]) if f.lower().endswith(MEDIA_EXTS)])
            except Exception:
                return 0
        has_dynamic_media = self._state_has_dynamic_media(state)
        return sum(
            1
            for row in state.get("table_rows", [])
            if self._row_has_batch_content(row) and (has_dynamic_media or (row.get("video") or "").strip())
        )

    def _update_queue_stats(self):
        total = sum(self._queue_task_count(state) for state in self.batch_queues)
        current = self.batch_queues[self.current_queue_index] if self.batch_queues else {}
        current_count = self._queue_task_count(current)
        if hasattr(self, "lbl_queue_summary"):
            self.lbl_queue_summary.setText(f"当前 {current_count} 个任务")
        if hasattr(self, "lbl_global_queue_stats"):
            self.lbl_global_queue_stats.setText(f"{len(self.batch_queues)} 个队列 / 共 {total} 个候选任务")

    def _load_queue_backups(self):
        data = read_json_file(BATCH_QUEUE_BACKUPS_FILE, default=[])
        return data if isinstance(data, list) else []

    def _write_queue_backups(self, backups):
        write_json_file(BATCH_QUEUE_BACKUPS_FILE, backups, indent=2)

    def save_current_queue_backup(self):
        self._capture_current_queue_state()
        state = copy.deepcopy(self.batch_queues[self.current_queue_index])
        name, ok = QInputDialog.getText(self, "保存队列", "备份名称:", text=state.get("name", "队列备份"))
        if not ok or not name.strip():
            return
        state["backup_name"] = name.strip()
        state["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        backups = [item for item in self._load_queue_backups() if item.get("backup_name") != state["backup_name"]]
        backups.append(state)
        self._write_queue_backups(backups)
        self.sig_log.emit(f"队列已保存到备份列表：{state['backup_name']}", "#a6e3a1")

    def load_queue_backup(self):
        backups = self._load_queue_backups()
        if not backups:
            return QMessageBox.information(self, "调用队列", "还没有保存过队列备份。")
        labels = [f"{item.get('backup_name', item.get('name', '队列'))}  {item.get('saved_at', '')}" for item in backups]
        choice, ok = QInputDialog.getItem(self, "调用队列", "选择备份:", labels, 0, False)
        if not ok:
            return
        idx = labels.index(choice)
        self._capture_current_queue_state()
        state = copy.deepcopy(backups[idx])
        state["name"] = state.get("backup_name") or state.get("name") or f"队列 {len(self.batch_queues) + 1}"
        self.batch_queues.append(state)
        self.current_queue_index = len(self.batch_queues) - 1
        self._refresh_queue_tabs()
        self._apply_queue_state(state)

    def _set_video_volume(self, value, enabled=True):
        value = max(0, min(100, int(value)))
        for widget in (getattr(self, "video_volume_slider", None), getattr(self, "video_volume_spin", None)):
            if widget is None:
                continue
            widget.blockSignals(True)
            widget.setValue(value)
            widget.setEnabled(bool(enabled))
            widget.blockSignals(False)

    def _on_audio_mode_changed(self, text):
        if "静音" in text or "替换" in text:
            self._set_video_volume(0, enabled=False)
        elif "保留" in text:
            self._set_video_volume(100, enabled=True)
        elif self.video_volume_spin.value() in (0, 100):
            self._set_video_volume(20, enabled=True)
        else:
            self._set_video_volume(self.video_volume_spin.value(), enabled=True)

    def video_volume_percent(self):
        return int(self.video_volume_spin.value()) if hasattr(self, "video_volume_spin") else 20

    def video_volume_gain(self, task=None):
        value = task.get("video_volume", self.video_volume_percent()) if isinstance(task, dict) else self.video_volume_percent()
        return max(0.0, min(1.0, float(value or 0) / 100.0))

    def _set_batch_music_volume(self, value):
        value = max(0, min(100, int(value)))
        for widget in (getattr(self, "batch_music_volume_slider", None), getattr(self, "batch_music_volume_spin", None)):
            if widget is None:
                continue
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def music_volume_percent(self):
        return int(self.batch_music_volume_spin.value()) if hasattr(self, "batch_music_volume_spin") else 35

    def _valid_batch_music_mode(self, mode):
        valid = {value for _, value in BATCH_MUSIC_MODES}
        return mode if mode in valid else "cycle"

    def _batch_music_mode(self, state=None):
        if isinstance(state, dict):
            return self._valid_batch_music_mode(state.get("music_mode", "cycle"))
        combo = getattr(self, "batch_music_mode_combo", None)
        if combo is None:
            return "cycle"
        return self._valid_batch_music_mode(combo.currentData(Qt.ItemDataRole.UserRole) or "cycle")

    def _set_batch_music_mode(self, mode):
        combo = getattr(self, "batch_music_mode_combo", None)
        if combo is None:
            return
        mode = self._valid_batch_music_mode(mode)
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _music_mode_label(self, mode):
        mode = self._valid_batch_music_mode(mode)
        return next((label for label, value in BATCH_MUSIC_MODES if value == mode), "顺序循环")

    def _normalize_batch_music_paths(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        clean = []
        for path in paths or []:
            p = str(path or "").strip()
            if p and looks_audio_path(p) and p not in clean:
                clean.append(p)
        return clean

    def _set_batch_music_paths(self, paths):
        self.batch_music_paths = self._normalize_batch_music_paths(paths)
        self.batch_music_path = self.batch_music_paths[0] if self.batch_music_paths else ""

    def _current_batch_music_paths(self):
        paths = self._normalize_batch_music_paths(getattr(self, "batch_music_paths", []))
        fallback = getattr(self, "batch_music_path", "")
        if fallback and fallback not in paths:
            paths.insert(0, fallback)
        self.batch_music_paths = paths
        self.batch_music_path = paths[0] if paths else ""
        return paths

    def _batch_music_summary(self, paths, mode):
        names = [os.path.basename(path) for path in paths]
        if not names:
            return ""
        if len(names) == 1:
            return f"1 首 · {names[0]}"
        preview = "、".join(names[:3])
        if len(names) > 3:
            preview += f" 等 {len(names)} 首"
        return f"{len(names)} 首 · {self._music_mode_label(mode)} · {preview}"

    def batch_music_enabled(self):
        payload = self._batch_music_payload()
        return bool(payload.get("enabled"))

    def _set_batch_music_controls_enabled(self, enabled=None):
        checked = bool(getattr(self, "chk_batch_music", None) and self.chk_batch_music.isChecked()) if enabled is None else bool(enabled)
        paths = self._current_batch_music_paths()
        has_path = bool(paths)
        mode = self._batch_music_mode()
        mode_combo = getattr(self, "batch_music_mode_combo", None)
        if mode_combo is not None:
            mode_combo.setEnabled(has_path)
        for widget in (getattr(self, "batch_music_volume_slider", None), getattr(self, "batch_music_volume_spin", None)):
            if widget is not None:
                widget.setEnabled(checked and has_path)
        if getattr(self, "btn_clear_batch_music", None) is not None:
            self.btn_clear_batch_music.setEnabled(has_path)
        if getattr(self, "btn_apply_batch_music_all", None) is not None:
            self.btn_apply_batch_music_all.setEnabled(has_path)
        if getattr(self, "lbl_batch_music", None) is not None:
            if has_path:
                prefix = "已启用" if checked else "已选择未启用"
                self.lbl_batch_music.setText(f"{prefix}: {self._batch_music_summary(paths, mode)}")
            elif checked:
                self.lbl_batch_music.setText("已启用，但还没有选择配乐池")
            else:
                self.lbl_batch_music.setText("未选择；可一次选择多首配乐并批量分配")

    def _on_batch_music_enabled_changed(self, *_):
        self._set_batch_music_controls_enabled()
        self._capture_current_queue_state()

    def select_batch_music(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择批量配乐（可多选）", "", audio_file_filter())
        if not paths:
            return
        self._set_batch_music_paths(paths)
        if hasattr(self, "chk_batch_music"):
            self.chk_batch_music.setChecked(True)
        self._set_batch_music_controls_enabled(True)
        self._capture_current_queue_state()

    def clear_batch_music(self):
        self._set_batch_music_paths([])
        if hasattr(self, "chk_batch_music"):
            self.chk_batch_music.setChecked(False)
        self._set_batch_music_controls_enabled(False)
        self._capture_current_queue_state()

    def apply_batch_music_to_all_queues(self):
        paths = [path for path in self._current_batch_music_paths() if os.path.exists(path)]
        if not paths:
            return QMessageBox.warning(self, "提示", "请先选择一个或多个有效的批量配乐。")
        if hasattr(self, "chk_batch_music") and not self.chk_batch_music.isChecked():
            self.chk_batch_music.setChecked(True)
        self._capture_current_queue_state()
        volume = self.music_volume_percent()
        mode = self._batch_music_mode()
        for state in self.batch_queues:
            state["music_enabled"] = True
            state["music_path"] = paths[0]
            state["music_paths"] = list(paths)
            state["music_mode"] = mode
            state["music_volume"] = volume
        self._update_queue_stats()
        self.sig_log.emit(f"配乐池已同步到全部队列：{len(paths)} 首 · {self._music_mode_label(mode)} @ {volume}%", "#a6e3a1")

    def _batch_music_payload(self, state=None):
        if isinstance(state, dict):
            raw_paths = state.get("music_paths") or state.get("music_path", "")
            enabled = bool(state.get("music_enabled", False))
            mode = self._batch_music_mode(state)
            try:
                volume = int(state.get("music_volume", self.music_volume_percent()))
            except Exception:
                volume = self.music_volume_percent()
        else:
            raw_paths = self._current_batch_music_paths()
            enabled = bool(getattr(self, "chk_batch_music", None) and self.chk_batch_music.isChecked())
            mode = self._batch_music_mode()
            volume = self.music_volume_percent()
        paths = [path for path in self._normalize_batch_music_paths(raw_paths) if os.path.exists(path)]
        volume = max(0, min(100, volume))
        if not enabled or not paths:
            return {"enabled": False, "paths": [], "path": "", "mode": mode, "volume": volume}
        return {"enabled": True, "paths": paths, "path": paths[0], "mode": mode, "volume": volume}

    def _normalize_batch_assembly_paths(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        clean = []
        for raw in paths or []:
            path = str(raw or "").strip()
            if path and os.path.exists(path) and looks_media_path(path) and path not in clean:
                clean.append(path)
        return clean

    def _set_batch_assembly_paths(self, paths):
        self.batch_assembly_paths = self._normalize_batch_assembly_paths(paths)

    def _current_batch_assembly_paths(self):
        self.batch_assembly_paths = self._normalize_batch_assembly_paths(getattr(self, "batch_assembly_paths", []))
        return list(self.batch_assembly_paths)

    def _batch_assembly_count(self, state=None):
        if isinstance(state, dict):
            return max(1, int(state.get("assembly_count", 3) or 3))
        return max(1, int(self.batch_assembly_count_spin.value()) if hasattr(self, "batch_assembly_count_spin") else 3)

    def _batch_assembly_mode(self, state=None):
        if isinstance(state, dict):
            mode = str(state.get("assembly_mode", "smart") or "smart")
            return mode if mode in {"smart", "fixed"} else "smart"
        combo = getattr(self, "batch_assembly_mode_combo", None)
        if combo is None:
            return "smart"
        mode = str(combo.currentData(Qt.ItemDataRole.UserRole) or "smart")
        return mode if mode in {"smart", "fixed"} else "smart"

    def _set_batch_assembly_mode(self, mode):
        combo = getattr(self, "batch_assembly_mode_combo", None)
        if combo is None:
            return
        mode = mode if mode in {"smart", "fixed"} else "smart"
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _valid_batch_assembly_priority_mode(self, mode):
        valid = {value for _, value in BATCH_ASSEMBLY_PRIORITY_MODES}
        mode = str(mode or "auto")
        return mode if mode in valid else "auto"

    def _batch_assembly_priority_mode(self, state=None):
        if isinstance(state, dict):
            return self._valid_batch_assembly_priority_mode(state.get("assembly_priority", "auto"))
        for attr in ("batch_assembly_priority_combo", "smart_queue_priority_combo"):
            combo = getattr(self, attr, None)
            if combo is not None:
                return self._valid_batch_assembly_priority_mode(combo.currentData(Qt.ItemDataRole.UserRole) or "auto")
        return "auto"

    def _set_assembly_priority_combo_value(self, combo, mode):
        if combo is None:
            return
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _set_batch_assembly_priority_mode(self, mode):
        mode = self._valid_batch_assembly_priority_mode(mode)
        for attr in ("batch_assembly_priority_combo", "smart_queue_priority_combo"):
            self._set_assembly_priority_combo_value(getattr(self, attr, None), mode)

    def _on_assembly_priority_changed(self, combo=None):
        if combo is not None:
            mode = self._valid_batch_assembly_priority_mode(combo.currentData(Qt.ItemDataRole.UserRole) or "auto")
        else:
            mode = self._batch_assembly_priority_mode()
        self._set_batch_assembly_priority_mode(mode)
        self._set_batch_assembly_controls_enabled()
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()
    def _assembly_priority_label(self, mode=None):
        mode = self._valid_batch_assembly_priority_mode(mode or self._batch_assembly_priority_mode())
        return next((label for label, value in BATCH_ASSEMBLY_PRIORITY_MODES if value == mode), "自动优先")

    def _assembly_priority_timestamp(self, path):
        try:
            stat = os.stat(path)
            created = float(getattr(stat, "st_ctime", 0.0) or 0.0)
            modified = float(getattr(stat, "st_mtime", 0.0) or 0.0)
            return max(created, modified)
        except Exception:
            return 0.0

    def _resolved_assembly_priority_mode(self, paths, mode=None):
        mode = self._valid_batch_assembly_priority_mode(mode or self._batch_assembly_priority_mode())
        if mode != "auto":
            return mode
        times = [self._assembly_priority_timestamp(path) for path in self._normalize_batch_assembly_paths(paths)]
        times = [value for value in times if value > 0]
        if len(times) < 2 or max(times) - min(times) < 1.0:
            return "none"
        return "new_first"

    def _prioritized_assembly_paths(self, paths, mode=None):
        paths = self._normalize_batch_assembly_paths(paths)
        resolved = self._resolved_assembly_priority_mode(paths, mode)
        if resolved == "none":
            return list(paths)
        indexed = list(enumerate(paths))
        if resolved == "old_first":
            ordered = sorted(indexed, key=lambda item: (self._assembly_priority_timestamp(item[1]), item[0]))
        else:
            ordered = sorted(indexed, key=lambda item: (-self._assembly_priority_timestamp(item[1]), item[0]))
        return [path for _, path in ordered]


    def _select_priority_assembly_combo(self, paths, count, used_combos, rng, priority_mode):
        ordered = self._prioritized_assembly_paths(paths, priority_mode)
        if not ordered:
            return []
        count = max(1, min(int(count or 1), len(ordered)))
        if len(ordered) <= count:
            return ordered
        max_start = max(0, len(ordered) - count)
        primary_starts = list(range(0, max_start + 1, count))
        fallback_starts = [idx for idx in range(0, max_start + 1) if idx not in primary_starts]
        for start in primary_starts + fallback_starts:
            combo = ordered[start:start + count]
            signature = tuple(sorted(combo))
            if signature not in used_combos:
                used_combos.add(signature)
                return combo
        used_combos.clear()
        combo = ordered[:count]
        used_combos.add(tuple(sorted(combo)))
        return combo

    def _smart_assembly_count(self, paths, audio_path="", fallback_target=0.0):
        paths = self._normalize_batch_assembly_paths(paths)
        if not paths:
            return 0
        target = 0.0
        if audio_path and os.path.exists(audio_path):
            target = float(get_exact_duration(audio_path) or 0.0)
        if target <= 0:
            target = float(fallback_target or 0.0)
        if target <= 0:
            return min(3, len(paths))
        min_count = 1
        max_count = min(len(paths), 12)
        ideal_by_time = int(round(target / 5.5))
        if target <= 10:
            ideal_by_time = min(2, ideal_by_time or 1)
        elif target <= 18:
            ideal_by_time = max(2, ideal_by_time)
        count = max(min_count, min(max_count, ideal_by_time))
        return max(1, count)

    def _batch_assembly_summary(self, paths, count):
        paths = self._normalize_batch_assembly_paths(paths)
        count = max(1, min(int(count or 1), len(paths) if paths else 1))
        if not paths:
            return "未选择；选择后会覆盖每行单视频，按配音/工程时长自动拼接"
        mode = self._batch_assembly_mode()
        priority = self._batch_assembly_priority_mode()
        priority_label = "" if priority == "none" else f"，{self._assembly_priority_label(priority)}"
        if mode == "smart":
            return f"{len(paths)} 个素材{priority_label}，智能按音频时长自动决定数量"
        try:
            combo_count = math.comb(len(paths), count) if len(paths) >= count else 1
        except Exception:
            combo_count = 0
        suffix = f"，理论组合 {combo_count} 种" if combo_count else ""
        return f"{len(paths)} 个素材{priority_label}，每条随机抽 {count} 个{suffix}"

    def _set_batch_assembly_controls_enabled(self, *_):
        paths = self._current_batch_assembly_paths()
        if hasattr(self, "btn_clear_batch_assembly"):
            self.btn_clear_batch_assembly.setEnabled(bool(paths))
        if hasattr(self, "batch_assembly_count_spin"):
            self.batch_assembly_count_spin.setEnabled(bool(paths) and self._batch_assembly_mode() == "fixed")
            self.batch_assembly_count_spin.setMaximum(max(1, min(12, len(paths) if paths else 12)))
            if paths and self.batch_assembly_count_spin.value() > len(paths):
                self.batch_assembly_count_spin.setValue(len(paths))
        if hasattr(self, "lbl_batch_assembly"):
            self.lbl_batch_assembly.setText(self._batch_assembly_summary(paths, self._batch_assembly_count()))

    def select_batch_assembly_pool(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择批量随机拼接素材池", "", media_file_filter())
        if not paths:
            return
        self._set_batch_assembly_paths(paths)
        self._set_batch_assembly_controls_enabled()
        self._capture_current_queue_state()
        self.sig_log.emit(self._batch_assembly_summary(self.batch_assembly_paths, self._batch_assembly_count()), "#a6e3a1")

    def clear_batch_assembly_pool(self):
        self._set_batch_assembly_paths([])
        self._set_batch_assembly_controls_enabled()
        self._capture_current_queue_state()

    def _normalize_smart_queue_groups(self, groups):
        if isinstance(groups, dict):
            groups = [{"name": name, "paths": paths} for name, paths in groups.items()]
        clean = []
        used_names = set()
        for idx, group in enumerate(groups or [], start=1):
            if not isinstance(group, dict):
                continue
            name = str(group.get("name") or group.get("label") or f"\u4e3b\u4f53\u7ec4 {idx}").strip() or f"\u4e3b\u4f53\u7ec4 {idx}"
            paths = self._normalize_batch_assembly_paths(group.get("paths") or group.get("files") or [])
            base_name = name
            suffix = 2
            while name in used_names:
                name = f"{base_name} {suffix}"
                suffix += 1
            used_names.add(name)
            clean.append({"name": name, "paths": paths, "enabled": bool(group.get("enabled", True))})
        return clean

    def _set_smart_queue_groups(self, groups):
        self.smart_queue_groups = self._normalize_smart_queue_groups(groups)

    def _current_smart_queue_groups(self):
        self.smart_queue_groups = self._normalize_smart_queue_groups(getattr(self, "smart_queue_groups", []))
        return copy.deepcopy(self.smart_queue_groups)

    def _smart_queue_mode(self, state=None):
        if isinstance(state, dict):
            mode = str(state.get("smart_queue_mode", "cycle") or "cycle")
        else:
            combo = getattr(self, "smart_queue_mode_combo", None)
            mode = str(combo.currentData(Qt.ItemDataRole.UserRole) if combo is not None else "cycle")
        return mode if mode in {"match", "cycle", "random"} else "cycle"

    def _set_smart_queue_mode(self, mode):
        combo = getattr(self, "smart_queue_mode_combo", None)
        if combo is None:
            return
        mode = mode if mode in {"match", "cycle", "random"} else "cycle"
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _smart_queue_cut_mode(self, state=None):
        if isinstance(state, dict):
            mode = str(state.get("smart_queue_cut_mode", "single") or "single")
        else:
            combo = getattr(self, "smart_queue_cut_combo", None)
            mode = str(combo.currentData(Qt.ItemDataRole.UserRole) if combo is not None else "single")
        return mode if mode in {"auto", "single", "parallel", "cross", "sequence"} else "single"

    def _set_smart_queue_cut_mode(self, mode):
        combo = getattr(self, "smart_queue_cut_combo", None)
        if combo is None:
            return
        mode = mode if mode in {"auto", "single", "parallel", "cross", "sequence"} else "single"
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _smart_queue_cut_label(self, mode=None, group_count=0):
        mode = mode or self._smart_queue_cut_mode()
        if mode == "auto":
            if group_count >= 3:
                return "\u81ea\u52a8\u4ea4\u53c9"
            if group_count == 2:
                return "\u81ea\u52a8\u5e73\u884c"
            return "\u5355\u4e3b\u4f53"
        return {"single": "\u5355\u4e3b\u4f53\u968f\u673a", "parallel": "\u5e73\u884c\u526a\u8f91", "cross": "\u4ea4\u53c9\u526a\u8f91", "sequence": "\u5e73\u7eed\u526a\u8f91"}.get(mode, "\u81ea\u52a8\u526a\u8f91")

    def _smart_queue_enabled(self, state=None):
        if isinstance(state, dict):
            return bool(state.get("smart_queue_enabled", False)) and bool(self._active_smart_queue_groups(state.get("smart_queue_groups", [])))
        return bool(getattr(self, "chk_smart_queue", None) and self.chk_smart_queue.isChecked() and self._active_smart_queue_groups(self._current_smart_queue_groups()))

    def _active_smart_queue_groups(self, groups):
        return [
            group for group in self._normalize_smart_queue_groups(groups)
            if group.get("enabled", True) and self._normalize_batch_assembly_paths(group.get("paths", []))
        ]

    def _clear_layout_widgets(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _set_smart_queue_group_enabled(self, index, checked):
        groups = self._current_smart_queue_groups()
        if 0 <= index < len(groups):
            groups[index]["enabled"] = bool(checked)
            self._set_smart_queue_groups(groups)
            self._refresh_smart_queue_controls()
            self._capture_current_queue_state()

    def _rebuild_smart_queue_group_options(self, groups):
        panel = getattr(self, "smart_queue_groups_panel", None)
        grid = getattr(self, "smart_queue_groups_layout", None)
        if panel is None or grid is None:
            return
        groups = self._normalize_smart_queue_groups(groups)
        self._clear_layout_widgets(grid)
        if not groups:
            panel.setVisible(False)
            return
        panel.setVisible(True)
        title = QLabel("\u4e3b\u4f53\u7ec4\u6e05\u5355\uff08\u53ef\u76f4\u63a5\u6539\u540d/\u6362\u7d20\u6750\uff09")
        title.setStyleSheet("color:#a6adc8; font-weight:900; border:none;")
        grid.addWidget(title, 0, 0, 1, 6)
        for idx, group in enumerate(groups):
            paths = self._normalize_batch_assembly_paths(group.get("paths", []))
            row_frame = QFrame()
            row_frame.setStyleSheet("QFrame { background-color:#181825; border:1px solid #313244; border-radius:6px; }")
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(7)

            chk = QCheckBox()
            chk.setChecked(bool(group.get("enabled", True)))
            chk.setToolTip("\u52fe\u9009\u540e\u8fd9\u4e2a\u4e3b\u4f53\u7ec4\u624d\u4f1a\u53c2\u4e0e\u6279\u91cf\u968f\u673a\u3002")
            chk.setStyleSheet("QCheckBox { border:none; } QCheckBox:checked { color:#a6e3a1; }")
            chk.stateChanged.connect(lambda state, row_idx=idx: self._set_smart_queue_group_enabled(row_idx, state == Qt.CheckState.Checked.value))
            row_layout.addWidget(chk)

            name_edit = QLineEdit(str(group.get("name", "")))
            name_edit.setPlaceholderText("\u4e3b\u4f53\u7ec4\u540d")
            name_edit.setMinimumWidth(130)
            name_edit.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:5px; padding:4px 7px; font-weight:800;")
            name_edit.editingFinished.connect(lambda edit=name_edit, row_idx=idx: self._rename_smart_queue_group(row_idx, edit.text()))
            row_layout.addWidget(name_edit, stretch=1)

            count_label = QLabel(f"{len(paths)} \u4e2a\u7d20\u6750" if paths else "\u672a\u9009\u7d20\u6750")
            count_label.setStyleSheet("color:#a6adc8; border:none; min-width:72px;")
            row_layout.addWidget(count_label)

            btn_replace = QPushButton("\u9009\u7d20\u6750" if not paths else "\u6362\u7d20\u6750")
            btn_append = QPushButton("\u8ffd\u52a0")
            btn_remove = QPushButton("\u5220\u9664")
            for btn in (btn_replace, btn_append, btn_remove):
                btn.setStyleSheet("background-color:#313244; color:#cdd6f4; font-weight:800; padding:4px 9px; border-radius:5px; border:none;")
            btn_replace.setToolTip("\u4e3a\u8fd9\u4e2a\u4e3b\u4f53\u7ec4\u9009\u62e9\u4e00\u6279\u7d20\u6750\uff0c\u4f1a\u66ff\u6362\u8be5\u7ec4\u73b0\u6709\u7d20\u6750\u3002")
            btn_append.setToolTip("\u5728\u8fd9\u4e2a\u4e3b\u4f53\u7ec4\u540e\u9762\u8ffd\u52a0\u7d20\u6750\u3002")
            btn_remove.setToolTip("\u5220\u9664\u8fd9\u4e2a\u53ef\u89c6\u5316\u4e3b\u4f53\u7ec4\u3002")
            btn_replace.clicked.connect(lambda _=False, row_idx=idx: self._choose_smart_queue_group_files(row_idx, append=False))
            btn_append.clicked.connect(lambda _=False, row_idx=idx: self._choose_smart_queue_group_files(row_idx, append=True))
            btn_remove.clicked.connect(lambda _=False, row_idx=idx: self._remove_smart_queue_group(row_idx))
            row_layout.addWidget(btn_replace)
            row_layout.addWidget(btn_append)
            row_layout.addWidget(btn_remove)
            grid.addWidget(row_frame, idx + 1, 0, 1, 6)

    def _rename_smart_queue_group(self, index, name):
        groups = self._current_smart_queue_groups()
        if not (0 <= index < len(groups)):
            return
        name = str(name or "").strip() or f"\u4e3b\u4f53\u7ec4 {index + 1}"
        if groups[index].get("name", "") == name:
            return
        groups[index]["name"] = name
        self._set_smart_queue_groups(groups)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()

    def _choose_smart_queue_group_files(self, index, append=False):
        groups = self._current_smart_queue_groups()
        if not (0 <= index < len(groups)):
            return
        title = "\u8ffd\u52a0\u4e3b\u4f53\u7ec4\u7d20\u6750" if append else "\u9009\u62e9\u4e3b\u4f53\u7ec4\u7d20\u6750"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", media_file_filter())
        paths = self._normalize_batch_assembly_paths(paths)
        if not paths:
            return
        if append:
            combined = list(groups[index].get("paths", []))
            for path in paths:
                if path not in combined:
                    combined.append(path)
            paths = combined
        groups[index]["paths"] = paths
        groups[index]["enabled"] = True
        self._set_smart_queue_groups(groups)
        if hasattr(self, "chk_smart_queue") and self._active_smart_queue_groups(groups):
            self.chk_smart_queue.setChecked(True)
        if hasattr(self, "smart_queue_toggle"):
            self.smart_queue_toggle.setChecked(True)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"\u4e3b\u4f53\u7ec4 {groups[index].get('name', '')} \u5df2\u66f4\u65b0\uff1a{len(paths)} \u4e2a\u7d20\u6750", "#a6e3a1")

    def _remove_smart_queue_group(self, index):
        groups = self._current_smart_queue_groups()
        if not (0 <= index < len(groups)):
            return
        removed = groups.pop(index)
        self._set_smart_queue_groups(groups)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"\u5df2\u5220\u9664\u4e3b\u4f53\u7ec4: {removed.get('name', '')}", "#f38ba8")

    def _smart_queue_summary(self, groups=None):
        groups = self._normalize_smart_queue_groups(self.smart_queue_groups if groups is None else groups)
        filled_groups = [group for group in groups if self._normalize_batch_assembly_paths(group.get("paths", []))]
        active_groups = self._active_smart_queue_groups(groups)
        if not groups:
            return "\u672a\u5efa\u4e3b\u4f53\u7ec4\uff1b\u624b\u52a8\u65b0\u589e\u6216\u6309\u6587\u4ef6\u5939\u6210\u7ec4\uff0c\u4e0d\u4f9d\u8d56\u7d20\u6750\u547d\u540d"
        if not filled_groups:
            return f"\u5df2\u5efa {len(groups)} \u7ec4\uff0c\u5148\u5728\u7ec4\u5361\u7247\u91cc\u9009\u7d20\u6750"
        if not active_groups:
            return f"\u5df2\u5efa {len(groups)} \u7ec4\uff0c{len(filled_groups)} \u7ec4\u6709\u7d20\u6750\uff0c\u8bf7\u52fe\u9009\u8981\u53c2\u4e0e\u7684\u4e3b\u4f53\u7ec4"
        parts = [f"{g['name']} {len(g['paths'])}\u4e2a" for g in active_groups[:4]]
        suffix = f" \u7b49 {len(active_groups)} \u7ec4" if len(active_groups) > 4 else ""
        enabled_hint = f" (\u542f\u7528 {len(active_groups)}/{len(filled_groups)} \u4e2a\u6709\u7d20\u6750\u7ec4)" if len(active_groups) != len(filled_groups) else ""
        mode = {"cycle": "\u52fe\u9009\u7ec4\u8f6e\u6362", "random": "\u968f\u673a\u4e3b\u4f53", "match": "\u5173\u952e\u8bcd\u5339\u914d"}.get(self._smart_queue_mode(), "\u52fe\u9009\u7ec4\u8f6e\u6362")
        cut_label = self._smart_queue_cut_label(group_count=len(active_groups))
        return f"{mode} / {cut_label}: " + " / ".join(parts) + suffix + enabled_hint

    def _refresh_smart_queue_controls(self, *_):
        groups = self._current_smart_queue_groups() if hasattr(self, "smart_queue_groups") else []
        active_groups = self._active_smart_queue_groups(groups)
        self._rebuild_smart_queue_group_options(groups)
        enabled = bool(getattr(self, "chk_smart_queue", None) and self.chk_smart_queue.isChecked())
        for widget in (getattr(self, "smart_queue_mode_combo", None), getattr(self, "smart_queue_cut_combo", None), getattr(self, "btn_clear_smart_queue", None)):
            if widget is not None:
                widget.setEnabled(bool(groups))
        for widget in (
            getattr(self, "btn_add_smart_queue_group", None),
            getattr(self, "btn_import_smart_queue_folder", None),
            getattr(self, "btn_import_smart_queue_multi_folder", None),
        ):
            if widget is not None:
                widget.setEnabled(True)
        if getattr(self, "chk_smart_queue", None) is not None and enabled and not active_groups:
            self.chk_smart_queue.blockSignals(True)
            self.chk_smart_queue.setChecked(False)
            self.chk_smart_queue.blockSignals(False)
        if hasattr(self, "lbl_smart_queue_summary"):
            prefix = "\u5df2\u542f\u7528 | " if self._smart_queue_enabled() else ""
            self.lbl_smart_queue_summary.setText(prefix + self._smart_queue_summary(groups))

    def add_smart_queue_group(self):
        groups = self._current_smart_queue_groups()
        base = "\u4e3b\u4f53\u7ec4"
        used = {str(group.get("name", "")) for group in groups}
        idx = len(groups) + 1
        name = f"{base} {idx}"
        while name in used:
            idx += 1
            name = f"{base} {idx}"
        groups.append({"name": name, "paths": [], "enabled": True})
        self._set_smart_queue_groups(groups)
        if hasattr(self, "smart_queue_toggle"):
            self.smart_queue_toggle.setChecked(True)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"\u5df2\u65b0\u589e\u53ef\u89c6\u5316\u4e3b\u4f53\u7ec4: {name}\uff0c\u8bf7\u5728\u7ec4\u5361\u7247\u91cc\u9009\u7d20\u6750\u3002", "#a6e3a1")

    def _media_paths_in_folder(self, folder):
        found = []
        for root, _, files in os.walk(folder):
            for filename in sorted(files, key=natural_sort_key):
                path = os.path.join(root, filename)
                if looks_media_path(path) and os.path.exists(path) and path not in found:
                    found.append(path)
        return found

    def _smart_queue_groups_from_folders(self, folders):
        new_groups = []
        seen = set()
        for folder in folders:
            if not folder or not os.path.isdir(folder):
                continue
            folder_key = os.path.normcase(os.path.abspath(folder))
            if folder_key in seen:
                continue
            seen.add(folder_key)
            paths = self._media_paths_in_folder(folder)
            if paths:
                new_groups.append({
                    "name": os.path.basename(os.path.normpath(folder)) or "\u4e3b\u4f53\u7ec4",
                    "paths": paths,
                    "enabled": True,
                })
        return new_groups

    def _append_smart_queue_groups(self, new_groups, source_label):
        if not new_groups:
            return QMessageBox.information(
                self,
                "\u6ca1\u6709\u7d20\u6750",
                "\u9009\u4e2d\u7684\u6587\u4ef6\u5939\u91cc\u6ca1\u6709\u627e\u5230\u53ef\u7528\u7684\u89c6\u9891/\u56fe\u7247\u7d20\u6750\u3002",
            )
        groups = self._current_smart_queue_groups() + new_groups
        self._set_smart_queue_groups(groups)
        if hasattr(self, "chk_smart_queue"):
            self.chk_smart_queue.setChecked(True)
        if hasattr(self, "smart_queue_toggle"):
            self.smart_queue_toggle.setChecked(True)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"\u5df2\u4ece{source_label}\u751f\u6210 {len(new_groups)} \u4e2a\u4e3b\u4f53\u7ec4\u3002", "#a6e3a1")

    def _select_smart_queue_subject_folders(self):
        dialog = QFileDialog(self, "\u591a\u9009\u4e3b\u4f53\u6587\u4ef6\u5939")
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        for view in dialog.findChildren(QAbstractItemView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        return [folder for folder in dialog.selectedFiles() if os.path.isdir(folder)]

    def import_smart_queue_groups_from_selected_folders(self):
        folders = self._select_smart_queue_subject_folders()
        if not folders:
            return
        self._append_smart_queue_groups(
            self._smart_queue_groups_from_folders(folders),
            "\u591a\u9009\u6587\u4ef6\u5939",
        )

    def import_smart_queue_groups_from_folder(self):
        root = QFileDialog.getExistingDirectory(self, "\u9009\u62e9\u4e3b\u4f53\u5206\u7ec4\u7236\u6587\u4ef6\u5939")
        if not root:
            return
        new_groups = []
        for name in sorted(os.listdir(root), key=natural_sort_key):
            folder = os.path.join(root, name)
            if not os.path.isdir(folder):
                continue
            paths = self._media_paths_in_folder(folder)
            if paths:
                new_groups.append({"name": name, "paths": paths, "enabled": True})
        if not new_groups:
            paths = self._media_paths_in_folder(root)
            if paths:
                new_groups.append({"name": os.path.basename(root) or "\u4e3b\u4f53\u7ec4", "paths": paths, "enabled": True})
        self._append_smart_queue_groups(new_groups, "\u6587\u4ef6\u5939")

    def clear_smart_queue_groups(self):
        self._set_smart_queue_groups([])
        if hasattr(self, "chk_smart_queue"):
            self.chk_smart_queue.setChecked(False)
        self._refresh_smart_queue_controls()
        self._capture_current_queue_state()

    def _smart_queue_keywords(self, name):
        raw = str(name or "").strip().lower()
        compact = re.sub(r"[\s_\-\|/\\]+", "", raw)
        candidates = {raw, compact}
        for token in re.split("[,\\uFF0C;\\uFF1B/|\\s_\\-]+", raw):
            token = token.strip()
            if token:
                candidates.add(token)
        base = re.sub(r"(\u7d20\u6750|\u89c6\u9891|\u4e3b\u4f53|\u4eba\u7269|\u5206\u7ec4|\u961f\u5217|\u7ec4)$", "", compact)
        if base:
            candidates.add(base)
        return [item for item in candidates if len(item) >= 2]

    def _select_smart_queue_group(self, groups, row, row_index, rng, mode):
        groups = [group for group in self._normalize_smart_queue_groups(groups) if self._normalize_batch_assembly_paths(group.get("paths", []))]
        if not groups:
            return None
        if len(groups) == 1:
            return groups[0]
        mode = mode if mode in {"match", "cycle", "random"} else "cycle"
        if mode == "random":
            return rng.choice(groups)
        haystack_parts = [row.get("title", ""), row.get("text", ""), file_stem(row.get("video", "")), file_stem(row.get("audio", ""))]
        haystack = " ".join(str(part or "") for part in haystack_parts).lower()
        haystack_compact = re.sub(r"[\s_\-\|/\\]+", "", haystack)
        if mode == "match":
            for group in groups:
                for keyword in self._smart_queue_keywords(group.get("name", "")):
                    if keyword and (keyword in haystack or keyword in haystack_compact):
                        return group
        return groups[row_index % len(groups)]

    def _smart_queue_group_order(self, groups, row, row_index, rng, mode):
        groups = [group for group in self._normalize_smart_queue_groups(groups) if self._normalize_batch_assembly_paths(group.get("paths", []))]
        if not groups:
            return []
        selected = self._select_smart_queue_group(groups, row, row_index, rng, mode)
        if not selected:
            return groups
        selected_name = selected.get("name", "")
        try:
            start_idx = next(i for i, group in enumerate(groups) if group.get("name", "") == selected_name)
        except StopIteration:
            start_idx = 0
        ordered = groups[start_idx:] + groups[:start_idx]
        if mode == "random" and len(ordered) > 2:
            head, rest = ordered[0], ordered[1:]
            rng.shuffle(rest)
            ordered = [head] + rest
        return ordered

    def _smart_queue_groups_for_cut(self, groups, row, row_index, rng, select_mode, cut_mode):
        ordered = self._smart_queue_group_order(groups, row, row_index, rng, select_mode)
        if not ordered:
            return []
        cut_mode = cut_mode if cut_mode in {"auto", "single", "parallel", "cross", "sequence"} else "auto"
        if cut_mode == "auto":
            if len(ordered) >= 3:
                cut_mode = "cross"
            elif len(ordered) == 2:
                cut_mode = "parallel"
            else:
                cut_mode = "single"
        if cut_mode == "single":
            return ordered[:1]
        if cut_mode == "parallel":
            return ordered[:2]
        if cut_mode in {"cross", "sequence"}:
            return ordered
        return ordered[:1]

    def _select_one_smart_queue_clip(self, group, used_clips, rng, priority_mode="none"):
        paths = self._normalize_batch_assembly_paths(group.get("paths", []))
        resolved_priority = self._resolved_assembly_priority_mode(paths, priority_mode)
        if resolved_priority != "none":
            paths = self._prioritized_assembly_paths(paths, resolved_priority)
        if not paths:
            return ""
        key = group.get("name", "__group__")
        used = used_clips.setdefault(key, set())
        available = [path for path in paths if path not in used]
        if not available:
            used.clear()
            available = list(paths)
        choice = available[0] if resolved_priority != "none" else rng.choice(available)
        used.add(choice)
        return choice

    def _select_smart_queue_clips(self, groups, count, used_clips, rng, cut_mode, priority_mode="none"):
        groups = [group for group in self._normalize_smart_queue_groups(groups) if self._normalize_batch_assembly_paths(group.get("paths", []))]
        if not groups:
            return []
        if len(groups) == 1:
            combo_used = used_clips.setdefault(f"combo::{groups[0].get('name', '__group__')}", set())
            return self._select_assembly_combo(groups[0].get("paths", []), count, combo_used, rng, priority_mode)
        total_paths = sum(len(self._normalize_batch_assembly_paths(group.get("paths", []))) for group in groups)
        if total_paths <= 0:
            return []
        resolved = cut_mode if cut_mode in {"parallel", "cross", "sequence"} else ("cross" if len(groups) >= 3 else "parallel")
        count = max(1, min(int(count or 1), total_paths))
        if resolved == "parallel":
            count = max(count, min(2, total_paths))
            active_groups = groups[:2]
            slots = [active_groups[i % len(active_groups)] for i in range(count)]
        elif resolved == "sequence":
            count = max(count, min(len(groups), total_paths))
            base = count // len(groups)
            extra = count % len(groups)
            slots = []
            for idx, group in enumerate(groups):
                slots.extend([group] * (base + (1 if idx < extra else 0)))
        else:
            count = max(count, min(3, len(groups), total_paths))
            slots = [groups[i % len(groups)] for i in range(count)]
        clips = []
        for group in slots:
            clip = self._select_one_smart_queue_clip(group, used_clips, rng, priority_mode)
            if clip:
                clips.append(clip)
        return clips

    def _smart_queue_group_names_label(self, groups, cut_mode):
        groups = self._normalize_smart_queue_groups(groups)
        if not groups:
            return ""
        return f"{self._smart_queue_cut_label(cut_mode, len(groups))}:" + "+".join(group.get("name", "") for group in groups)

    def _normalize_audio_paths(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        clean = []
        for raw in paths or []:
            path = str(raw or "").strip()
            if path and os.path.exists(path) and looks_audio_path(path) and path not in clean:
                clean.append(path)
        return sort_audio_paths(clean, self._audio_import_sort_mode())

    def _normalize_multi_project_packages(self, packages):
        clean = []
        used_names = set()
        for idx, package in enumerate(packages or [], start=1):
            if not isinstance(package, dict):
                continue
            name = str(package.get("name") or f"项目 {idx}").strip() or f"项目 {idx}"
            base_name = name
            suffix = 2
            while name in used_names:
                name = f"{base_name} {suffix}"
                suffix += 1
            used_names.add(name)
            script_source = package.get("script_lines") or package.get("texts") or package.get("scripts") or []
            if isinstance(script_source, str):
                script_lines = [line.strip() for line in script_source.splitlines() if line.strip()]
            else:
                script_lines = [str(line or "").strip() for line in script_source if str(line or "").strip()]
            clean.append({
                "name": name,
                "enabled": bool(package.get("enabled", True)),
                "audio_paths": self._normalize_audio_paths(package.get("audio_paths") or package.get("audios") or []),
                "media_paths": self._normalize_batch_assembly_paths(package.get("media_paths") or package.get("video_paths") or package.get("paths") or []),
                "script_lines": script_lines,
            })
        return clean

    def _set_multi_project_packages(self, packages):
        self.multi_project_packages = self._normalize_multi_project_packages(packages)

    def _current_multi_project_packages(self):
        self.multi_project_packages = self._normalize_multi_project_packages(getattr(self, "multi_project_packages", []))
        return copy.deepcopy(self.multi_project_packages)

    def _active_multi_project_packages(self, packages=None):
        return [
            package for package in self._normalize_multi_project_packages(self.multi_project_packages if packages is None else packages)
            if package.get("enabled", True) and self._normalize_batch_assembly_paths(package.get("media_paths", []))
        ]

    def _multi_project_enabled(self, state=None):
        return False

    def _multi_project_summary(self, packages=None):
        packages = self._normalize_multi_project_packages(self.multi_project_packages if packages is None else packages)
        active = self._active_multi_project_packages(packages)
        if not packages:
            return "未建项目包；每个项目包单独选择音频和素材"
        if not active:
            return f"已建 {len(packages)} 个项目包，请给项目包选择素材并勾选启用"
        task_count = sum(len(package.get("audio_paths", [])) if package.get("audio_paths") else 1 for package in active)
        parts = [f"{p['name']} 音频{len(p.get('audio_paths', []))} / 文案{len(p.get('script_lines', []))} / 素材{len(p.get('media_paths', []))}" for p in active[:3]]
        suffix = f" 等 {len(active)} 个项目包" if len(active) > 3 else ""
        return f"{task_count} 个候选工程：" + " / ".join(parts) + suffix

    def _rebuild_multi_project_package_options(self, packages):
        panel = getattr(self, "multi_project_panel", None)
        grid = getattr(self, "multi_project_layout", None)
        if panel is None or grid is None:
            return
        packages = self._normalize_multi_project_packages(packages)
        self._clear_layout_widgets(grid)
        if not packages:
            panel.setVisible(False)
            return
        panel.setVisible(True)
        title = QLabel("项目包列表（每个项目单独绑定音频和素材）")
        title.setStyleSheet("color:#a6adc8; font-weight:900; border:none;")
        grid.addWidget(title, 0, 0, 1, 9)
        for idx, package in enumerate(packages):
            audio_paths = self._normalize_audio_paths(package.get("audio_paths", []))
            media_paths = self._normalize_batch_assembly_paths(package.get("media_paths", []))
            row_frame = QFrame()
            row_frame.setStyleSheet("QFrame { background-color:#181825; border:1px solid #313244; border-radius:6px; }")
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(7)

            chk = QCheckBox()
            chk.setChecked(bool(package.get("enabled", True)))
            chk.setToolTip("勾选后这个项目包才会参与批量建工程。")
            chk.setStyleSheet("QCheckBox { border:none; } QCheckBox:checked { color:#a6e3a1; }")
            chk.stateChanged.connect(lambda state, row_idx=idx: self._set_multi_project_package_enabled(row_idx, state == Qt.CheckState.Checked.value))
            row_layout.addWidget(chk)

            name_edit = QLineEdit(str(package.get("name", "")))
            name_edit.setPlaceholderText("项目名")
            name_edit.setMinimumWidth(118)
            name_edit.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:5px; padding:4px 7px; font-weight:800;")
            name_edit.editingFinished.connect(lambda edit=name_edit, row_idx=idx: self._rename_multi_project_package(row_idx, edit.text()))
            row_layout.addWidget(name_edit, stretch=1)

            script_lines = package.get("script_lines", []) or []
            count_label = QLabel(f"音频 {len(audio_paths)} / 文案 {len(script_lines)} / 素材 {len(media_paths)}")
            count_label.setStyleSheet("color:#a6adc8; border:none; min-width:138px;")
            row_layout.addWidget(count_label)

            btn_audio = QPushButton("选音频" if not audio_paths else "换音频")
            btn_audio_append = QPushButton("追加音频")
            btn_media = QPushButton("选素材" if not media_paths else "换素材")
            btn_media_append = QPushButton("追加素材")
            btn_remove = QPushButton("删除")
            for btn in (btn_audio, btn_audio_append, btn_media, btn_media_append, btn_remove):
                btn.setStyleSheet("background-color:#313244; color:#cdd6f4; font-weight:800; padding:4px 8px; border-radius:5px; border:none;")
            btn_audio.clicked.connect(lambda _=False, row_idx=idx: self._choose_multi_project_package_audio(row_idx, append=False))
            btn_audio_append.clicked.connect(lambda _=False, row_idx=idx: self._choose_multi_project_package_audio(row_idx, append=True))
            btn_media.clicked.connect(lambda _=False, row_idx=idx: self._choose_multi_project_package_media(row_idx, append=False))
            btn_media_append.clicked.connect(lambda _=False, row_idx=idx: self._choose_multi_project_package_media(row_idx, append=True))
            btn_remove.clicked.connect(lambda _=False, row_idx=idx: self._remove_multi_project_package(row_idx))
            row_layout.addWidget(btn_audio)
            row_layout.addWidget(btn_audio_append)
            row_layout.addWidget(btn_media)
            row_layout.addWidget(btn_media_append)
            row_layout.addWidget(btn_remove)
            grid.addWidget(row_frame, idx + 1, 0, 1, 9)

    def _set_multi_project_package_enabled(self, index, checked):
        packages = self._current_multi_project_packages()
        if 0 <= index < len(packages):
            packages[index]["enabled"] = bool(checked)
            self._set_multi_project_packages(packages)
            self._refresh_multi_project_controls()
            self._capture_current_queue_state()

    def _rename_multi_project_package(self, index, name):
        packages = self._current_multi_project_packages()
        if not (0 <= index < len(packages)):
            return
        name = str(name or "").strip() or f"项目 {index + 1}"
        if packages[index].get("name", "") == name:
            return
        packages[index]["name"] = name
        self._set_multi_project_packages(packages)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()

    def _choose_multi_project_package_audio(self, index, append=False):
        packages = self._current_multi_project_packages()
        if not (0 <= index < len(packages)):
            return
        title = "追加项目音频" if append else "选择项目音频"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", audio_file_filter())
        paths = self._normalize_audio_paths(paths)
        if not paths:
            return
        if append:
            combined = list(packages[index].get("audio_paths", []))
            for path in paths:
                if path not in combined:
                    combined.append(path)
            paths = combined
        packages[index]["audio_paths"] = self._normalize_audio_paths(paths)
        packages[index]["enabled"] = True
        self._set_multi_project_packages(packages)
        if hasattr(self, "chk_multi_project_batch"):
            self.chk_multi_project_batch.setChecked(True)
        if hasattr(self, "multi_project_toggle"):
            self.multi_project_toggle.setChecked(True)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()

    def _choose_multi_project_package_media(self, index, append=False):
        packages = self._current_multi_project_packages()
        if not (0 <= index < len(packages)):
            return
        title = "追加项目素材" if append else "选择项目素材"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", media_file_filter())
        paths = self._normalize_batch_assembly_paths(paths)
        if not paths:
            return
        if append:
            combined = list(packages[index].get("media_paths", []))
            for path in paths:
                if path not in combined:
                    combined.append(path)
            paths = combined
        packages[index]["media_paths"] = self._normalize_batch_assembly_paths(paths)
        packages[index]["enabled"] = True
        self._set_multi_project_packages(packages)
        if hasattr(self, "chk_multi_project_batch"):
            self.chk_multi_project_batch.setChecked(True)
        if hasattr(self, "multi_project_toggle"):
            self.multi_project_toggle.setChecked(True)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()

    def _remove_multi_project_package(self, index):
        packages = self._current_multi_project_packages()
        if not (0 <= index < len(packages)):
            return
        removed = packages.pop(index)
        self._set_multi_project_packages(packages)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"已删除项目包: {removed.get('name', '')}", "#f38ba8")

    def _refresh_multi_project_controls(self, *_):
        packages = self._current_multi_project_packages() if hasattr(self, "multi_project_packages") else []
        active = self._active_multi_project_packages(packages)
        self._rebuild_multi_project_package_options(packages)
        enabled = bool(getattr(self, "chk_multi_project_batch", None) and self.chk_multi_project_batch.isChecked())
        for widget in (
            getattr(self, "btn_add_multi_project", None),
            getattr(self, "btn_import_multi_project_folder", None),
            getattr(self, "btn_import_multi_project_multi_folder", None),
        ):
            if widget is not None:
                widget.setEnabled(True)
        if hasattr(self, "btn_clear_multi_project"):
            self.btn_clear_multi_project.setEnabled(bool(packages))
        if getattr(self, "chk_multi_project_batch", None) is not None and enabled and not active:
            self.chk_multi_project_batch.blockSignals(True)
            self.chk_multi_project_batch.setChecked(False)
            self.chk_multi_project_batch.blockSignals(False)
            enabled = False
        if hasattr(self, "lbl_multi_project_summary"):
            prefix = "已启用 | " if self._multi_project_enabled() else ""
            self.lbl_multi_project_summary.setText(prefix + self._multi_project_summary(packages))
        self._refresh_multi_project_script_controls()
        self._update_queue_stats()

    def add_multi_project_package(self):
        packages = self._current_multi_project_packages()
        used = {str(package.get("name", "")) for package in packages}
        idx = len(packages) + 1
        name = f"项目 {idx}"
        while name in used:
            idx += 1
            name = f"项目 {idx}"
        packages.append({"name": name, "audio_paths": [], "media_paths": [], "enabled": True})
        self._set_multi_project_packages(packages)
        if hasattr(self, "multi_project_toggle"):
            self.multi_project_toggle.setChecked(True)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"已新增项目包: {name}，请在卡片里选音频和素材。", "#a6e3a1")

    def _audio_paths_in_folder(self, folder):
        found = []
        for root, _, files in os.walk(folder):
            for filename in sorted(files, key=natural_sort_key):
                path = os.path.join(root, filename)
                if looks_audio_path(path) and os.path.exists(path) and path not in found:
                    found.append(path)
        return self._normalize_audio_paths(found)

    def _multi_project_package_from_folder(self, folder):
        return {
            "name": os.path.basename(os.path.normpath(folder)) or "项目",
            "audio_paths": self._audio_paths_in_folder(folder),
            "media_paths": self._media_paths_in_folder(folder),
            "enabled": True,
        }

    def _append_multi_project_packages(self, new_packages, source_label):
        new_packages = [package for package in self._normalize_multi_project_packages(new_packages) if package.get("audio_paths") or package.get("media_paths")]
        if not new_packages:
            return QMessageBox.information(self, "没有素材", "选中的文件夹里没有找到可用音频或视频/图片素材。")
        packages = self._current_multi_project_packages() + new_packages
        self._set_multi_project_packages(packages)
        if hasattr(self, "chk_multi_project_batch"):
            self.chk_multi_project_batch.setChecked(True)
        if hasattr(self, "multi_project_toggle"):
            self.multi_project_toggle.setChecked(True)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"已从{source_label}生成 {len(new_packages)} 个项目包。", "#a6e3a1")

    def _select_multi_project_folders(self):
        dialog = QFileDialog(self, "多选项目文件夹")
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        for view in dialog.findChildren(QAbstractItemView):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        return [folder for folder in dialog.selectedFiles() if os.path.isdir(folder)]

    def import_multi_project_packages_from_selected_folders(self):
        folders = self._select_multi_project_folders()
        if not folders:
            return
        self._append_multi_project_packages([self._multi_project_package_from_folder(folder) for folder in folders], "多选文件夹")

    def import_multi_project_packages_from_folder(self):
        root = QFileDialog.getExistingDirectory(self, "选择项目包父文件夹")
        if not root:
            return
        new_packages = []
        for name in sorted(os.listdir(root), key=natural_sort_key):
            folder = os.path.join(root, name)
            if os.path.isdir(folder):
                package = self._multi_project_package_from_folder(folder)
                if package.get("audio_paths") or package.get("media_paths"):
                    new_packages.append(package)
        if not new_packages:
            package = self._multi_project_package_from_folder(root)
            if package.get("audio_paths") or package.get("media_paths"):
                new_packages.append(package)
        self._append_multi_project_packages(new_packages, "项目文件夹")

    def clear_multi_project_packages(self):
        self._set_multi_project_packages([])
        if hasattr(self, "chk_multi_project_batch"):
            self.chk_multi_project_batch.setChecked(False)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()

    def _multi_project_script_index(self):
        combo = getattr(self, "multi_project_script_package_combo", None)
        if combo is None:
            return -1
        try:
            return int(combo.currentData(Qt.ItemDataRole.UserRole))
        except Exception:
            return -1

    def _refresh_multi_project_script_controls(self):
        combo = getattr(self, "multi_project_script_package_combo", None)
        if combo is None:
            return
        packages = self._current_multi_project_packages()
        current = self._multi_project_script_index()
        combo.blockSignals(True)
        combo.clear()
        for idx, package in enumerate(packages):
            combo.addItem(
                f"{package.get('name', f'项目 {idx + 1}')}  音频{len(package.get('audio_paths', []))} / 文案{len(package.get('script_lines', []))}",
                userData=idx,
            )
        if packages:
            combo.setCurrentIndex(current if 0 <= current < len(packages) else 0)
        combo.blockSignals(False)
        self._load_multi_project_script_editor()

    def _load_multi_project_script_editor(self):
        edit = getattr(self, "multi_project_scripts_edit", None)
        label = getattr(self, "lbl_multi_project_script_hint", None)
        if edit is None:
            return
        packages = self._current_multi_project_packages()
        idx = self._multi_project_script_index()
        if not (0 <= idx < len(packages)):
            edit.setPlainText("")
            edit.setPlaceholderText("先在项目包页新增项目，再回到这里给该项目粘贴文案。")
            if label is not None:
                label.setText("未选择项目包")
            return
        package = packages[idx]
        lines = package.get("script_lines", []) or []
        edit.blockSignals(True)
        edit.setPlainText("\n".join(lines))
        edit.blockSignals(False)
        if label is not None:
            label.setText(f"{package.get('name', '')}: 每行一条文案，会按音频顺序分配；多余音频仍会自动听译。")

    def save_multi_project_script_lines(self):
        edit = getattr(self, "multi_project_scripts_edit", None)
        if edit is None:
            return
        packages = self._current_multi_project_packages()
        idx = self._multi_project_script_index()
        if not (0 <= idx < len(packages)):
            return QMessageBox.information(self, "没有项目包", "请先新增一个项目包。")
        lines = [line.strip() for line in edit.toPlainText().splitlines() if line.strip()]
        packages[idx]["script_lines"] = lines
        self._set_multi_project_packages(packages)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()
        self.sig_log.emit(f"已为 {packages[idx].get('name', '')} 保存 {len(lines)} 条文案。", "#a6e3a1")

    def clear_multi_project_script_lines(self):
        packages = self._current_multi_project_packages()
        idx = self._multi_project_script_index()
        if not (0 <= idx < len(packages)):
            return
        packages[idx]["script_lines"] = []
        self._set_multi_project_packages(packages)
        self._refresh_multi_project_controls()
        self._capture_current_queue_state()

    def _sidecar_text_for_audio(self, audio_path):
        if not audio_path:
            return ""
        folder = os.path.dirname(audio_path)
        stem = file_stem(audio_path)
        for ext in TEXT_EXTS:
            candidate = os.path.join(folder, stem + ext)
            if os.path.exists(candidate):
                return read_text_source(candidate)
        return ""

    def _music_path_for_task(self, payload, task_index):
        if not isinstance(payload, dict) or not payload.get("enabled"):
            return ""
        paths = payload.get("paths") or []
        if not paths:
            return ""
        mode = self._valid_batch_music_mode(payload.get("mode", "cycle"))
        if mode == "first":
            return paths[0]
        if mode == "random":
            return random.choice(paths)
        return paths[int(task_index or 0) % len(paths)]

    def batch_subtitle_y(self):
        return float(self.global_subtitle_y_spin.value()) if hasattr(self, "global_subtitle_y_spin") else 25.0

    def subtitle_render_scale(self, mode_text=None):
        mode = mode_text or (self.performance_mode.currentText() if hasattr(self, "performance_mode") else "标准画质")
        if "极速" in mode:
            return 1.0
        if "轻量" in mode:
            return min(float(SUBTITLE_SUPERSAMPLE), 1.25)
        return float(SUBTITLE_SUPERSAMPLE)

    def open_paste_dialog(self, auto_add=False):
        dialog = QDialog(self)
        dialog.setWindowTitle("📥 智能表格粘贴器")
        dialog.resize(650, 450)
        dialog.setStyleSheet("background-color: #181825;")
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(dialog, self._theme_colors)
        layout = QVBoxLayout(dialog)

        lbl = QLabel("去 Excel / 飞书 / 腾讯文档 选中内容按 Ctrl+C，在这里 Ctrl+V：\n👉 支持表头：视频路径 / 配音路径 / 标签 / 大标题 / 正文 / 字幕Y\n👉 无表头也可用 tag:老人素材 或 #海边 作为标签；正文不填时会盲听音频。")
        lbl.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 14px; line-height: 1.5;")
        layout.addWidget(lbl)

        tb = QTextEdit()
        tb.setStyleSheet("background-color: #11111b; color: #cdd6f4; font-size: 14px; border: 1px solid #313244; border-radius: 5px; padding: 10px;")
        layout.addWidget(tb)

        btn = QPushButton("✅ 解析并填入表格")
        btn.setFixedHeight(45)
        btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; font-size: 16px; border-radius: 5px;")

        def apply_paste():
            content = tb.toPlainText().strip()
            if not content: return
            try:
                lines = list(csv.reader(io.StringIO(content), delimiter='\t'))
            except:
                lines = [line.split('\t') for line in content.split('\n')]

            header_map = detect_table_paste_header(lines[0]) if lines else {}
            header_found = bool(header_map) and (len(header_map) >= 2 or "body" in header_map or "tag" in header_map)
            data_lines = lines[1:] if header_found else lines

            row_widgets = []
            for i in range(self.table_layout.count()):
                w = self.table_layout.itemAt(i).widget()
                if isinstance(w, BatchTaskRow): row_widgets.append(w)

            if auto_add:
                while len(row_widgets) < len(data_lines):
                    self.add_table_row()
                    w = self.table_layout.itemAt(self.table_layout.count()-1).widget()
                    row_widgets.append(w)

            def header_cell(parts, key):
                if not header_found or key not in header_map:
                    return ""
                idx = header_map.get(key, -1)
                return str(parts[idx] or "").strip() if 0 <= idx < len(parts) else ""

            def is_numeric_cell(value):
                try:
                    float(str(value or "").strip().replace(",", "."))
                    return True
                except Exception:
                    return False

            def apply_tag(row_obj, tag_text):
                tag_text = (tag_text or "").strip()
                if tag_text:
                    row_obj.txt_tag.setText(tag_text)

            for i, parts in enumerate(data_lines):
                if i >= len(row_widgets): break
                if not parts: continue
                row_obj = row_widgets[i]
                values = [str(p or "").strip() for p in parts]

                if header_found:
                    video_col = header_cell(parts, "video")
                    audio_col = header_cell(parts, "audio")
                    text_path = header_cell(parts, "text_path")
                    title_text = header_cell(parts, "title")
                    body_text = header_cell(parts, "body")
                    tag_text = prefixed_tag_value(header_cell(parts, "tag")) or header_cell(parts, "tag")
                    y_raw = header_cell(parts, "y")
                    if body_text and looks_text_path(body_text):
                        text_path = body_text
                        body_text = ""
                    loaded_text = read_text_source(text_path) if text_path else ""
                    if loaded_text:
                        body_text = loaded_text
                    if not body_text and audio_col:
                        body_text = self._sidecar_text_for_audio(audio_col)
                    if video_col:
                        row_obj.set_video_path(video_col)
                    if audio_col:
                        row_obj.set_audio_path(audio_col)
                    if y_raw and is_numeric_cell(y_raw):
                        row_obj.spin_y.setValue(float(y_raw.replace(",", ".")))
                    if not title_text and video_col:
                        title_text = os.path.splitext(os.path.basename(video_col))[0]
                    if title_text or video_col:
                        title_text = project_title_from_task(title_text, video_col)
                        if title_text:
                            row_obj.txt_title.setText(title_text)
                    if body_text:
                        row_obj.txt_content.setPlainText(body_text)
                    apply_tag(row_obj, tag_text)
                    continue

                video_col = next((v for v in values if looks_media_path(v)), "")
                audio_col = next((v for v in values if looks_audio_path(v)), "")
                text_col = next((v for v in values if looks_text_path(v)), "")
                tag_text = next((prefixed_tag_value(v) for v in values if prefixed_tag_value(v)), "")
                y_value = None
                for v in values:
                    try:
                        y_value = float(v.replace(",", "."))
                        break
                    except Exception:
                        pass
                plain_values = [
                    v for v in values
                    if v and v not in (video_col, audio_col, text_col) and not prefixed_tag_value(v) and not looks_media_path(v) and not looks_audio_path(v) and not looks_text_path(v) and not is_numeric_cell(v)
                ]

                if video_col or audio_col or text_col:
                    if video_col:
                        row_obj.set_video_path(video_col)
                    if audio_col:
                        row_obj.set_audio_path(audio_col)
                    if y_value is not None:
                        row_obj.spin_y.setValue(y_value)
                    loaded_text = read_text_source(text_col) if text_col else ""
                    title_text = ""
                    body_text = loaded_text
                    if not body_text and audio_col:
                        body_text = self._sidecar_text_for_audio(audio_col)
                    if plain_values:
                        if body_text:
                            title_text = plain_values[0]
                            if len(plain_values) >= 2 and not tag_text:
                                tag_text = plain_values[1]
                        elif len(plain_values) == 1:
                            body_text = plain_values[0]
                        else:
                            title_text = plain_values[0]
                            body_text = "\n".join(plain_values[1:]).strip()
                    if not title_text and video_col:
                        title_text = os.path.splitext(os.path.basename(video_col))[0]
                    title_text = project_title_from_task(title_text, video_col)
                    if title_text:
                        row_obj.txt_title.setText(title_text)
                    if body_text:
                        row_obj.txt_content.setPlainText(body_text)
                    apply_tag(row_obj, tag_text)
                elif len(parts) >= 2:
                    row_obj.txt_title.setText(parts[0].strip())
                    row_obj.txt_content.setPlainText(parts[1].strip())
                    if len(parts) >= 3:
                        apply_tag(row_obj, prefixed_tag_value(parts[2]) or parts[2].strip())
                elif len(parts) == 1:
                    one = parts[0].strip()
                    tag_only = prefixed_tag_value(one)
                    if tag_only:
                        apply_tag(row_obj, tag_only)
                    else:
                        row_obj.txt_content.setPlainText(one)

            dialog.accept()

        btn.clicked.connect(apply_paste)
        layout.addWidget(btn)
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(dialog, self._theme_colors)
        dialog.exec()

    def init_table_tab(self):
        layout = QVBoxLayout(self.tab_table)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        btn_batch_vid = QPushButton("🎞️ 1. 批量选视频"); btn_batch_vid.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; padding: 6px 10px; border-radius: 4px;")
        btn_batch_aud = QPushButton("🎵 2. 批量选音频"); btn_batch_aud.setStyleSheet("background-color: #cba6f7; color: #11111b; font-weight: bold; padding: 6px 10px; border-radius: 4px;")
        btn_paste = QPushButton("📋 3. 从表格/Excel一键粘贴"); btn_paste.setStyleSheet("background-color: #b4befe; color: #11111b; font-weight: bold; padding: 6px 10px; border-radius: 4px;")
        btn_batch_tag = QPushButton("🏷 4. 批量标签"); btn_batch_tag.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 6px 10px; border-radius: 4px;")

        btn_batch_vid.clicked.connect(self.batch_select_videos)
        btn_batch_aud.clicked.connect(self.batch_select_audios)
        btn_paste.clicked.connect(lambda: self.open_paste_dialog(auto_add=True))
        btn_batch_tag.clicked.connect(self.open_batch_tag_dialog)

        btn_start_table = QPushButton("🚀 建工程并导出")
        btn_start_table.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-size: 15px; font-weight: bold; padding: 7px 18px; border-radius: 4px;")
        btn_start_table.clicked.connect(self.start_table_batch)

        btn_build_projects = QPushButton("开始创建工程")
        btn_build_projects.setStyleSheet("background-color: #f9e2af; color: #11111b; font-size: 15px; font-weight: bold; padding: 7px 18px; border-radius: 4px;")
        btn_build_projects.clicked.connect(self.start_table_project_build)

        toolbar.addWidget(btn_batch_vid); toolbar.addWidget(btn_batch_aud); toolbar.addWidget(btn_paste); toolbar.addWidget(btn_batch_tag)
        toolbar.addStretch(); toolbar.addWidget(btn_build_projects); toolbar.addWidget(btn_start_table)
        layout.addLayout(toolbar)

        audio_sort_row = QHBoxLayout()
        audio_sort_row.setSpacing(8)
        audio_sort_row.addWidget(QLabel("🎵 音频导入排序:", styleSheet="color: #cba6f7; font-weight: bold;"))
        self.audio_import_sort_combo = QComboBox()
        for label, mode in BATCH_AUDIO_SORT_MODES:
            self.audio_import_sort_combo.addItem(label, userData=mode)
        self.audio_import_sort_combo.setFixedWidth(184)
        self.audio_import_sort_combo.setToolTip("控制‘批量选音频’和文件夹模式里配音的排列方式。按时间顺序生成的 6 个一组素材，建议选创建/生成时间从早到晚。")
        self.audio_import_sort_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.audio_import_sort_combo.currentIndexChanged.connect(lambda *_: self._capture_current_queue_state())
        audio_sort_row.addWidget(self.audio_import_sort_combo)
        audio_sort_row.addWidget(QLabel("默认按文件名；重复 1-6 编号时可改按时间。", styleSheet="color: #a6adc8; font-size: 12px;"), stretch=1)
        layout.addLayout(audio_sort_row)

        self.batch_workflow_tabs = QTabWidget()
        self.batch_workflow_tabs.setMinimumHeight(120)
        self.batch_workflow_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.batch_workflow_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #313244; border-radius: 8px; background: #11111b; }
            QTabBar::tab { background:#181825; color:#a6adc8; padding:7px 12px; border-top-left-radius:6px; border-top-right-radius:6px; font-weight:800; }
            QTabBar::tab:selected { background:#313244; color:#cdd6f4; }
        """)
        def _scrollable_workflow_page(content_widget):
            scroll_page = QScrollArea()
            scroll_page.setWidgetResizable(True)
            scroll_page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll_page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll_page.setFrameShape(QFrame.Shape.NoFrame)
            scroll_page.setStyleSheet("""
                QScrollArea { border: none; background: #11111b; }
                QScrollBar:vertical { background: #11111b; width: 10px; margin: 2px; }
                QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 28px; }
                QScrollBar::handle:vertical:hover { background: #89b4fa; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """)
            scroll_page.setWidget(content_widget)
            return scroll_page

        assembly_page = QWidget()
        assembly_page_layout = QVBoxLayout(assembly_page)
        assembly_page_layout.setContentsMargins(8, 8, 8, 8)
        assembly_page_layout.setSpacing(6)
        smart_page = QWidget()
        smart_page_layout = QVBoxLayout(smart_page)
        smart_page_layout.setContentsMargins(8, 8, 8, 8)
        smart_page_layout.setSpacing(6)
        multi_page = QWidget()
        multi_page_layout = QVBoxLayout(multi_page)
        multi_page_layout.setContentsMargins(8, 8, 8, 8)
        multi_page_layout.setSpacing(6)
        self.batch_workflow_tabs.addTab(_scrollable_workflow_page(assembly_page), "随机组接")
        self.batch_workflow_tabs.addTab(_scrollable_workflow_page(smart_page), "智能主体")
        self._hidden_multi_project_workflow_page = _scrollable_workflow_page(multi_page)
        self.batch_table_splitter = QSplitter(Qt.Orientation.Vertical)
        self.batch_table_splitter.setChildrenCollapsible(False)
        self.batch_table_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #313244;
                height: 6px;
                border-radius: 3px;
                margin: 3px 0;
            }
            QSplitter::handle:hover { background-color: #89b4fa; }
        """)
        self.batch_table_splitter.addWidget(self.batch_workflow_tabs)

        assembly_row = QHBoxLayout()
        assembly_row.setSpacing(8)
        assembly_row.addWidget(QLabel("🎬 随机组接池:", styleSheet="color: #f9e2af; font-weight: bold;"))
        self.btn_select_batch_assembly = QPushButton("选择拼接素材")
        self.btn_select_batch_assembly.setToolTip("一次选择多个画面素材；建工程时每条任务会随机抽取指定数量进行拼接。")
        self.btn_select_batch_assembly.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 5px 12px; border-radius: 5px;")
        self.btn_select_batch_assembly.clicked.connect(self.select_batch_assembly_pool)
        assembly_row.addWidget(self.btn_select_batch_assembly)
        self.btn_clear_batch_assembly = QPushButton("清空")
        self.btn_clear_batch_assembly.setStyleSheet("background-color: #45475a; color: #cdd6f4; font-weight: bold; padding: 5px 10px; border-radius: 5px;")
        self.btn_clear_batch_assembly.clicked.connect(self.clear_batch_assembly_pool)
        assembly_row.addWidget(self.btn_clear_batch_assembly)
        self.batch_assembly_mode_combo = QComboBox()
        self.batch_assembly_mode_combo.addItem("智能匹配", userData="smart")
        self.batch_assembly_mode_combo.addItem("固定数量", userData="fixed")
        self.batch_assembly_mode_combo.setFixedWidth(92)
        self.batch_assembly_mode_combo.setToolTip("智能匹配会按配音时长自动决定用几个素材；固定数量则按右侧数量抽取。")
        self.batch_assembly_mode_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.batch_assembly_mode_combo.currentIndexChanged.connect(lambda *_: (self._set_batch_assembly_controls_enabled(), self._capture_current_queue_state()))
        assembly_row.addWidget(self.batch_assembly_mode_combo)
        assembly_row.addWidget(QLabel("每条:", styleSheet="color: #a6adc8; font-weight: bold;"))
        self.batch_assembly_count_spin = QSpinBox()
        self.batch_assembly_count_spin.setRange(1, 12)
        self.batch_assembly_count_spin.setValue(3)
        self.batch_assembly_count_spin.setSuffix(" 个")
        self.batch_assembly_count_spin.setFixedWidth(70)
        self.batch_assembly_count_spin.setToolTip("例如素材池 12 个、每条 3 个，就会每个工程随机抽 3 个拼接。")
        self.batch_assembly_count_spin.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 4px; font-weight: bold; border-radius: 5px;")
        self.batch_assembly_count_spin.valueChanged.connect(lambda *_: (self._set_batch_assembly_controls_enabled(), self._capture_current_queue_state()))
        assembly_row.addWidget(self.batch_assembly_count_spin)
        self.lbl_batch_assembly = QLabel("未选择；选择后会覆盖每行单视频，按配音/工程时长自动拼接")
        self.lbl_batch_assembly.setStyleSheet("color: #a6adc8; font-size: 12px;")
        assembly_row.addWidget(self.lbl_batch_assembly, stretch=1)
        assembly_page_layout.addLayout(assembly_row)
        assembly_priority_row = QHBoxLayout()
        assembly_priority_row.setSpacing(8)
        assembly_priority_row.addWidget(QLabel("素材优先级:", styleSheet="color: #a6adc8; font-weight: bold;"))
        self.batch_assembly_priority_combo = QComboBox()
        for label, value in BATCH_ASSEMBLY_PRIORITY_MODES:
            self.batch_assembly_priority_combo.addItem(label, userData=value)
        self.batch_assembly_priority_combo.setFixedWidth(126)
        self.batch_assembly_priority_combo.setToolTip("自动/新素材优先会按文件创建或修改时间排序：新生成素材先进入开场和前半段；不设置优先级则保持原随机抽取。")
        self.batch_assembly_priority_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.batch_assembly_priority_combo.currentIndexChanged.connect(lambda *_, combo=self.batch_assembly_priority_combo: self._on_assembly_priority_changed(combo))
        assembly_priority_row.addWidget(self.batch_assembly_priority_combo)
        hint = QLabel("新素材优先 = 最近生成/修改的素材先做开场；旧素材优先适合倒序复用；不设置就是老随机。")
        hint.setStyleSheet("color: #7f849c; font-size: 12px;")
        assembly_priority_row.addWidget(hint, stretch=1)
        assembly_page_layout.addLayout(assembly_priority_row)
        assembly_page_layout.addStretch(1)

        smart_shell = QFrame()
        smart_shell.setObjectName("SmartQueueShell")
        smart_shell.setStyleSheet("""
            QFrame#SmartQueueShell { background-color: #11111b; border: 1px solid #313244; border-radius: 8px; }
            QFrame#SmartQueueBody, QFrame#SmartQueueGroups { background: transparent; border: none; }
        """)
        smart_shell_layout = QVBoxLayout(smart_shell)
        smart_shell_layout.setContentsMargins(8, 6, 8, 6)
        smart_shell_layout.setSpacing(6)

        smart_header = QHBoxLayout()
        smart_header.setSpacing(8)
        self.smart_queue_toggle = QToolButton()
        self.smart_queue_toggle.setText("\u667a\u80fd\u4e3b\u4f53\u961f\u5217")
        self.smart_queue_toggle.setCheckable(True)
        self.smart_queue_toggle.setChecked(False)
        self.smart_queue_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.smart_queue_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.smart_queue_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.smart_queue_toggle.setStyleSheet("QToolButton { color:#a6e3a1; font-weight:900; border:none; padding:4px 6px; }")
        smart_header.addWidget(self.smart_queue_toggle)

        self.chk_smart_queue = QCheckBox("\u542f\u7528")
        self.chk_smart_queue.setToolTip("\u6309\u4f60\u52fe\u9009\u7684\u4e3b\u4f53\u7ec4\u8f6e\u6362\u6216\u968f\u673a\uff0c\u4e0d\u4f9d\u8d56\u82f1\u6587\u6587\u6848\u6216\u7d20\u6750\u547d\u540d\u3002")
        self.chk_smart_queue.setStyleSheet("QCheckBox { color: #cdd6f4; font-weight: 900; border:none; } QCheckBox:checked { color:#a6e3a1; }")
        self.chk_smart_queue.stateChanged.connect(lambda *_: (self._refresh_smart_queue_controls(), self._capture_current_queue_state()))
        smart_header.addWidget(self.chk_smart_queue)

        self.lbl_smart_queue_summary = QLabel("\u672a\u5efa\u4e3b\u4f53\u7ec4\uff1b\u624b\u52a8\u65b0\u589e\u6216\u6309\u6587\u4ef6\u5939\u6210\u7ec4\uff0c\u4e0d\u4f9d\u8d56\u7d20\u6750\u547d\u540d")
        self.lbl_smart_queue_summary.setStyleSheet("color: #a6adc8; font-size: 12px; border:none;")
        smart_header.addWidget(self.lbl_smart_queue_summary, stretch=1)
        smart_shell_layout.addLayout(smart_header)

        self.smart_queue_body = QFrame()
        self.smart_queue_body.setObjectName("SmartQueueBody")
        smart_body_layout = QVBoxLayout(self.smart_queue_body)
        smart_body_layout.setContentsMargins(0, 0, 0, 0)
        smart_body_layout.setSpacing(6)

        smart_priority_row = QHBoxLayout()
        smart_priority_row.setSpacing(8)
        smart_priority_row.addWidget(QLabel("主体素材优先级:", styleSheet="color:#a6adc8; font-weight:900; border:none;"))
        self.smart_queue_priority_combo = QComboBox()
        for label, value in BATCH_ASSEMBLY_PRIORITY_MODES:
            self.smart_queue_priority_combo.addItem(label, userData=value)
        self.smart_queue_priority_combo.setFixedWidth(126)
        self.smart_queue_priority_combo.setToolTip("控制智能主体组内素材的使用顺序：新素材优先会把最近生成/修改的素材放到开场和前半段；不设置则保持原随机。")
        self.smart_queue_priority_combo.setStyleSheet("background-color:#313244; color:#cdd6f4; padding:5px 8px; font-weight:800; border-radius:5px; border:none;")
        self.smart_queue_priority_combo.currentIndexChanged.connect(lambda *_, combo=self.smart_queue_priority_combo: self._on_assembly_priority_changed(combo))
        smart_priority_row.addWidget(self.smart_queue_priority_combo)
        smart_priority_hint = QLabel("和随机组接池共用同一个优先级，切换队列/保存队列都会保留。")
        smart_priority_hint.setStyleSheet("color:#7f849c; font-size:12px; border:none;")
        smart_priority_row.addWidget(smart_priority_hint, stretch=1)
        smart_body_layout.addLayout(smart_priority_row)

        smart_group_row = QHBoxLayout()
        smart_group_row.setSpacing(8)
        smart_group_row.addWidget(QLabel("1. \u4e3b\u4f53\u7ec4:", styleSheet="color:#f9e2af; font-weight:900; border:none;"))
        self.btn_add_smart_queue_group = QPushButton("\u65b0\u589e\u7a7a\u7ec4")
        self.btn_import_smart_queue_folder = QPushButton("\u6587\u4ef6\u5939\u6210\u7ec4")
        self.btn_import_smart_queue_multi_folder = QPushButton("\u591a\u9009\u6587\u4ef6\u5939\u6210\u7ec4")
        self.btn_clear_smart_queue = QPushButton("\u6e05\u7a7a")
        for btn in (self.btn_add_smart_queue_group, self.btn_import_smart_queue_folder, self.btn_import_smart_queue_multi_folder, self.btn_clear_smart_queue):
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 5px 10px; border-radius: 5px; border:none;")
        self.btn_add_smart_queue_group.clicked.connect(self.add_smart_queue_group)
        self.btn_import_smart_queue_folder.clicked.connect(self.import_smart_queue_groups_from_folder)
        self.btn_import_smart_queue_multi_folder.clicked.connect(self.import_smart_queue_groups_from_selected_folders)
        self.btn_clear_smart_queue.clicked.connect(self.clear_smart_queue_groups)
        smart_group_row.addWidget(self.btn_add_smart_queue_group)
        smart_group_row.addWidget(self.btn_import_smart_queue_folder)
        smart_group_row.addWidget(self.btn_import_smart_queue_multi_folder)
        smart_group_row.addWidget(self.btn_clear_smart_queue)
        smart_group_row.addStretch()
        smart_body_layout.addLayout(smart_group_row)

        self.smart_queue_groups_panel = QFrame()
        self.smart_queue_groups_panel.setObjectName("SmartQueueGroups")
        self.smart_queue_groups_layout = QGridLayout(self.smart_queue_groups_panel)
        self.smart_queue_groups_layout.setContentsMargins(8, 4, 8, 4)
        self.smart_queue_groups_layout.setHorizontalSpacing(8)
        self.smart_queue_groups_layout.setVerticalSpacing(4)
        smart_body_layout.addWidget(self.smart_queue_groups_panel)

        smart_mode_row = QHBoxLayout()
        smart_mode_row.setSpacing(8)
        smart_mode_row.addWidget(QLabel("2. \u5206\u914d\u65b9\u5f0f:", styleSheet="color:#89b4fa; font-weight:900; border:none;"))
        self.smart_queue_mode_combo = QComboBox()
        self.smart_queue_mode_combo.addItem("\u6309\u52fe\u9009\u4e3b\u4f53\u8f6e\u6362", userData="cycle")
        self.smart_queue_mode_combo.addItem("\u968f\u673a\u4e3b\u4f53\u7ec4", userData="random")
        self.smart_queue_mode_combo.addItem("\u6807\u9898\u5173\u952e\u8bcd\u5339\u914d", userData="match")
        self.smart_queue_mode_combo.setFixedWidth(154)
        self.smart_queue_mode_combo.setToolTip("\u9ed8\u8ba4\u6309\u52fe\u9009\u4e3b\u4f53\u8f6e\u6362\uff0c\u4e0d\u9700\u8981\u82f1\u6587\u6587\u6848\u6216\u7d20\u6750\u547d\u540d\uff1b\u5173\u952e\u8bcd\u5339\u914d\u53ea\u4f5c\u4e3a\u9ad8\u7ea7\u9009\u9879\u3002")
        self.smart_queue_mode_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.smart_queue_mode_combo.currentIndexChanged.connect(lambda *_: (self._refresh_smart_queue_controls(), self._capture_current_queue_state()))
        smart_mode_row.addWidget(self.smart_queue_mode_combo)
        smart_mode_row.addStretch()
        smart_body_layout.addLayout(smart_mode_row)

        smart_cut_row = QHBoxLayout()
        smart_cut_row.setSpacing(8)
        smart_cut_row.addWidget(QLabel("3. \u526a\u8f91\u65b9\u5f0f:", styleSheet="color:#cba6f7; font-weight:900; border:none;"))
        self.smart_queue_cut_combo = QComboBox()
        self.smart_queue_cut_combo.addItem("\u5355\u4e3b\u4f53\u968f\u673a", userData="single")
        self.smart_queue_cut_combo.addItem("\u81ea\u52a8\u526a\u8f91", userData="auto")
        self.smart_queue_cut_combo.addItem("\u5e73\u884c\u526a\u8f91", userData="parallel")
        self.smart_queue_cut_combo.addItem("\u4ea4\u53c9\u526a\u8f91", userData="cross")
        self.smart_queue_cut_combo.addItem("\u5e73\u7eed\u526a\u8f91", userData="sequence")
        self.smart_queue_cut_combo.setFixedWidth(128)
        self.smart_queue_cut_combo.setToolTip("\u5355\u4e3b\u4f53\u968f\u673a: \u6bcf\u6761\u5148\u9009\u4e00\u4e2a\u4e3b\u4f53\u7ec4\uff0c\u518d\u4ece\u8be5\u7ec4\u91cc\u6309\u6570\u91cf\u62bd\u591a\u4e2a\u7d20\u6750\uff1b\u81ea\u52a8: 2\u7ec4\u5e73\u884c\uff0c3\u7ec4\u4ee5\u4e0a\u4ea4\u53c9\u3002")
        self.smart_queue_cut_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 8px; font-weight: bold; border-radius: 5px;")
        self.smart_queue_cut_combo.currentIndexChanged.connect(lambda *_: (self._refresh_smart_queue_controls(), self._capture_current_queue_state()))
        smart_cut_row.addWidget(self.smart_queue_cut_combo)
        smart_cut_row.addStretch()
        smart_body_layout.addLayout(smart_cut_row)

        self.smart_queue_body.setVisible(False)
        smart_shell_layout.addWidget(self.smart_queue_body)
        smart_page_layout.addWidget(smart_shell)
        smart_page_layout.addStretch(1)

        def _toggle_smart_queue_body(checked):
            self.smart_queue_body.setVisible(bool(checked))
            self.smart_queue_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            if hasattr(self, "batch_table_splitter"):
                self.batch_table_splitter.setSizes([320 if checked else 180, 640])
        self.smart_queue_toggle.toggled.connect(_toggle_smart_queue_body)
        self._set_batch_assembly_controls_enabled(False)
        self._refresh_smart_queue_controls()

        multi_project_shell = QFrame()
        multi_project_shell.setObjectName("MultiProjectShell")
        multi_project_shell.setStyleSheet("""
            QFrame#MultiProjectShell { background-color: #11111b; border: 1px solid #313244; border-radius: 8px; }
            QFrame#MultiProjectBody, QFrame#MultiProjectPanel { background: transparent; border: none; }
        """)
        multi_project_layout = QVBoxLayout(multi_project_shell)
        multi_project_layout.setContentsMargins(8, 6, 8, 6)
        multi_project_layout.setSpacing(6)

        multi_header = QHBoxLayout()
        multi_header.setSpacing(8)
        self.multi_project_toggle = QToolButton()
        self.multi_project_toggle.setText("批量多项目包")
        self.multi_project_toggle.setCheckable(True)
        self.multi_project_toggle.setChecked(True)
        self.multi_project_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.multi_project_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.multi_project_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.multi_project_toggle.setStyleSheet("QToolButton { color:#f9e2af; font-weight:900; border:none; padding:4px 6px; }")
        multi_header.addWidget(self.multi_project_toggle)

        self.chk_multi_project_batch = QCheckBox("启用")
        self.chk_multi_project_batch.setToolTip("启用后，当前表格页会按项目包生成任务；每个项目包的音频只匹配该项目包自己的素材。")
        self.chk_multi_project_batch.setStyleSheet("QCheckBox { color: #cdd6f4; font-weight: 900; border:none; } QCheckBox:checked { color:#a6e3a1; }")
        self.chk_multi_project_batch.stateChanged.connect(lambda *_: (self._refresh_multi_project_controls(), self._capture_current_queue_state()))
        multi_header.addWidget(self.chk_multi_project_batch)

        self.lbl_multi_project_summary = QLabel("未建项目包；每个项目包单独选择音频和素材")
        self.lbl_multi_project_summary.setStyleSheet("color: #a6adc8; font-size: 12px; border:none;")
        multi_header.addWidget(self.lbl_multi_project_summary, stretch=1)
        multi_project_layout.addLayout(multi_header)

        self.multi_project_body = QFrame()
        self.multi_project_body.setObjectName("MultiProjectBody")
        multi_body_layout = QVBoxLayout(self.multi_project_body)
        multi_body_layout.setContentsMargins(0, 0, 0, 0)
        multi_body_layout.setSpacing(6)
        self.multi_project_inner_tabs = QTabWidget()
        self.multi_project_inner_tabs.setStyleSheet("QTabWidget::pane { border: none; } QTabBar::tab { background:#181825; color:#a6adc8; padding:6px 10px; font-weight:800; } QTabBar::tab:selected { background:#313244; color:#cdd6f4; }")
        multi_package_page = QWidget()
        multi_package_layout = QVBoxLayout(multi_package_page)
        multi_package_layout.setContentsMargins(0, 0, 0, 0)
        multi_package_layout.setSpacing(6)
        multi_script_page = QWidget()
        multi_script_layout = QVBoxLayout(multi_script_page)
        multi_script_layout.setContentsMargins(0, 0, 0, 0)
        multi_script_layout.setSpacing(6)
        self.multi_project_inner_tabs.addTab(multi_package_page, "项目包")
        self.multi_project_inner_tabs.addTab(multi_script_page, "音频/文案")
        multi_body_layout.addWidget(self.multi_project_inner_tabs)

        multi_btn_row = QHBoxLayout()
        multi_btn_row.setSpacing(8)
        multi_btn_row.addWidget(QLabel("项目包:", styleSheet="color:#f9e2af; font-weight:900; border:none;"))
        self.btn_add_multi_project = QPushButton("新增项目")
        self.btn_import_multi_project_folder = QPushButton("项目文件夹成组")
        self.btn_import_multi_project_multi_folder = QPushButton("多选文件夹成项目")
        self.btn_clear_multi_project = QPushButton("清空")
        for btn in (self.btn_add_multi_project, self.btn_import_multi_project_folder, self.btn_import_multi_project_multi_folder, self.btn_clear_multi_project):
            btn.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 5px 10px; border-radius: 5px; border:none;")
        self.btn_add_multi_project.clicked.connect(self.add_multi_project_package)
        self.btn_import_multi_project_folder.clicked.connect(self.import_multi_project_packages_from_folder)
        self.btn_import_multi_project_multi_folder.clicked.connect(self.import_multi_project_packages_from_selected_folders)
        self.btn_clear_multi_project.clicked.connect(self.clear_multi_project_packages)
        multi_btn_row.addWidget(self.btn_add_multi_project)
        multi_btn_row.addWidget(self.btn_import_multi_project_folder)
        multi_btn_row.addWidget(self.btn_import_multi_project_multi_folder)
        multi_btn_row.addWidget(self.btn_clear_multi_project)
        multi_btn_row.addStretch()
        multi_package_layout.addLayout(multi_btn_row)

        multi_project_scroll = QScrollArea()
        multi_project_scroll.setWidgetResizable(True)
        multi_project_scroll.setMaximumHeight(178)
        multi_project_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.multi_project_panel = QFrame()
        self.multi_project_panel.setObjectName("MultiProjectPanel")
        self.multi_project_layout = QGridLayout(self.multi_project_panel)
        self.multi_project_layout.setContentsMargins(8, 4, 8, 4)
        self.multi_project_layout.setHorizontalSpacing(8)
        self.multi_project_layout.setVerticalSpacing(4)
        multi_project_scroll.setWidget(self.multi_project_panel)
        multi_package_layout.addWidget(multi_project_scroll)

        script_row = QHBoxLayout()
        script_row.setSpacing(8)
        script_row.addWidget(QLabel("项目包:", styleSheet="color:#f9e2af; font-weight:900; border:none;"))
        self.multi_project_script_package_combo = QComboBox()
        self.multi_project_script_package_combo.setStyleSheet("background-color:#313244; color:#cdd6f4; padding:5px 8px; font-weight:800; border-radius:5px;")
        self.multi_project_script_package_combo.currentIndexChanged.connect(lambda *_: self._load_multi_project_script_editor())
        script_row.addWidget(self.multi_project_script_package_combo, stretch=1)
        self.btn_save_multi_project_scripts = QPushButton("保存文案")
        self.btn_clear_multi_project_scripts = QPushButton("清空文案")
        for btn in (self.btn_save_multi_project_scripts, self.btn_clear_multi_project_scripts):
            btn.setStyleSheet("background-color:#313244; color:#cdd6f4; font-weight:800; padding:5px 10px; border-radius:5px; border:none;")
        self.btn_save_multi_project_scripts.clicked.connect(self.save_multi_project_script_lines)
        self.btn_clear_multi_project_scripts.clicked.connect(self.clear_multi_project_script_lines)
        script_row.addWidget(self.btn_save_multi_project_scripts)
        script_row.addWidget(self.btn_clear_multi_project_scripts)
        multi_script_layout.addLayout(script_row)
        self.lbl_multi_project_script_hint = QLabel("每行一条文案，按该项目包里的音频顺序分配。")
        self.lbl_multi_project_script_hint.setStyleSheet("color:#a6adc8; font-size:12px; border:none;")
        multi_script_layout.addWidget(self.lbl_multi_project_script_hint)
        self.multi_project_scripts_edit = QTextEdit()
        self.multi_project_scripts_edit.setPlaceholderText("例如：\n第一条音频对应的文案\n第二条音频对应的文案\n第三条音频对应的文案")
        self.multi_project_scripts_edit.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:8px;")
        multi_script_layout.addWidget(self.multi_project_scripts_edit, stretch=1)

        self.multi_project_body.setVisible(True)
        multi_project_layout.addWidget(self.multi_project_body)
        multi_page_layout.addWidget(multi_project_shell)
        multi_page_layout.addStretch(1)

        def _toggle_multi_project_body(checked):
            self.multi_project_body.setVisible(bool(checked))
            self.multi_project_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            if hasattr(self, "batch_table_splitter"):
                self.batch_table_splitter.setSizes([340 if checked else 180, 640])
        self.multi_project_toggle.toggled.connect(_toggle_multi_project_body)
        self._refresh_multi_project_controls()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #11111b; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background: #45475a; border-radius: 5px; min-height: 28px; }
            QScrollBar::handle:vertical:hover { background: #89b4fa; }
            QScrollBar:horizontal { background: #11111b; height: 10px; margin: 2px; }
            QScrollBar::handle:horizontal { background: #45475a; border-radius: 5px; min-width: 42px; }
            QScrollBar::handle:horizontal:hover { background: #89b4fa; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        """)
        self.table_scroll = scroll
        self.table_content = QWidget()
        self.table_content.setMinimumWidth(1180)
        self.table_layout = QVBoxLayout(self.table_content)
        self.table_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.table_layout.setContentsMargins(0, 0, 0, 0)
        self.table_layout.setSpacing(6)
        scroll.setWidget(self.table_content)
        self.batch_table_splitter.addWidget(scroll)
        self.batch_table_splitter.setStretchFactor(0, 0)
        self.batch_table_splitter.setStretchFactor(1, 1)
        self.batch_table_splitter.setSizes([240, 640])
        layout.addWidget(self.batch_table_splitter, stretch=1)

        btn_add_row = QPushButton("➕ 新增空行")
        btn_add_row.setFixedHeight(32)
        btn_add_row.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; padding: 6px; border-radius: 5px;")
        btn_add_row.clicked.connect(self.add_table_row)
        layout.addWidget(btn_add_row)

        self.add_table_row()

    def add_table_row(self):
        row = BatchTaskRow(parent_view=self) # 👑 修复：将父视图传给行，以便获取预设样式
        _, preset_y = self.selected_preset_position()
        row.spin_y.setValue(preset_y)
        self.table_layout.addWidget(row)
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(row, self._theme_colors)
            row.apply_compact_theme()

    def _table_rows(self):
        rows = []
        for i in range(self.table_layout.count()):
            widget = self.table_layout.itemAt(i).widget()
            if isinstance(widget, BatchTaskRow):
                rows.append(widget)
        return rows

    def _ensure_table_rows(self, count):
        rows = self._table_rows()
        while len(rows) < count:
            self.add_table_row()
            rows = self._table_rows()
        return rows

    def _existing_video_paths(self):
        paths = []
        for row in self._table_rows():
            row.sync_paths_from_fields()
            if row.video_path:
                paths.append(row.video_path)
        return paths

    def _batch_tag_target_rows(self):
        rows = self._table_rows()
        active_rows = []
        for row in rows:
            row.sync_paths_from_fields()
            if row.video_path or row.audio_path or row.txt_title.text().strip() or row.txt_content.toPlainText().strip():
                active_rows.append(row)
        return active_rows or rows

    def _clean_batch_tag_text(self, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        return (prefixed_tag_value(raw) or raw.lstrip("#").strip()).strip()

    def _build_batch_tag_sequence(self, raw_text, mode_text, row_count):
        lines = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
        if not lines or row_count <= 0:
            return []
        if "统一" in mode_text:
            tag = self._clean_batch_tag_text(lines[0])
            return [tag] * row_count if tag else []
        if "逐行" in mode_text:
            return [tag for tag in (self._clean_batch_tag_text(line) for line in lines) if tag][:row_count]

        labels = []
        for line in lines:
            text = self._clean_batch_tag_text(line)
            if not text:
                continue
            match = re.match(r"^(.+?)(?:\s*[xX*×]\s*|[,，:：]\s*|\s+)(\d+)\s*$", text)
            if match:
                tag = self._clean_batch_tag_text(match.group(1))
                count = max(0, int(match.group(2)))
            else:
                tag = text
                count = 1
            if tag and count:
                labels.extend([tag] * count)
            if len(labels) >= row_count:
                break
        return labels[:row_count]

    def open_batch_tag_dialog(self):
        rows = self._batch_tag_target_rows()
        if not rows:
            return QMessageBox.information(self, "没有批量行", "请先添加或导入一批视频/音频/文案行。")

        dialog = QDialog(self)
        dialog.setWindowTitle("🏷 批量添加标签")
        dialog.resize(520, 420)
        dialog.setStyleSheet("background-color: #181825;")
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(dialog, self._theme_colors)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        tip = QLabel("不用 Excel 表头也能批量写标签。标签只用于工程/精修顶部提示，不会进入字幕正文。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#f9e2af; font-weight:900; font-size:13px;")
        layout.addWidget(tip)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("写入方式:", styleSheet="color:#cdd6f4; font-weight:bold;"))
        mode_combo = QComboBox()
        mode_combo.addItems(["统一标签到有效行", "逐行标签（一行一个）", "按数量分组（标签 6）"])
        mode_combo.setStyleSheet("background-color:#313244; color:#cdd6f4; padding:6px; border-radius:5px;")
        mode_row.addWidget(mode_combo, stretch=1)
        layout.addLayout(mode_row)

        editor = QTextEdit()
        editor.setPlaceholderText("统一标签示例：\n老人素材\n\n逐行示例：\n老人素材\n小孩素材\n青年素材\n\n分组示例：\n老人素材 6\n小孩素材 6\n海边素材 3")
        editor.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:10px; font-size:13px;")
        layout.addWidget(editor, stretch=1)

        preview = QLabel(f"将写入当前 {len(rows)} 个有效行；空行会自动跳过。")
        preview.setStyleSheet("color:#a6adc8; font-size:12px;")
        layout.addWidget(preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("background-color:#313244; color:#cdd6f4; font-weight:bold; padding:7px 14px; border-radius:5px;")
        btn_apply = QPushButton("应用标签")
        btn_apply.setStyleSheet("background-color:#f9e2af; color:#11111b; font-weight:900; padding:7px 16px; border-radius:5px;")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

        def apply_tags():
            target_rows = self._batch_tag_target_rows()
            labels = self._build_batch_tag_sequence(editor.toPlainText(), mode_combo.currentText(), len(target_rows))
            if not labels:
                return QMessageBox.warning(dialog, "没有标签", "请输入标签。统一模式填一个标签；逐行/分组模式可填多行。")
            applied = 0
            for row, label in zip(target_rows, labels):
                row.txt_tag.setText(label)
                applied += 1
            self._capture_current_queue_state()
            self.sig_log.emit(f"已为 {applied} 行批量写入标签。", "#f9e2af")
            dialog.accept()

        btn_apply.clicked.connect(apply_tags)
        dialog.exec()

    def _audio_row_count(self):
        count = 0
        for row in self._table_rows():
            row.sync_paths_from_fields()
            if row.audio_path:
                count += 1
        return count

    def _last_audio_row_index(self):
        last_idx = -1
        for idx, row in enumerate(self._table_rows()):
            row.sync_paths_from_fields()
            if row.audio_path:
                last_idx = idx
        return last_idx

    def _row_has_batch_content(self, row):
        return bool(
            (row.get("video") or "").strip()
            or (row.get("audio") or "").strip()
            or (row.get("text") or "").strip()
        )

    def _state_has_dynamic_media(self, state):
        if self._normalize_batch_assembly_paths(state.get("assembly_paths", [])):
            return True
        return self._smart_queue_enabled(state)

    def _set_row_title_for_pair(self, row, video_path="", audio_path=""):
        if audio_path:
            row.txt_title.setText(file_stem(audio_path))
        elif video_path and not row.txt_title.text().strip():
            row.txt_title.setText(file_stem(video_path))

    def _valid_audio_import_sort_mode(self, mode):
        valid = {value for _, value in BATCH_AUDIO_SORT_MODES}
        return mode if mode in valid else "natural"

    def _audio_import_sort_mode(self):
        combo = getattr(self, "audio_import_sort_combo", None)
        if combo is None:
            return "natural"
        return self._valid_audio_import_sort_mode(combo.currentData(Qt.ItemDataRole.UserRole) or "natural")

    def _set_audio_import_sort_mode(self, mode):
        combo = getattr(self, "audio_import_sort_combo", None)
        if combo is None:
            return
        mode = self._valid_audio_import_sort_mode(mode)
        idx = combo.findData(mode, Qt.ItemDataRole.UserRole)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _audio_import_sort_label(self, mode=None):
        mode = self._valid_audio_import_sort_mode(mode or self._audio_import_sort_mode())
        return next((label for label, value in BATCH_AUDIO_SORT_MODES if value == mode), "文件名自然排序")

    def _sort_audio_import_paths(self, paths):
        return sort_audio_paths(paths, self._audio_import_sort_mode())

    def batch_select_videos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "批量选择画面", "", media_file_filter())
        if not paths:
            return
        paths = sorted(paths, key=natural_sort_key)
        target_count = max(len(paths), self._audio_row_count())
        rows = self._ensure_table_rows(target_count)
        for i, row in enumerate(rows[:target_count]):
            path = paths[i % len(paths)]
            row.set_video_path(path)
            self._set_row_title_for_pair(row, path, row.audio_path)
        if target_count > len(paths):
            self.sig_log.emit(f"画面素材 {len(paths)} 个，已循环匹配到 {target_count} 行。", "#a6e3a1")

    def batch_select_audios(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "批量选择配音", "", audio_file_filter())
        if not paths:
            return
        paths = self._sort_audio_import_paths(paths)
        self.sig_log.emit(f"配音导入排序：{self._audio_import_sort_label()}，共 {len(paths)} 个。", "#89b4fa")
        video_paths = self._existing_video_paths()
        append_start = self._last_audio_row_index() + 1
        rows = self._ensure_table_rows(append_start + len(paths))
        for i, path in enumerate(paths):
            row = rows[append_start + i]
            if video_paths:
                row.set_video_path(video_paths[i % len(video_paths)])
            row.set_audio_path(path)
            if not row.txt_content.toPlainText().strip():
                sidecar_text = self._sidecar_text_for_audio(path)
                if sidecar_text:
                    row.txt_content.setPlainText(sidecar_text)
            self._set_row_title_for_pair(row, row.video_path, path)
        if video_paths and len(paths) > len(video_paths):
            self.sig_log.emit(f"配音 {len(paths)} 个，画面 {len(video_paths)} 个，已按顺序循环套用画面。", "#a6e3a1")
        if append_start:
            self.sig_log.emit(f"已从第 {append_start + 1} 行继续追加 {len(paths)} 个配音，没有覆盖前面的音频。", "#a6e3a1")

    def init_folder_tab(self):
        layout = QVBoxLayout(self.tab_folder)
        layout.addWidget(QLabel("1. 选择一个包含视频的文件夹，系统会自动扫描并处理。"))
        layout.addWidget(QLabel("2. 如果文件夹内有同名的 .mp3 文件，系统会自动将其作为配音合成。"))

        self.btn_input = QPushButton("📂 选择输入文件夹")
        self.btn_input.setFixedHeight(50)
        self.btn_input.setStyleSheet("background-color: #313244; color: white; font-weight: bold; font-size: 16px; border-radius: 8px;")
        self.btn_input.clicked.connect(self.select_input_dir)
        self.lbl_input = QLabel("未选择")

        btn_start_folder = QPushButton("🚀 扫盘建工程并导出")
        btn_start_folder.setFixedHeight(60)
        btn_start_folder.setStyleSheet("background-color: #f38ba8; color: #11111b; font-size: 18px; font-weight: bold; border-radius: 8px; margin-top: 20px;")
        btn_start_folder.clicked.connect(self.start_folder_batch)

        btn_build_folder_projects = QPushButton("开始创建工程")
        btn_build_folder_projects.setFixedHeight(52)
        btn_build_folder_projects.setStyleSheet("background-color: #f9e2af; color: #11111b; font-size: 16px; font-weight: bold; border-radius: 8px; margin-top: 12px;")
        btn_build_folder_projects.clicked.connect(self.start_folder_project_build)

        layout.addWidget(self.btn_input)
        layout.addWidget(self.lbl_input)
        layout.addStretch()
        layout.addWidget(btn_build_folder_projects)
        layout.addWidget(btn_start_folder)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_external_presets()

    def refresh_external_presets(self, style_name=None, signature_name=None):
        self.refresh_presets(prefer_name=style_name)
        self.refresh_signature_presets(prefer_name=signature_name)

    def refresh_presets(self, prefer_name=None):
        current = self.preset_combo.currentText() if hasattr(self, "preset_combo") else ""
        if current.startswith("未找到预设"):
            current = ""
        target = prefer_name or current
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        presets = read_json_file(PRESETS_FILE, default={})
        if not isinstance(presets, dict):
            presets = {}
        presets = merge_built_in_style_presets(presets)
        if presets:
            self.preset_combo.addItems(list(presets.keys()))
        if self.preset_combo.count() == 0: self.preset_combo.addItem("未找到预设，请先在 Edit 房间保存")
        elif target:
            idx = self.preset_combo.findText(target)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

        self.preset_combo.blockSignals(False)
        self.apply_selected_preset_position_to_rows()

    def refresh_signature_presets(self, prefer_name=None):
        if not hasattr(self, "signature_preset_combo"):
            return
        current = prefer_name or self.signature_preset_combo.currentData(Qt.ItemDataRole.UserRole)
        self.signature_preset_combo.blockSignals(True)
        self.signature_preset_combo.clear()
        self.signature_preset_combo.addItem("不使用署名", userData="")
        for name in load_signature_presets_file().keys():
            self.signature_preset_combo.addItem(name, userData=name)
        idx = self.signature_preset_combo.findData(current, Qt.ItemDataRole.UserRole) if current else 0
        self.signature_preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.signature_preset_combo.blockSignals(False)

    def _load_signature_preset_by_name(self, preset_name):
        if not preset_name:
            return {}
        presets = load_signature_presets_file()
        raw = presets.get(preset_name)
        if not isinstance(raw, dict):
            return {}
        sig = normalize_signature_config(copy.deepcopy(raw))
        sig["enabled"] = True
        return sig

    def _load_selected_signature_config(self):
        if not hasattr(self, "signature_preset_combo"):
            return {}
        return self._load_signature_preset_by_name(self.signature_preset_combo.currentData(Qt.ItemDataRole.UserRole) or "")

    def _load_selected_preset_raw(self):
        preset_name = self.preset_combo.currentText() if hasattr(self, "preset_combo") else ""
        if not preset_name:
            return {}
        try:
            presets = read_json_file(PRESETS_FILE, default={})
            if not isinstance(presets, dict):
                presets = {}
            presets = merge_built_in_style_presets(presets)
            return presets.get(preset_name, {}) if isinstance(presets, dict) else {}
        except Exception:
            return {}

    def selected_preset_position(self, default_x=0.0, default_y=None):
        _, position = split_style_preset(self._load_selected_preset_raw())
        if position:
            return position["pos_x"], position["pos_y"]
        if default_y is None:
            default_y = self.batch_subtitle_y()
        return float(default_x), float(default_y)

    def apply_selected_preset_position_to_rows(self, *args):
        if not hasattr(self, "table_layout"):
            return
        _, preset_y = self.selected_preset_position(default_y=self.batch_subtitle_y())
        if hasattr(self, "global_subtitle_y_spin"):
            self.global_subtitle_y_spin.blockSignals(True)
            self.global_subtitle_y_spin.setValue(preset_y)
            self.global_subtitle_y_spin.blockSignals(False)
        self.apply_global_subtitle_y_to_rows(silent=True)

    def apply_global_subtitle_y_to_rows(self, silent=False):
        if not hasattr(self, "table_layout"):
            return
        value = self.batch_subtitle_y()
        for row in self._table_rows():
            row.spin_y.blockSignals(True)
            row.spin_y.setValue(value)
            row.spin_y.blockSignals(False)
        if not silent:
            self.sig_log.emit(f"字幕高度已批量应用到全部行：Y={value:.1f}", "#a6e3a1")

    def select_input_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含原视频的文件夹")
        if d:
            self.input_dir = d
            self.lbl_input.setText(d)
            self._capture_current_queue_state()

    def select_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择成品保存文件夹")
        if d:
            self.output_dir = d
            self.lbl_output.setText(f"当前输出路径: {d}")
            self._capture_current_queue_state()

    def select_project_output_dir(self):
        workspace = get_active_workspace()
        os.makedirs(workspace, exist_ok=True)
        d = QFileDialog.getExistingDirectory(self, "选择批量工程保存目录", workspace)
        if d:
            self.project_output_dir = d
            self.lbl_project_output.setText(f"批量建工程目录: {d}")
            self._capture_current_queue_state()

    def prepare_project_builder(self, project_dir=None, source_label=""):
        self.refresh_presets()
        if project_dir:
            os.makedirs(project_dir, exist_ok=True)
            self.project_output_dir = project_dir
            self.lbl_project_output.setText(f"批量建工程目录: {project_dir}")
        elif not self.project_output_dir:
            self.lbl_project_output.setText("批量建工程目录: 未选择（默认当前工作区/批量工程_时间）")
        self.tabs.setCurrentIndex(0)
        rows = self._ensure_table_rows(1)
        for row in rows:
            row.lbl_status.setText("待建工程")
            row.lbl_status.setStyleSheet("color: #a6adc8; border: none;")
        self.log_console.clear()
        hint = f"已连接到工程「{source_label}」，可直接选择视频、音频、粘贴文案，再点开始创建工程。" if source_label else "可直接选择视频、音频、粘贴文案，再点开始创建工程。"
        self.sig_log.emit(hint, "#89b4fa")

    @pyqtSlot(str, str)
    def _append_log(self, msg, color):
        self.log_console.append(f"<span style='color:{color}'>{msg}</span>")
        self.log_console.verticalScrollBar().setValue(self.log_console.verticalScrollBar().maximum())

    @pyqtSlot(int)
    def _update_progress(self, val):
        self.progress_bar.setValue(val)

    @property
    def batch_pause_requested(self):
        return self.batch_job_control.pause_requested

    @batch_pause_requested.setter
    def batch_pause_requested(self, value):
        self.batch_job_control.pause_requested = bool(value)

    @property
    def batch_cancel_requested(self):
        return self.batch_job_control.cancel_requested

    @batch_cancel_requested.setter
    def batch_cancel_requested(self, value):
        self.batch_job_control.cancel_requested = bool(value)

    @property
    def batch_finish_reason(self):
        return self.batch_job_control.finish_reason

    @batch_finish_reason.setter
    def batch_finish_reason(self, value):
        self.batch_job_control.finish_reason = str(value or "completed")

    def _set_batch_run_controls(self, running=None, state_text=None):
        active = self.is_running if running is None else bool(running)
        if hasattr(self, "btn_batch_pause"):
            self.btn_batch_pause.setEnabled(active and not self.batch_cancel_requested)
            self.btn_batch_pause.setText("继续" if self.batch_pause_requested else "暂停")
        if hasattr(self, "btn_batch_cancel"):
            self.btn_batch_cancel.setEnabled(active and not self.batch_cancel_requested)
        if hasattr(self, "lbl_batch_run_state"):
            if state_text is None:
                if self.batch_cancel_requested:
                    state_text = "批量状态：取消请求已收到，当前任务完成后停止"
                elif self.batch_pause_requested:
                    state_text = "批量状态：暂停请求已收到，当前任务完成后停在下一条前"
                elif active:
                    state_text = "批量状态：运行中"
                else:
                    state_text = "批量状态：空闲"
            self.lbl_batch_run_state.setText(state_text)

    def _reset_batch_control_flags(self, run_kind):
        self.batch_job_control.reset(run_kind)
        self.batch_run_kind = run_kind
        self._set_batch_run_controls(True)

    def toggle_batch_pause(self):
        if not self.is_running:
            return
        paused = self.batch_job_control.toggle_pause()
        if paused:
            self.sig_log.emit("已请求暂停：当前任务完成后停在下一条前。", "#f9e2af")
        else:
            self.sig_log.emit("已继续批量任务。", "#a6e3a1")
            if self.batch_run_kind == "pipeline":
                QTimer.singleShot(0, self.process_next)
        self._set_batch_run_controls(True)

    def request_batch_cancel(self):
        if not self.is_running or self.batch_cancel_requested:
            return
        was_paused = self.batch_pause_requested
        self.batch_job_control.request_cancel()
        self.sig_log.emit("已请求取消：当前任务完成后停止后续队列。", "#f38ba8")
        self._set_batch_run_controls(True)
        if self.batch_run_kind == "pipeline" and was_paused:
            QTimer.singleShot(0, self.process_next)

    def _wait_while_batch_paused(self):
        return self.batch_job_control.wait_if_paused(
            on_pause_once=lambda: self.sig_log.emit("批量已暂停，点击“继续”后从下一条任务恢复。", "#f9e2af")
        )

    @pyqtSlot(int, str, str)
    def _update_table_row_status(self, idx, text, color):
        if self.tabs.currentIndex() == 0:
            if idx < self.table_layout.count():
                row_widget = self.table_layout.itemAt(idx).widget()
                if isinstance(row_widget, BatchTaskRow):
                    row_widget.lbl_status.setText(text)
                    row_widget.lbl_status.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _select_assembly_combo(self, paths, count, used_combos, rng, priority_mode="none"):
        paths = self._normalize_batch_assembly_paths(paths)
        if not paths:
            return []
        count = max(1, min(int(count or 1), len(paths)))
        resolved_priority = self._resolved_assembly_priority_mode(paths, priority_mode)
        if resolved_priority != "none":
            return self._select_priority_assembly_combo(paths, count, used_combos, rng, resolved_priority)
        if len(paths) <= count:
            combo = list(paths)
            rng.shuffle(combo)
            return combo

        total_combos = math.comb(len(paths), count) if len(paths) >= count else 1
        if total_combos <= 8000:
            all_combos = list(itertools.combinations(paths, count))
            available = [combo for combo in all_combos if tuple(sorted(combo)) not in used_combos]
            if not available:
                used_combos.clear()
                available = all_combos
            combo = list(rng.choice(available))
            rng.shuffle(combo)
            used_combos.add(tuple(sorted(combo)))
            return combo

        best = None
        best_score = -1
        recent = list(used_combos)[-50:]
        for _ in range(80):
            combo = rng.sample(paths, count)
            signature = tuple(sorted(combo))
            if signature not in used_combos:
                used_combos.add(signature)
                return combo
            overlap_penalty = sum(len(set(combo).intersection(prev)) for prev in recent)
            score = -overlap_penalty
            if best is None or score > best_score:
                best = combo
                best_score = score
        return best or rng.sample(paths, count)

    def _table_tasks_from_state(self, state):
        tasks = []
        preset_pos_x, _ = self._load_preset_position_by_name(state.get("preset_name", ""), default_y=float(state.get("subtitle_y", 25.0) or 25.0))
        preset_style = self._load_preset_style_by_name(state.get("preset_name", ""))
        signature = self._load_signature_preset_by_name(state.get("signature_preset_name", ""))
        music_payload = self._batch_music_payload(state)
        assembly_paths = self._normalize_batch_assembly_paths(state.get("assembly_paths", []))
        assembly_count = self._batch_assembly_count(state)
        assembly_mode = self._batch_assembly_mode(state)
        assembly_priority = self._batch_assembly_priority_mode(state)
        smart_groups = self._active_smart_queue_groups(state.get("smart_queue_groups", []))
        smart_enabled = bool(state.get("smart_queue_enabled", False)) and bool(smart_groups)
        smart_mode = self._smart_queue_mode(state)
        smart_cut_mode = self._smart_queue_cut_mode(state)
        used_assembly_combos = {}
        used_smart_queue_clips = {}
        rng = random.Random(f"{state.get('name', 'queue')}|{datetime.now().timestamp()}")
        for i, row in enumerate(state.get("table_rows", [])):
            row_video = (row.get("video", "") or "").strip()
            row_audio = (row.get("audio", "") or "").strip()
            row_title = (row.get("title", "") or "").strip()
            row_tag = (row.get("tag", "") or "").strip()
            row_text = (row.get("text", "") or "").strip()
            if not self._row_has_batch_content(row):
                continue
            smart_groups_for_task = self._smart_queue_groups_for_cut(smart_groups, row, i, rng, smart_mode, smart_cut_mode) if smart_enabled else []
            smart_group_name = self._smart_queue_group_names_label(smart_groups_for_task, smart_cut_mode)
            active_assembly_paths = [path for group in smart_groups_for_task for path in self._normalize_batch_assembly_paths(group.get("paths", []))] if smart_groups_for_task else assembly_paths
            selected_count = self._smart_assembly_count(active_assembly_paths, audio_path=row_audio) if assembly_mode == "smart" else assembly_count
            if smart_groups_for_task:
                video_clips = self._select_smart_queue_clips(smart_groups_for_task, selected_count, used_smart_queue_clips, rng, smart_cut_mode, assembly_priority)
            else:
                combo_key = "__default__"
                used_combos = used_assembly_combos.setdefault(combo_key, set())
                video_clips = self._select_assembly_combo(active_assembly_paths, selected_count, used_combos, rng, assembly_priority) if active_assembly_paths else []
            if row_video or video_clips:
                task_order = len(tasks)
                primary_video = video_clips[0] if video_clips else row_video
                tasks.append({
                    "type": "table",
                    "idx": i,
                    "video": primary_video,
                    "video_clips": video_clips,
                    "assembly_count": len(video_clips),
                    "assembly_mode": assembly_mode,
                    "assembly_priority": assembly_priority,
                    "smart_queue_group": smart_group_name,
                    "smart_queue_cut_mode": smart_cut_mode if smart_groups_for_task else "",
                    "audio": row_audio,
                    "title": row_title,
                    "tag": row_tag,
                    "text": row_text or self._sidecar_text_for_audio(row_audio),
                    "a_mode": state.get("audio_mode", self.audio_mode.currentText()),
                    "video_volume": int(state.get("video_volume", self.video_volume_percent())),
                    "music_path": self._music_path_for_task(music_payload, task_order),
                    "music_volume": music_payload.get("volume", self.music_volume_percent()),
                    "music_mode": music_payload.get("mode", "cycle"),
                    "performance_mode": state.get("performance_mode", self.performance_mode.currentText()),
                    "pos_x": preset_pos_x,
                    "pos_y": float(row.get("pos_y", state.get("subtitle_y", 25.0)) or 25.0),
                    "queue_name": state.get("name", "队列"),
                    "output_dir": state.get("output_dir", ""),
                    "chunk_mode": state.get("chunk_mode", self.chunk_mode.currentText()),
                    "timing_mode": state.get("timing_mode", self.timing_mode.currentText()),
                    "preset_style": preset_style,
                    "signature": copy.deepcopy(signature),
                })
        return tasks

    def _multi_project_tasks_from_state(self, state):
        tasks = []
        packages = self._active_multi_project_packages(state.get("multi_project_packages", []))
        if not packages:
            return tasks
        preset_pos_x, preset_pos_y = self._load_preset_position_by_name(state.get("preset_name", ""), default_y=float(state.get("subtitle_y", 25.0) or 25.0))
        preset_style = self._load_preset_style_by_name(state.get("preset_name", ""))
        signature = self._load_signature_preset_by_name(state.get("signature_preset_name", ""))
        music_payload = self._batch_music_payload(state)
        assembly_count = self._batch_assembly_count(state)
        assembly_mode = self._batch_assembly_mode(state)
        assembly_priority = self._batch_assembly_priority_mode(state)
        used_package_combos = {}
        rng = random.Random(f"multi-project|{state.get('name', 'queue')}|{datetime.now().timestamp()}")
        for package_index, package in enumerate(packages):
            package_name = str(package.get("name") or f"项目 {package_index + 1}")
            media_paths = self._normalize_batch_assembly_paths(package.get("media_paths", []))
            if not media_paths:
                continue
            audio_paths = self._normalize_audio_paths(package.get("audio_paths", [])) or [""]
            script_lines = package.get("script_lines", []) or []
            for audio_index, audio_path in enumerate(audio_paths):
                selected_count = self._smart_assembly_count(media_paths, audio_path=audio_path) if assembly_mode == "smart" else assembly_count
                used_combos = used_package_combos.setdefault(package_name, set())
                video_clips = self._select_assembly_combo(media_paths, selected_count, used_combos, rng, assembly_priority)
                if not video_clips:
                    continue
                task_order = len(tasks)
                title = file_stem(audio_path) if audio_path else f"{package_name}-{audio_index + 1}"
                text = script_lines[audio_index] if audio_index < len(script_lines) else self._sidecar_text_for_audio(audio_path)
                tasks.append({
                    "type": "multi_project",
                    "idx": task_order,
                    "video": video_clips[0],
                    "video_clips": video_clips,
                    "assembly_count": len(video_clips),
                    "assembly_mode": assembly_mode,
                    "assembly_priority": assembly_priority,
                    "smart_queue_group": f"项目包:{package_name}",
                    "smart_queue_cut_mode": "project_package",
                    "audio": audio_path,
                    "title": title,
                    "text": text,
                    "a_mode": state.get("audio_mode", self.audio_mode.currentText()),
                    "video_volume": int(state.get("video_volume", self.video_volume_percent())),
                    "music_path": self._music_path_for_task(music_payload, task_order),
                    "music_volume": music_payload.get("volume", self.music_volume_percent()),
                    "music_mode": music_payload.get("mode", "cycle"),
                    "performance_mode": state.get("performance_mode", self.performance_mode.currentText()),
                    "pos_x": preset_pos_x,
                    "pos_y": preset_pos_y,
                    "queue_name": f"{state.get('name', '队列')} / {package_name}",
                    "output_dir": state.get("output_dir", ""),
                    "chunk_mode": state.get("chunk_mode", self.chunk_mode.currentText()),
                    "timing_mode": state.get("timing_mode", self.timing_mode.currentText()),
                    "preset_style": preset_style,
                    "signature": copy.deepcopy(signature),
                })
        return tasks

    def _folder_tasks_from_state(self, state):
        old_input = self.input_dir
        self.input_dir = state.get("input_dir", "")
        try:
            folder_items = self._folder_pair_items() if self.input_dir else []
        finally:
            self.input_dir = old_input
        preset_pos_x, preset_pos_y = self._load_preset_position_by_name(state.get("preset_name", ""), default_y=float(state.get("subtitle_y", 25.0) or 25.0))
        preset_style = self._load_preset_style_by_name(state.get("preset_name", ""))
        signature = self._load_signature_preset_by_name(state.get("signature_preset_name", ""))
        music_payload = self._batch_music_payload(state)
        tasks = []
        for item in folder_items:
            task_order = len(tasks)
            tasks.append({
                "type": "folder",
                "idx": item["idx"],
                "video": item["video"],
                "audio": item["audio"],
                "title": item.get("title", ""),
                "text": item.get("text", ""),
                "a_mode": state.get("audio_mode", self.audio_mode.currentText()),
                "video_volume": int(state.get("video_volume", self.video_volume_percent())),
                "music_path": self._music_path_for_task(music_payload, task_order),
                "music_volume": music_payload.get("volume", self.music_volume_percent()),
                "music_mode": music_payload.get("mode", "cycle"),
                "performance_mode": state.get("performance_mode", self.performance_mode.currentText()),
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y,
                "queue_name": state.get("name", "队列"),
                "output_dir": state.get("output_dir", ""),
                "chunk_mode": state.get("chunk_mode", self.chunk_mode.currentText()),
                "timing_mode": state.get("timing_mode", self.timing_mode.currentText()),
                "preset_style": preset_style,
                "signature": copy.deepcopy(signature),
            })
        return tasks

    def _tasks_from_queue_state(self, state):
        if int(state.get("active_tab", 0) or 0) == 1:
            return self._folder_tasks_from_state(state)
        return self._table_tasks_from_state(state)

    def start_all_queue_batches(self):
        if self.is_running:
            return
        self._capture_current_queue_state()
        tasks = []
        for state in self.batch_queues:
            tasks.extend(self._tasks_from_queue_state(state))
        if not tasks:
            return QMessageBox.warning(self, "提示", "所有队列里都没有有效任务。")
        self._start_project_build(tasks, "全部队列建工程并导出", auto_render=True)

    def start_all_queue_project_builds(self):
        if self.is_running:
            return
        self._capture_current_queue_state()
        tasks = []
        for state in self.batch_queues:
            tasks.extend(self._tasks_from_queue_state(state))
        if not tasks:
            return QMessageBox.warning(self, "提示", "所有队列里都没有可建工程的任务。")
        self._start_project_build(tasks, "全部队列批量建工程")

    def start_table_batch(self):
        if self.is_running: return
        self._capture_current_queue_state()
        tasks = self._tasks_from_queue_state(self.batch_queues[self.current_queue_index])
        if not tasks:
            return QMessageBox.warning(self, "提示", "表格中没有有效任务：请填文案/音频，并选择画面，或启用随机组接/智能主体素材。")
        self._start_project_build(tasks, "表格建工程并导出", auto_render=True)

    def start_table_project_build(self):
        if self.is_running: return
        self._capture_current_queue_state()
        current_state = self.batch_queues[self.current_queue_index]
        tasks = self._tasks_from_queue_state(current_state)
        dynamic_media_enabled = self._state_has_dynamic_media(current_state)
        for row_widget in self._table_rows():
            row_widget.sync_paths_from_fields()
            row_payload = {
                "video": row_widget.video_path,
                "audio": row_widget.audio_path,
                "title": row_widget.txt_title.text().strip(),
                "tag": row_widget.txt_tag.text().strip(),
                "text": row_widget.txt_content.toPlainText().strip(),
            }
            if not self._row_has_batch_content(row_payload):
                row_widget.lbl_status.setText("略过:空行")
            elif not dynamic_media_enabled and not row_widget.video_path:
                row_widget.lbl_status.setText("略过:无画面")
        if not tasks:
            return QMessageBox.warning(self, "提示", "表格中没有有效任务：请填文案/音频，并选择画面，或启用随机组接/智能主体素材。")
        self._start_project_build(tasks, "表格批量建工程")

    def start_folder_batch(self):
        if self.is_running: return
        self._capture_current_queue_state()
        if not self.input_dir: return QMessageBox.warning(self, "提示", "请先选择输入文件夹！")

        self.task_queue.clear()
        v_files = sorted(
            [f for f in os.listdir(self.input_dir) if f.lower().endswith(MEDIA_EXTS)],
            key=natural_sort_key,
        )
        audio_lookup = build_audio_lookup(self.input_dir, self._audio_import_sort_mode())
        a_mode = self.audio_mode.currentText()
        video_volume = self.video_volume_percent()
        music_payload = self._batch_music_payload()
        performance_mode = self.performance_mode.currentText()
        preset_pos_x, preset_pos_y = self.selected_preset_position()

        for i, vf in enumerate(v_files):
            v_path = os.path.join(self.input_dir, vf)
            a_path = match_audio_for_media(vf, audio_lookup)

            task_order = len(self.task_queue)
            self.task_queue.append({
                "type": "folder",
                "idx": i,
                "video": v_path,
                "audio": a_path,
                "text": "",
                "a_mode": a_mode,
                "video_volume": video_volume,
                "music_path": self._music_path_for_task(music_payload, task_order),
                "music_volume": music_payload.get("volume", self.music_volume_percent()),
                "music_mode": music_payload.get("mode", "cycle"),
                "performance_mode": performance_mode,
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y # 文件夹模式默认高度
            })

        if not self.task_queue: return QMessageBox.warning(self, "提示", "文件夹中没找到视频/图片！")
        self._start_project_build(self.task_queue, "文件夹建工程并导出", auto_render=True)

    def start_folder_project_build(self):
        if self.is_running: return
        self._capture_current_queue_state()
        if not self.input_dir:
            return QMessageBox.warning(self, "提示", "请先选择输入文件夹。")
        tasks = []
        v_files = sorted(
            [f for f in os.listdir(self.input_dir) if f.lower().endswith(MEDIA_EXTS)],
            key=natural_sort_key,
        )
        audio_lookup = build_audio_lookup(self.input_dir, self._audio_import_sort_mode())
        preset_pos_x, preset_pos_y = self.selected_preset_position()
        music_payload = self._batch_music_payload()
        for i, vf in enumerate(v_files):
            v_path = os.path.join(self.input_dir, vf)
            base_name = os.path.splitext(vf)[0]
            text_path = ""
            a_path = match_audio_for_media(vf, audio_lookup)
            for ext in TEXT_EXTS:
                test_t = os.path.join(self.input_dir, base_name + ext)
                if os.path.exists(test_t):
                    text_path = test_t
                    break
            custom_text = ""
            if text_path:
                custom_text = read_text_source(text_path)
            task_order = len(tasks)
            tasks.append({
                "idx": i,
                "video": v_path,
                "audio": a_path,
                "a_mode": self.audio_mode.currentText(),
                "video_volume": self.video_volume_percent(),
                "music_path": self._music_path_for_task(music_payload, task_order),
                "music_volume": music_payload.get("volume", self.music_volume_percent()),
                "music_mode": music_payload.get("mode", "cycle"),
                "performance_mode": self.performance_mode.currentText(),
                "title": base_name,
                "text": custom_text,
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y
            })
        if not tasks:
            return QMessageBox.warning(self, "提示", "文件夹中没有找到视频/图片。")
        self._start_project_build(tasks, "文件夹批量建工程")

    def _folder_pair_items(self):
        v_files = sorted(
            [f for f in os.listdir(self.input_dir) if f.lower().endswith(MEDIA_EXTS)],
            key=natural_sort_key,
        )
        if not v_files:
            return []
        video_paths = [os.path.join(self.input_dir, vf) for vf in v_files]
        audio_paths = list_audio_paths(self.input_dir, self._audio_import_sort_mode())
        audio_lookup = build_audio_lookup(self.input_dir, self._audio_import_sort_mode())
        items = []

        def sidecar_text(*stems):
            for stem in stems:
                if not stem:
                    continue
                for ext in TEXT_EXTS:
                    candidate = os.path.join(self.input_dir, stem + ext)
                    if os.path.exists(candidate):
                        return read_text_source(candidate)
            return ""

        if audio_paths and len(audio_paths) > len(video_paths):
            for i, audio_path in enumerate(audio_paths):
                video_path = video_paths[i % len(video_paths)]
                audio_stem = file_stem(audio_path)
                video_stem = file_stem(video_path)
                items.append({
                    "idx": i,
                    "video": video_path,
                    "audio": audio_path,
                    "title": audio_stem,
                    "text": sidecar_text(audio_stem, video_stem),
                    "cycle_video": True,
                })
            return items

        for i, video_path in enumerate(video_paths):
            video_name = os.path.basename(video_path)
            video_stem = file_stem(video_path)
            audio_path = match_audio_for_media(video_name, audio_lookup)
            items.append({
                "idx": i,
                "video": video_path,
                "audio": audio_path,
                "title": video_stem,
                "text": sidecar_text(video_stem, file_stem(audio_path)),
                "cycle_video": False,
            })
        return items

    def start_folder_batch(self):
        if self.is_running:
            return
        self._capture_current_queue_state()
        if not self.input_dir:
            return QMessageBox.warning(self, "提示", "请先选择输入文件夹！")

        folder_items = self._folder_pair_items()
        if not folder_items:
            return QMessageBox.warning(self, "提示", "文件夹中没找到视频/图片！")

        self.task_queue.clear()
        a_mode = self.audio_mode.currentText()
        video_volume = self.video_volume_percent()
        music_payload = self._batch_music_payload()
        performance_mode = self.performance_mode.currentText()
        preset_pos_x, preset_pos_y = self.selected_preset_position()
        for item in folder_items:
            task_order = len(self.task_queue)
            self.task_queue.append({
                "type": "folder",
                "idx": item["idx"],
                "video": item["video"],
                "audio": item["audio"],
                "title": item["title"],
                "text": item.get("text", ""),
                "a_mode": a_mode,
                "video_volume": video_volume,
                "music_path": self._music_path_for_task(music_payload, task_order),
                "music_volume": music_payload.get("volume", self.music_volume_percent()),
                "music_mode": music_payload.get("mode", "cycle"),
                "performance_mode": performance_mode,
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y,
            })

        cycled = sum(1 for item in folder_items if item.get("cycle_video"))
        if cycled:
            self.sig_log.emit(f"检测到音频多于画面：已用 {len({item['video'] for item in folder_items})} 个画面循环匹配 {len(folder_items)} 个音频。", "#a6e3a1")
        self._start_project_build(self.task_queue, "文件夹建工程并导出", auto_render=True)

    def start_folder_project_build(self):
        if self.is_running:
            return
        self._capture_current_queue_state()
        if not self.input_dir:
            return QMessageBox.warning(self, "提示", "请先选择输入文件夹。")

        folder_items = self._folder_pair_items()
        if not folder_items:
            return QMessageBox.warning(self, "提示", "文件夹中没有找到视频/图片。")

        preset_pos_x, preset_pos_y = self.selected_preset_position()
        music_payload = self._batch_music_payload()
        tasks = []
        for item in folder_items:
            task_order = len(tasks)
            tasks.append({
                "idx": item["idx"],
                "video": item["video"],
                "audio": item["audio"],
                "a_mode": self.audio_mode.currentText(),
                "video_volume": self.video_volume_percent(),
                "music_path": self._music_path_for_task(music_payload, task_order),
                "music_volume": music_payload.get("volume", self.music_volume_percent()),
                "music_mode": music_payload.get("mode", "cycle"),
                "performance_mode": self.performance_mode.currentText(),
                "title": item["title"],
                "text": item.get("text", ""),
                "pos_x": preset_pos_x,
                "pos_y": preset_pos_y,
            })
        self._start_project_build(tasks, "文件夹批量建工程")

    def _start_project_build(self, tasks, mode_name, auto_render=False):
        self.refresh_signature_presets()
        project_dir = self._resolve_project_output_dir()
        preset_style = self._load_selected_preset_style()
        c_mode = self.chunk_mode.currentText()
        timing_mode = self.timing_mode.currentText()
        default_signature = self._load_selected_signature_config()
        for task in tasks:
            if isinstance(task, dict) and "signature" not in task:
                task["signature"] = copy.deepcopy(default_signature)
        batch_record = self._init_project_record(tasks, project_dir, mode_name, c_mode, timing_mode)
        self.auto_render_after_project_build = bool(auto_render)
        self.is_running = True
        self._reset_batch_control_flags("project_build")
        self.log_console.clear()
        self.progress_bar.setValue(0)
        self.sig_log.emit(f"{mode_name}启动，共 {len(tasks)} 个工程。", "#a6e3a1")
        self.sig_log.emit(f"批量工程记录已创建: {os.path.basename(batch_record['files']['json'])}", "#89b4fa")
        threading.Thread(
            target=self._project_build_worker,
            args=(tasks, project_dir, preset_style, c_mode, timing_mode, batch_record),
            daemon=True
        ).start()

    def _resolve_project_output_dir(self):
        if self.project_output_dir:
            os.makedirs(self.project_output_dir, exist_ok=True)
            return self.project_output_dir
        workspace = get_active_workspace()
        os.makedirs(workspace, exist_ok=True)
        project_dir = os.path.join(workspace, f"批量工程_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(project_dir, exist_ok=True)
        self.project_output_dir = project_dir
        self.lbl_project_output.setText(f"批量建工程目录: {project_dir}")
        return project_dir

    def _load_selected_preset_style(self):
        base_style = {"layout_mode": "standard", "box_layout": "fixed", "box_width": 74.0, "box_height": 0.0, "max_lines": 2, "hl_style": "text"}
        preset_style, _ = split_style_preset(self._load_selected_preset_raw())
        base_style.update(preset_style)
        return base_style

    def _load_preset_style_by_name(self, preset_name):
        base_style = {"layout_mode": "standard", "box_layout": "fixed", "box_width": 74.0, "box_height": 0.0, "max_lines": 2, "hl_style": "text"}
        if not preset_name:
            return base_style
        try:
            presets = read_json_file(PRESETS_FILE, default={})
            if not isinstance(presets, dict):
                presets = {}
            presets = merge_built_in_style_presets(presets)
            preset_style, _ = split_style_preset(presets.get(preset_name, {}) if isinstance(presets, dict) else {})
            base_style.update(preset_style)
        except Exception:
            pass
        return base_style

    def _load_preset_position_by_name(self, preset_name, default_x=0.0, default_y=25.0):
        if not preset_name:
            return float(default_x), float(default_y)
        try:
            presets = read_json_file(PRESETS_FILE, default={})
            if not isinstance(presets, dict):
                presets = {}
            presets = merge_built_in_style_presets(presets)
            _, position = split_style_preset(presets.get(preset_name, {}) if isinstance(presets, dict) else {})
            if position:
                return position["pos_x"], position["pos_y"]
        except Exception:
            pass
        return float(default_x), float(default_y)

    def _record_rel_path(self, base_dir, path):
        if not path:
            return ""
        try:
            return os.path.relpath(path, base_dir).replace("\\", "/")
        except Exception:
            return path

    def _init_project_record(self, tasks, project_dir, mode_name, c_mode, timing_mode):
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(project_dir, f"批量工程记录_{run_id}.json")
        csv_path = os.path.join(project_dir, f"批量工程记录_{run_id}.csv")
        task_music_paths = []
        for task in tasks:
            music_path = task.get("music_path", "")
            if music_path and music_path not in task_music_paths:
                task_music_paths.append(music_path)
        record = {
            "record_type": "subtitle_composer_batch_project_build",
            "version": 1,
            "run_id": run_id,
            "mode_name": mode_name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "project_dir": project_dir,
            "settings": {
                "preset_name": self.preset_combo.currentText(),
                "signature_preset_name": self.signature_preset_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "signature_preset_combo") else "",
                "chunk_mode": c_mode,
                "timing_mode": timing_mode,
                "audio_mode": self.audio_mode.currentText(),
                "video_volume": self.video_volume_percent(),
                "music_path": task_music_paths[0] if task_music_paths else "",
                "music_paths": task_music_paths,
                "music_mode": self._batch_music_mode(),
                "music_volume": self.music_volume_percent(),
                "performance_mode": self.performance_mode.currentText(),
                "output_dir": self.output_dir,
                "input_dir": self.input_dir,
                "workspace_mode": get_workspace_config().get("mode", "local"),
            },
            "summary": {"total": len(tasks), "success": 0, "failed": 0},
            "files": {"json": json_path, "csv": csv_path},
            "rows": [],
        }
        for order, task in enumerate(tasks, start=1):
            task["batch_record"] = {
                "run_id": run_id,
                "row": order,
                "record_json": os.path.basename(json_path),
                "record_csv": os.path.basename(csv_path),
            }
            text = task.get("text", "") or ""
            record["rows"].append({
                "row": order,
                "ui_row": int(task.get("idx", order - 1)) + 1,
                "status": "pending",
                "title": task.get("title", ""),
                "tag": task.get("tag", ""),
                "video": task.get("video", ""),
                "smart_queue_group": task.get("smart_queue_group", ""),
                "smart_queue_cut_mode": task.get("smart_queue_cut_mode", ""),
                "audio": task.get("audio", ""),
                "video_volume": int(task.get("video_volume", self.video_volume_percent())),
                "music": task.get("music_path", ""),
                "music_volume": int(task.get("music_volume", self.music_volume_percent())),
                "music_mode": task.get("music_mode", ""),
                "subtitle_x": task.get("pos_x", 0.0),
                "subtitle_y": task.get("pos_y", 25.0),
                "text": text,
                "text_chars": len(text),
                "project_name": "",
                "project_path": "",
                "project_rel_path": "",
                "error": "",
                "started_at": "",
                "finished_at": "",
            })
        self._write_project_record(record)
        return record

    def _write_project_record(self, record):
        try:
            with open(record["files"]["json"], "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            fields = [
                "row", "ui_row", "status", "project_name", "project_rel_path",
                "video", "smart_queue_group", "smart_queue_cut_mode", "audio", "video_volume", "music", "music_volume", "music_mode", "title", "tag", "subtitle_x", "subtitle_y", "text_chars", "error",
            ]
            with open(record["files"]["csv"], "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for row in record.get("rows", []):
                    writer.writerow({field: row.get(field, "") for field in fields})
        except Exception as e:
            self.sig_log.emit(f"工程记录写入失败: {e}", "#f38ba8")

    def _project_build_worker(self, tasks, project_dir, preset_style, c_mode, timing_mode, batch_record):
        success = 0
        failed = 0
        built_paths = []
        queue_groups = []
        queue_group_index = {}
        total = max(1, len(tasks))
        for i, task in enumerate(tasks):
            if self.batch_cancel_requested:
                failed += len(tasks) - i
                for row_record in batch_record.get("rows", [])[i:]:
                    row_record["status"] = "cancelled"
                    row_record["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.sig_log.emit("批量建工程已取消，剩余任务已跳过。", "#f38ba8")
                break
            if not self._wait_while_batch_paused():
                failed += len(tasks) - i
                for row_record in batch_record.get("rows", [])[i:]:
                    row_record["status"] = "cancelled"
                    row_record["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.sig_log.emit("批量建工程已取消，剩余任务已跳过。", "#f38ba8")
                break
            idx = task.get("idx", i)
            row_record = batch_record["rows"][i] if i < len(batch_record.get("rows", [])) else None
            try:
                if row_record is not None:
                    row_record["status"] = "building"
                    row_record["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._write_project_record(batch_record)
                self.sig_table_row_status.emit(idx, "建工程中", "#f9e2af")
                task_style = task.get("preset_style", preset_style)
                task_chunk_mode = task.get("chunk_mode", c_mode)
                task_timing_mode = task.get("timing_mode", timing_mode)
                project_path = self._build_single_project(task, project_dir, task_style, task_chunk_mode, task_timing_mode)
                success += 1
                if project_path:
                    built_paths.append(project_path)
                    queue_name = str(task.get("queue_name") or "队列")
                    queue_output_dir = str(task.get("output_dir") or "")
                    group_key = (queue_name, queue_output_dir)
                    if group_key not in queue_group_index:
                        queue_group_index[group_key] = len(queue_groups)
                        queue_groups.append({"name": queue_name, "output_dir": queue_output_dir, "paths": []})
                    queue_groups[queue_group_index[group_key]]["paths"].append(project_path)
                if row_record is not None:
                    row_record["status"] = "success"
                    row_record["project_name"] = os.path.splitext(os.path.basename(project_path))[0] if project_path else ""
                    row_record["project_path"] = project_path
                    row_record["project_rel_path"] = self._record_rel_path(project_dir, project_path)
                    row_record["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._write_project_record(batch_record)
                self.sig_table_row_status.emit(idx, "已建工程", "#a6e3a1")
                self.sig_log.emit(f"已建立工程: {os.path.basename(project_path)}", "#a6e3a1")
            except Exception as e:
                failed += 1
                if row_record is not None:
                    row_record["status"] = "failed"
                    row_record["error"] = str(e)
                    row_record["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._write_project_record(batch_record)
                self.sig_table_row_status.emit(idx, "失败", "#f38ba8")
                self.sig_log.emit(f"工程建立失败: {os.path.basename(task.get('video', ''))} | {e}", "#f38ba8")
            self.sig_progress.emit(int((i + 1) * 100 / total))
        batch_record["summary"] = {"total": len(tasks), "success": success, "failed": failed}
        batch_record["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_project_record(batch_record)
        payload = {"built_paths": built_paths, "queue_groups": queue_groups, "record_json": batch_record["files"]["json"], "record_csv": batch_record["files"]["csv"]}
        self.sig_projects_done.emit(success, failed, project_dir, payload)

    def _content_total_duration(self, video_dur, audio_dur, audio_mode):
        video_dur = max(0.1, float(video_dur or 0.0))
        audio_dur = max(0.0, float(audio_dur or 0.0))
        if audio_dur > 0 and ("替换" in audio_mode or "静音" in audio_mode):
            return max(1.0, audio_dur)
        if audio_dur > 0 and ("混合" in audio_mode or "配音" in audio_mode):
            return max(1.0, video_dur, audio_dur)
        return max(1.0, video_dur)

    def _aligned_total_duration(self, video_dur, audio_dur, audio_mode):
        return self._content_total_duration(video_dur, audio_dur, audio_mode) + render_tail_padding_seconds()

    def _audio_project_settings(self, audio_path, audio_mode, video_volume=None):
        has_audio = bool(audio_path and os.path.exists(audio_path))
        v_volume = max(0, min(100, int(self.video_volume_percent() if video_volume is None else video_volume)))
        if "保留" in audio_mode:
            return "", v_volume, 0
        if "替换" in audio_mode or "静音" in audio_mode:
            return (audio_path if has_audio else ""), 0, 100
        if "混合" in audio_mode or "配音" in audio_mode:
            return (audio_path if has_audio else ""), v_volume, 100
        return (audio_path if has_audio else ""), 0, 100

    def _media_source_duration(self, path):
        if not path or not os.path.exists(path):
            return 0.0, 0.0
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS:
            return 5.0, 5.0
        video_dur = float(get_exact_duration(path) or 0.0)
        stream_dur = float(get_video_stream_duration(path) or video_dur or 0.0)
        if video_dur <= 0:
            video_dur = stream_dur or 5.0
        if stream_dur <= 0:
            stream_dur = video_dur
        return max(0.1, video_dur), max(0.1, stream_dur)

    def _build_video_clip_sequence(self, video_paths, content_dur):
        paths = [path for path in video_paths or [] if path and os.path.exists(path) and looks_media_path(path)]
        if not paths:
            return [], 0.0
        media = []
        for path in paths:
            video_dur, stream_dur = self._media_source_duration(path)
            media.append({"path": path, "video_dur": video_dur, "stream_dur": stream_dur})
        source_total = sum(item["video_dur"] for item in media) or len(media) * 5.0
        target = max(1.0, float(content_dur or 0.0) or source_total)
        clips = []
        cursor = 0.0
        remaining = target
        for idx, item in enumerate(media):
            if idx == len(media) - 1:
                clip_len = max(0.1, remaining)
            else:
                weight = item["video_dur"] / source_total if source_total > 0 else 1.0 / len(media)
                clip_len = max(0.1, target * weight)
                clip_len = min(clip_len, max(0.1, remaining - 0.1 * (len(media) - idx - 1)))
                remaining -= clip_len
            clips.append({
                "path": item["path"],
                "start": cursor,
                "end": cursor + clip_len,
                "dur": item["stream_dur"],
                "source_in": 0.0,
                "source_out": item["video_dur"],
                "speed": 1.0,
                "scale": 100,
                "volume": 100,
                "transition": {"type": "cut", "duration": 0.0},
                "assembly_mode": "batch_random",
            })
            cursor += clip_len
        return clips, source_total

    def _build_single_project(self, task, project_dir, preset_style, c_mode, timing_mode):
        video_path = task.get("video", "")
        video_paths = [path for path in (task.get("video_clips") or []) if path and os.path.exists(path)]
        if not video_paths and video_path:
            video_paths = [video_path]
        if video_paths:
            video_path = video_paths[0]
        audio_path = task.get("audio", "")
        if not video_path or not os.path.exists(video_path):
            raise Exception("视频路径不存在")

        title = project_title_from_task(task.get("title", ""), video_path)
        base_name = title or os.path.splitext(os.path.basename(video_path))[0]
        reel_name = self._unique_reel_name(project_dir, base_name)
        project_data = create_reel(project_dir, reel_name, "edit_room")
        if task.get("batch_record"):
            project_data["batch_record"] = task.get("batch_record")
        project_tag = str(task.get("tag", "") or "").strip()
        if project_tag:
            project_data["project_tag"] = project_tag
            project_data["tags"] = [project_tag]

        video_durs = [self._media_source_duration(path)[0] for path in video_paths]
        video_dur = sum(video_durs) if video_durs else (get_exact_duration(video_path) or 5.0)
        audio_dur = get_exact_duration(audio_path) if audio_path and os.path.exists(audio_path) else 0.0
        audio_mode = task.get("a_mode") or self.audio_mode.currentText()
        if task.get("video_clips") and audio_dur > 0:
            content_dur = max(1.0, audio_dur)
        else:
            content_dur = self._content_total_duration(video_dur, audio_dur, audio_mode)
        total_dur = content_dur + render_tail_padding_seconds()
        music_path = task.get("music_path", "")
        has_music = bool(music_path and os.path.exists(music_path))
        music_dur = get_exact_duration(music_path) if has_music else 0.0
        music_volume = max(0, min(100, int(task.get("music_volume", self.music_volume_percent()))))
        project_audio_path, v_volume, a_volume = self._audio_project_settings(
            audio_path,
            audio_mode,
            task.get("video_volume", self.video_volume_percent()),
        )
        custom_text = task.get("text", "").strip()

        sub_task = dict(task)
        sub_task["text"] = custom_text
        subs_data = self._generate_project_subs(sub_task, content_dur, c_mode, timing_mode)
        pos_x = float(task.get("pos_x", 0.0))
        pos_y = float(task.get("pos_y", 25.0))
        for sub in subs_data:
            sub["style"] = preset_style.copy()
            sub["pos_x"] = pos_x
            sub["pos_y"] = pos_y
            sub["track"] = sub.get("track", 1)
        subs_data, _ = rebalance_subtitle_layout(
            subs_data,
            fallback_style=preset_style,
            default_pos=(pos_x, pos_y),
            force_standard_box=True
        )

        video_clip_sequence, _ = self._build_video_clip_sequence(video_paths, content_dur)

        edit_state = {
            "video_clips": video_clip_sequence,
            "audio_path": project_audio_path,
            "music_path": music_path if has_music else "",
            "subs_data": subs_data,
            "a_trim": [0.0, min(total_dur, audio_dur) if audio_dur > 0 else total_dur],
            "duration": total_dur,
            "resolution": get_output_resolution(),
            "v_scale": 100,
            "v_volume": v_volume,
            "a_volume": a_volume,
            "music_volume": music_volume,
            "music_dur": music_dur,
            "music_match_duration": content_dur if has_music else 0.0,
            "music_loop": bool(has_music),
            "chunk_mode": c_mode,
            "timing_mode": timing_mode,
            "custom_text": custom_text,
            "project_tag": project_tag,
            "default_pos_x": pos_x,
            "default_pos_y": pos_y,
            "default_style": preset_style.copy(),
            "signature": task.get("signature", {})
        }
        project_data = update_room_state(project_data, "edit_room", edit_state)
        if task.get("batch_record"):
            project_data["batch_record"] = task.get("batch_record")
            save_project(project_data["project_path"], project_data)
        if get_workspace_config().get("mode") == WORKSPACE_MODE_CLOUD:
            project_data, _ = sync_project_assets_to_project_dir(project_data)
        self._try_generate_project_cover(project_data, video_path)
        return project_data.get("project_path", "")

    def _generate_project_subs(self, task, total_dur, c_mode, timing_mode):
        custom_text = task.get("text", "").strip()
        target_path = task.get("audio") if task.get("audio") else task.get("video")
        words = []

        if target_path and os.path.exists(target_path):
            try:
                words = self._transcribe_words(target_path)
            except Exception:
                if not custom_text:
                    raise

        if custom_text:
            if words:
                words = self._align_user_text_to_ai_words(words, custom_text)
            else:
                words = self._rough_words_from_text(custom_text, total_dur)

        if not words:
            raise Exception("没有可用的文案或 AI 打轴结果")

        return self.process_words(words, c_mode, timing_mode)

    def _transcribe_words(self, target_path):
        temp_audio = os.path.join(tempfile.gettempdir(), f"sh_project_build_{threading.get_ident()}.mp3")
        try:
            cmd = [get_ffmpeg_cmd(), "-y", "-i", target_path, "-vn", "-map", "a:0?", "-ar", "16000", "-ac", "1", "-b:a", "16k", temp_audio]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)
            if not os.path.exists(temp_audio) or os.path.getsize(temp_audio) <= 100:
                raise Exception("\u97f3\u9891\u62bd\u53d6\u5931\u8d25")
            return transcribe_audio_words(temp_audio, provider_order=self._selected_ai_transcription_provider_order())
        finally:
            if os.path.exists(temp_audio):
                try:
                    os.remove(temp_audio)
                except Exception:
                    pass

    def _rough_words_from_text(self, raw_text, total_dur):
        tokens = self._tokenize_user_text_for_alignment(raw_text)
        if len(tokens) <= 1:
            cleaned = raw_text.strip()
            if re.search(r"\s", cleaned):
                tokens = cleaned.split()
            else:
                tokens = [ch for ch in cleaned if not ch.isspace()]
        if not tokens:
            return []
        step = max(0.05, float(total_dur) / len(tokens))
        words = []
        for i, token in enumerate(tokens):
            start = i * step
            end = min(float(total_dur), start + step * 0.92)
            if end <= start:
                end = start + 0.05
            words.append({"word": token, "start": start, "end": end})
        return words

    def _unique_reel_name(self, project_dir, base_name):
        safe = "".join(c for c in base_name.strip() if c not in r'\/:*?"<>|') or "批量Reel"
        candidate = safe
        n = 2
        while os.path.exists(os.path.join(project_dir, f"{candidate}.scomp")):
            candidate = f"{safe}-{n}"
            n += 1
        return candidate

    def _try_generate_project_cover(self, project_data, video_path):
        try:
            project_dir = project_data.get("project_dir", "")
            project_name = project_data.get("project_name", "untitled")
            if not project_dir or not video_path or not os.path.exists(video_path):
                return
            cover_filename = f"{project_name}_cover.jpg"
            cover_path = os.path.join(project_dir, cover_filename)
            flags = 0x08000000 if os.name == 'nt' else 0
            subprocess.run(
                [get_ffmpeg_cmd(), "-y", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-q:v", "2", cover_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                timeout=15
            )
            if os.path.exists(cover_path):
                project_data["cover_img"] = cover_filename
                save_project(project_data["project_path"], project_data)
        except Exception:
            pass

    def _on_projects_done(self, success, failed, project_dir, built_paths):
        was_cancelled = self.batch_finish_reason == "cancelled" or self.batch_cancel_requested
        self.is_running = False
        self.batch_pause_requested = False
        self.batch_cancel_requested = False
        self._set_batch_run_controls(False, "批量状态：已取消" if was_cancelled else "批量状态：空闲")
        self.progress_bar.setValue(100)
        record_json = ""
        record_csv = ""
        queue_groups = []
        if isinstance(built_paths, dict):
            record_json = built_paths.get("record_json", "")
            record_csv = built_paths.get("record_csv", "")
            queue_groups = built_paths.get("queue_groups", [])
            built_paths = built_paths.get("built_paths", [])
        self.sig_log.emit(f"批量建工程完成: 成功 {success} 个，失败 {failed} 个。目录: {project_dir}", "#a6e3a1" if success else "#f38ba8")
        if record_json:
            self.sig_log.emit(f"工程记录文件: {record_json}", "#89b4fa")
        parent = self.parent()
        while parent is not None and not hasattr(parent, "room_project"):
            parent = parent.parent()
        if parent and hasattr(parent, "room_project"):
            try:
                parent.room_project.refresh_folders(select_name=os.path.basename(project_dir))
            except Exception:
                pass
        handed_off = False
        auto_render = bool(getattr(self, "auto_render_after_project_build", False))
        self.auto_render_after_project_build = False
        if was_cancelled:
            auto_render = False
        if success and parent and hasattr(parent, "room_deliver"):
            output_dir = self.output_dir or os.path.join(project_dir, "批量成品")
            try:
                handoff_queue_count = 1
                if queue_groups and hasattr(parent.room_deliver, "set_export_queues_from_batch_groups"):
                    parent.room_deliver.set_export_queues_from_batch_groups(
                        queue_groups,
                        source_label=os.path.basename(project_dir),
                        default_output_dir=output_dir,
                    )
                    handoff_queue_count = len([group for group in queue_groups if group.get("paths")])
                else:
                    parent.room_deliver.set_batch_projects(built_paths, source_label=os.path.basename(project_dir), output_dir=output_dir)
                parent.switch_room(3)
                handed_off = True
                if auto_render:
                    self.sig_log.emit("已建立工程并接入导出房间，正在自动开始批量导出。", "#89b4fa")
                    if handoff_queue_count > 1 and hasattr(parent.room_deliver, "start_all_export_queues"):
                        QTimer.singleShot(0, parent.room_deliver.start_all_export_queues)
                    else:
                        QTimer.singleShot(0, parent.room_deliver.start_batch_render)
                else:
                    self.sig_log.emit("已接入导出房间，可直接开始批量导出。", "#89b4fa")
            except Exception as e:
                self.sig_log.emit(f"接入导出房间失败: {e}", "#f38ba8")
        handoff_text = "\n\n已把成功工程送到导出房间。" if handed_off else ""
        record_text = f"\n\n工程记录:\n{record_json}\n{record_csv}" if record_json else ""
        if auto_render and handed_off:
            return
        QMessageBox.information(self, "批量建工程完成", f"成功建立 {success} 个工程，失败 {failed} 个。\n目录:\n{project_dir}{record_text}{handoff_text}")

    def _start_pipeline(self, mode_name):
        self.preset_name = self.preset_combo.currentText()
        self.preset_style = self._load_selected_preset_style()

        self.is_running = True
        self._reset_batch_control_flags("pipeline")
        self.current_idx = 0
        self.log_console.clear()
        self.sig_log.emit(f"🚀 {mode_name} 启动！共发现 {len(self.task_queue)} 个生产任务。", "#a6e3a1")
        self.process_next()

    def _task_output_stem(self, task):
        audio_stem = file_stem(task.get("audio", ""))
        video_stem = file_stem(task.get("video", ""))
        return safe_filename_stem(task.get("title") or audio_stem or video_stem, "BatchReel")

    def _unique_output_path(self, out_dir, stem):
        base = safe_filename_stem(stem, "BatchReel")
        candidate = os.path.join(out_dir, f"Pro_{base}.mp4")
        if not os.path.exists(candidate):
            return candidate
        n = 2
        while True:
            candidate = os.path.join(out_dir, f"Pro_{base}_{n}.mp4")
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def _selected_ai_transcription_provider_order(self):
        combo = getattr(self, "ai_transcription_provider_combo", None)
        if combo is None:
            return None
        data = combo.currentData()
        return data if data else None

    def process_next(self):
        if self.batch_cancel_requested:
            self.batch_finish_reason = "cancelled"
            self.sig_all_done.emit()
            return
        if self.batch_pause_requested:
            self._set_batch_run_controls(True)
            self.sig_log.emit("批量已暂停，点击“继续”后从下一条任务恢复。", "#f9e2af")
            return
        if self.current_idx >= len(self.task_queue):
            self.sig_all_done.emit()
            return

        task = self.task_queue[self.current_idx]
        v_path = task["video"]
        a_path = task["audio"]
        if task.get("queue_name"):
            self.sig_log.emit(f"队列「{task.get('queue_name')}」任务 {self.current_idx + 1}/{len(self.task_queue)}", "#89b4fa")

        out_dir = task.get("output_dir") or self.output_dir or os.path.dirname(v_path)
        out_path = self._unique_output_path(out_dir, self._task_output_stem(task))

        c_mode = self.chunk_mode.currentText()
        timing_mode = self.timing_mode.currentText()

        self.sig_table_row_status.emit(task["idx"], "🔄 正在渲染", "#f9e2af")
        self.sig_progress.emit(0)

        provider_order = self._selected_ai_transcription_provider_order()
        threading.Thread(target=self.pipeline_worker, args=(task, out_path, c_mode, timing_mode, provider_order), daemon=True).start()

    def pipeline_worker(self, task, out_path, c_mode, timing_mode, provider_order=None):
        temp_dir = tempfile.mkdtemp()
        try:
            v_path = task["video"]
            a_path = task["audio"]
            custom_text = task["text"]
            t_idx = task["idx"]
            a_mode = task.get("a_mode", "🔈 原声20% + 配音")
            performance_mode = task.get("performance_mode", self.performance_mode.currentText() if hasattr(self, "performance_mode") else "标准画质")
            signature = task.get("signature", {})

            self.sig_log.emit(f"▶ 开始装配视频: {os.path.basename(v_path)}", "#89b4fa")

            subs_data = []

            target_path = a_path if a_path else v_path
            use_custom_text = bool(custom_text.strip())

            self.sig_log.emit(f"  [1/4] 抽取音频供 AI 识别{'并对齐手工文案' if use_custom_text else ''}...", "#cdd6f4")
            temp_audio = os.path.join(temp_dir, "temp.mp3")
            subprocess.run([get_ffmpeg_cmd(), "-y", "-i", target_path, "-vn", "-map", "a:0", "-ar", "16000", "-ac", "1", "-b:a", "16k", "-t", "600", temp_audio], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)

            if os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 10 * 1024 * 1024:
                raise Exception(f"源文件音频轨道异常，已被系统拦截！")

            self.sig_progress.emit(10)
            self.sig_log.emit("  [2/4] \u6309\u4f18\u5148\u7ea7\u547c\u53eb AI \u542c\u8bd1\u670d\u52a1...", "#cdd6f4")
            clean_words = transcribe_audio_words(
                temp_audio,
                progress=lambda msg, color="#cdd6f4": self.sig_log.emit(f"  {msg}", color),
                provider_order=provider_order,
            )

            if use_custom_text:
                self.sig_log.emit("  [2.5/4] 检测到手工文案，正在把文案对齐到 AI 时间轴...", "#a6e3a1")
                clean_words = self._align_user_text_to_ai_words(clean_words, custom_text)

            subs_data = self.process_words(clean_words, c_mode, timing_mode)
            row_custom_x = task.get("pos_x", 0.0)
            row_custom_y = task.get("pos_y", 25.0) # 👑 应用你调整好的独立高度参数
            for sub in subs_data:
                sub["style"] = task.get("preset_style", self.preset_style).copy()
                sub["pos_x"] = row_custom_x
                sub["pos_y"] = row_custom_y        # 👑 强制覆盖
            subs_data, _ = rebalance_subtitle_layout(
                subs_data,
                fallback_style=task.get("preset_style", self.preset_style),
                default_pos=(row_custom_x, row_custom_y),
                force_standard_box=True
            )

            self.sig_progress.emit(30)

            self.sig_log.emit(f"  [3/4] 启动 30FPS 特效物理引擎...", "#cdd6f4")
            concat_path = os.path.join(temp_dir, "subs_concat.txt").replace("\\", "/")
            blank_path = os.path.join(temp_dir, "blank.png").replace("\\", "/")

            proj_w, proj_h = resolution_to_size(get_output_resolution(), v_path, get_video_dimensions)

            v_dur = get_exact_duration(v_path)
            v_stream_dur = get_video_stream_duration(v_path) or v_dur
            a_dur = get_exact_duration(a_path) if a_path else 0
            content_dur = self._content_total_duration(v_dur, a_dur, a_mode)
            total_dur = content_dur + render_tail_padding_seconds()

            with sync_playwright() as p:
                browser = launch_render_browser(p)
                render_scale = self.subtitle_render_scale(performance_mode)
                render_w = int(proj_w * render_scale)
                render_h = int(proj_h * render_scale)
                page = browser.new_page(viewport={"width": render_w, "height": render_h}, device_scale_factor=1)
                page.set_content("<html><body style='background:transparent;'></body></html>")
                page.screenshot(path=blank_path, omit_background=True, scale="css")

                with open(concat_path, "w", encoding="utf-8") as f_concat:
                    frame_idx = 0
                    last_concat_file = blank_path
                    extra_styles = []
                    if isinstance(signature, dict) and signature.get("enabled") and str(signature.get("text", "")).strip():
                        extra_styles.append(signature.get("style", {}))
                    frame_schedule = build_subtitle_frame_schedule(subs_data, total_dur, extra_styles=extra_styles)
                    self.sig_log.emit(f"  ⚡ 字幕渲染采样: {len(frame_schedule)} 段，{performance_mode} x{render_scale:g}", "#89b4fa")

                    def write_subtitle_frame(path, duration):
                        nonlocal last_concat_file
                        duration = max(0.001, float(duration or 0.0))
                        f_concat.write(ffconcat_file_entry(path, duration))
                        last_concat_file = path

                    for current_time, frame_duration in frame_schedule:
                        active_subs = active_subtitles_for_frame(subs_data, current_time, frame_duration)
                        signature_html = render_signature_html(signature, current_time, proj_w, proj_h)
                        if not active_subs and not signature_html:
                            write_subtitle_frame(blank_path, frame_duration)
                            continue

                        html_subs = signature_html
                        for s, sub_time in active_subs:
                            px = s.get("pos_x", 0.0); py = s.get("pos_y", 25.0)
                            base_css = f"position: absolute; left: calc(50% + {px}%); top: calc(50% + {py}%); transform: translate(-50%, -50%); z-index: 10; width: max-content; max-width: 92%;"
                            sub_html = render_subtitle_html(s, sub_time, proj_w, proj_h)
                            html_subs += f"<div style='{base_css}'>{sub_html}</div>\n"

                        # 👑 全局抗锯齿平滑渲染参数
                        html_content = f"<!DOCTYPE html><html><head><style>html, body {{ margin: 0; padding: 0; width: 100vw; height: 100vh; overflow: hidden; background: transparent; display: flex; justify-content: center; align-items: center; -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }} #scale-wrapper {{ width: 100vw; height: 100vh; position: absolute; left: 0; top: 0; filter: drop-shadow(0px 0px 0px transparent); }}</style></head><body><div id='scale-wrapper'>{html_subs}</div></body></html>"
                        page.set_content(html_content)
                        frame_path = os.path.join(temp_dir, f"f_{frame_idx}.png").replace("\\", "/")
                        page.screenshot(path=frame_path, omit_background=True, scale="css")
                        write_subtitle_frame(frame_path, frame_duration)
                        frame_idx += 1

                    f_concat.write(ffconcat_file_entry(last_concat_file))

            self.sig_progress.emit(70)

            self.sig_log.emit(f"  [4/4] 最终封装: 根据 {a_mode.split(' ')[0]} 压制中...", "#cdd6f4")

            v_loop_path = os.path.join(temp_dir, "v_loop.txt").replace("\\", "/")
            with open(v_loop_path, 'w', encoding='utf-8') as f:
                media_loop_dur = max(0.0, float(v_stream_dur or 0.0))
                if media_loop_dur > 0.1:
                    remaining = content_dur
                    while remaining > 0.001:
                        part_dur = min(remaining, media_loop_dur)
                        f.write(ffconcat_inout_entry(v_path, 0, part_dur))
                        remaining -= part_dur
                else:
                    f.write(ffconcat_file_entry(v_path))

            has_audio_file = bool(a_path and os.path.exists(a_path))
            has_source_audio = has_audio_stream(v_path)
            v_gain = self.video_volume_gain(task)
            music_path = task.get("music_path", "")
            if music_path and not os.path.exists(music_path):
                music_path = ""
            music_gain = max(0.0, min(1.0, float(task.get("music_volume", self.music_volume_percent()) or 0) / 100.0))
            render_profile = get_render_profile()
            encoder_label = render_profile.get("encoder_label") or render_profile.get("encoder", "CPU x264")
            video_args = build_video_encoder_args(render_profile, quality="batch")
            self.sig_log.emit(f"  ⚙️ 渲染配置: {encoder_label}", "#89b4fa")

            args = ["-y", "-f", "concat", "-safe", "0", "-i", v_loop_path, "-f", "concat", "-safe", "0", "-i", concat_path]
            input_idx = 2
            audio_input_idx = None
            if has_audio_file:
                args.extend(["-i", a_path])
                audio_input_idx = input_idx
                input_idx += 1
            music_input_idx = None
            if music_path and music_gain > 0:
                args.extend(["-stream_loop", "-1", "-i", music_path])
                music_input_idx = input_idx
                input_idx += 1
                self.sig_log.emit(f"  🎼 批量配乐: {os.path.basename(music_path)} @ {int(music_gain * 100)}%", "#f9e2af")

            video_guard = f"tpad=stop_mode=clone:stop_duration={total_dur:.3f},trim=duration={total_dur:.3f},setpts=PTS-STARTPTS"
            sub_guard = f"tpad=stop_mode=clone:stop_duration={total_dur:.3f},trim=duration={total_dur:.3f},setpts=PTS-STARTPTS"
            layer_x, layer_y = ffmpeg_layer_overlay_xy()
            vf = (
                f"{ffmpeg_canvas_source(proj_w, proj_h, total_dur)};"
                f"[0:v]{ffmpeg_layer_scale_filter(1.0, proj_w, proj_h, fit='cover')},format=rgba,{video_guard}[fg];"
                f"[canvas][fg]overlay=x='{layer_x}':y='{layer_y}':eof_action=pass:format=auto[bg];"
                f"[1:v]format=rgba,scale={proj_w}:{proj_h}:flags=lanczos,{sub_guard}[sub];"
                f"[bg][sub]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[outv]"
            )

            wants_mix = ("混合" in a_mode) or ("配音" in a_mode and "静音" not in a_mode and "替换" not in a_mode)
            wants_keep = "保留" in a_mode
            audio_filter = ""
            audio_map = ""
            if wants_mix and has_audio_file:
                if has_source_audio and v_gain > 0:
                    audio_filter = f"[0:a]volume={v_gain:.3f}[va];[2:a]volume=1.000[aa];[va][aa]amix=inputs=2:duration=longest:normalize=0[outa]"
                else:
                    audio_filter = "[2:a]volume=1.000[outa]"
                audio_map = "[outa]"
            elif (wants_mix or wants_keep) and has_source_audio and v_gain > 0:
                audio_filter = f"[0:a]volume={v_gain:.3f}[outa]"
                audio_map = "[outa]"
            elif has_audio_file and not wants_keep:
                audio_filter = "[2:a]volume=1.000[outa]"
                audio_map = "[outa]"

            wants_replace = ("替换" in a_mode) or ("静音" in a_mode)
            audio_parts = []
            audio_sources = []
            if has_source_audio and not wants_replace and v_gain > 0:
                audio_parts.append(f"[0:a]volume={v_gain:.3f},atrim=duration={total_dur:.3f},asetpts=PTS-STARTPTS[va]")
                audio_sources.append("[va]")
            if audio_input_idx is not None and not wants_keep:
                audio_parts.append(f"[{audio_input_idx}:a]volume=1.000,atrim=duration={total_dur:.3f},asetpts=PTS-STARTPTS[aa]")
                audio_sources.append("[aa]")
            if music_input_idx is not None:
                audio_parts.append(f"[{music_input_idx}:a]volume={music_gain:.3f},atrim=duration={total_dur:.3f},asetpts=PTS-STARTPTS[ma]")
                audio_sources.append("[ma]")

            audio_filter = ""
            audio_map = ""
            if len(audio_sources) == 1:
                audio_parts.append(f"{audio_sources[0]}anull[outa]")
                audio_filter = ";".join(audio_parts)
                audio_map = "[outa]"
            elif len(audio_sources) > 1:
                audio_parts.append(f"{''.join(audio_sources)}amix=inputs={len(audio_sources)}:duration=longest:normalize=0,atrim=duration={total_dur:.3f},asetpts=PTS-STARTPTS[outa]")
                audio_filter = ";".join(audio_parts)
                audio_map = "[outa]"

            if audio_filter and audio_map:
                args.extend(["-filter_complex", f"{vf};{audio_filter}", "-map", "[outv]", "-map", audio_map] + video_args + ["-c:a", "aac", "-b:a", "192k", "-t", str(total_dur), out_path])
            else:
                args.extend(["-filter_complex", vf, "-map", "[outv]"] + video_args + ["-an", "-t", str(total_dur), out_path])

            proc = subprocess.run([get_ffmpeg_cmd()] + args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=0x08000000 if os.name == 'nt' else 0)
            if proc.returncode != 0: raise Exception(f"FFmpeg 渲染失败!")

            self.sig_log.emit(f"✅ {os.path.basename(v_path)} 交付成功！", "#a6e3a1")
            self.sig_progress.emit(100)
            self.sig_table_row_status.emit(t_idx, "✅ 完成", "#a6e3a1")

        except Exception as e:
            self.sig_log.emit(f"❌ 任务失败: {str(e)}", "#f38ba8")
            self.sig_table_row_status.emit(task["idx"], "❌ 失败", "#f38ba8")
        finally:
            try: shutil.rmtree(temp_dir)
            except: pass
            self.sig_file_done.emit()

    def _load_nlp_dict(self):
        dict_path = os.path.join(os.getcwd(), "nlp_dictionary.txt")
        default_words = [
            "a", "an", "the", "to", "in", "on", "at", "of", "for", "with", "from", "by", "about",
            "as", "into", "like", "through", "after", "over", "between", "out", "against", "during",
            "without", "before", "under", "around", "among", "and", "but", "or", "so", "because",
            "my", "your", "his", "her", "its", "our", "their", "this", "that", "these", "those",
            "is", "am", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
            "does", "did", "will", "would", "shall", "should", "can", "could", "may", "might", "must",
            "very", "too", "not"
        ]

        if not os.path.exists(dict_path):
            try:
                with open(dict_path, 'w', encoding='utf-8') as f:
                    for w in default_words: f.write(f"{w}\n")
            except: pass
            return set(default_words)

        custom_words = set()
        try:
            with open(dict_path, 'r', encoding='utf-8') as f:
                for line in f:
                    clean_line = line.split('#')[0].strip().lower()
                    if clean_line: custom_words.add(clean_line)
            return custom_words if custom_words else set(default_words)
        except: return set(default_words)

    def _tokenize_user_text_for_alignment(self, raw_text):
        return tokenize_display_text(raw_text)

    def _align_user_text_to_ai_words(self, ai_words, raw_text):
        return align_reference_text_to_timestamps(ai_words, raw_text)

    def process_words(self, words, mode, timing_mode=None):
        words = normalize_word_timestamps(words)
        NON_END_WORDS = self._load_nlp_dict()
        subs = []; curr = {"words": []}; puncts = ['.', '!', '?', ',', '，', '。', '！', '？']
        timing_mode = timing_mode or "J Cut (字幕稍后收尾)"
        sound_aligned = "对齐声音" in timing_mode
        fixed_count = fixed_word_count_for_chunk_mode(mode)
        exact_single_word = is_exact_single_word_chunk_mode(mode)
        precise_chunk_mode = exact_single_word or fixed_count > 0
        narrative_min_words, narrative_max_words = narrative_chunk_word_bounds(mode)
        narrative_merge_words = narrative_chunk_merge_words(mode)

        for i, w in enumerate(words):
            if not curr["words"]: curr["start"] = w["start"]
            curr["words"].append({"text": w["word"], "start": w["start"], "end": w["end"]})
            curr["end"] = w["end"]

            clean_w = re.sub(r'[^a-zA-Z0-9\']', '', w["word"]).lower()
            has_punct = any(w["word"].endswith(p) for p in puncts)
            is_last_word = (i == len(words) - 1)
            next_word = words[i + 1]["word"] if i + 1 < len(words) else ""
            next_start = words[i + 1]["start"] if i + 1 < len(words) else 9999.0
            silence_gap = next_start - curr["end"]
            curr_dur = curr["end"] - curr["start"]
            narrative_block = narrative_max_words > 0
            tiktok_smart = "智能听译" in mode or "4-6" in mode or "4-7" in mode

            smart_short = "智能重点" in mode or "3-4词为主" in mode
            natural_short = "自然短句" in mode or "1-4" in mode
            fixed_count = 0
            if not natural_short and not smart_short and not tiktok_smart and not narrative_block:
                if "短句快速" in mode or "1-3" in mode:
                    fixed_count = 3
                elif "双词" in mode or "2词" in mode:
                    fixed_count = 2
                elif "三词" in mode or "3词" in mode:
                    fixed_count = 3
                elif "四词" in mode or "4词" in mode:
                    fixed_count = 4

            fixed_count = fixed_word_count_for_chunk_mode(mode)
            exact_single_word = is_exact_single_word_chunk_mode(mode)
            precise_chunk_mode = exact_single_word or fixed_count > 0

            weak_words = {
                "i", "you", "he", "she", "we", "they", "a", "an", "the", "to", "of", "in", "on",
                "for", "and", "or", "but", "is", "am", "are", "was", "were", "be", "been", "do",
                "does", "did", "not", "would", "could", "should", "have", "has", "had", "it",
                "my", "your", "his", "her", "their", "our"
            }
            is_key_word = bool(clean_w) and clean_w not in weak_words and (
                len(clean_w) >= 7 or clean_w in FAITH_WORDS or clean_w.isupper()
            )

            if exact_single_word: is_break = True
            elif fixed_count: is_break = len(curr["words"]) >= fixed_count or silence_gap > 0.8
            elif narrative_block:
                narrative_hard_gap_min = max(6, narrative_min_words - 2)
                narrative_key_min = max(narrative_min_words + 2, narrative_max_words - 2)
                narrative_key_dur = 3.2 if narrative_max_words >= 18 else 2.6
                is_break = (
                    (silence_gap > 0.8 and len(curr["words"]) >= narrative_hard_gap_min) or
                    (has_punct and len(curr["words"]) >= narrative_min_words) or
                    (silence_gap > 0.42 and len(curr["words"]) >= narrative_min_words) or
                    (is_key_word and len(curr["words"]) >= narrative_key_min and (silence_gap > 0.16 or curr_dur > narrative_key_dur)) or
                    len(curr["words"]) >= narrative_max_words
                )
            elif tiktok_smart:
                is_break = (
                    silence_gap > 0.8 or
                    (has_punct and len(curr["words"]) >= 4) or
                    (silence_gap > 0.46 and len(curr["words"]) >= 3) or
                    (silence_gap > 0.28 and len(curr["words"]) >= 4) or
                    (is_key_word and len(curr["words"]) >= 5 and (silence_gap > 0.14 or curr_dur > 1.55)) or
                    len(curr["words"]) >= 7 or
                    (len(curr["words"]) >= 6 and curr_dur > 2.35)
                )
            elif smart_short:
                long_slot = (len(subs) + int(float(curr.get("start", 0.0)) * 10)) % 5 == 3
                is_break = (
                    silence_gap > 0.8 or
                    (has_punct and len(curr["words"]) >= 1) or
                    (is_key_word and len(curr["words"]) >= 4) or
                    (silence_gap > 0.42 and len(curr["words"]) >= 1 and is_key_word) or
                    (silence_gap > 0.28 and len(curr["words"]) >= 2) or
                    len(curr["words"]) >= 6 or
                    (len(curr["words"]) >= 4 and (not long_slot or silence_gap > 0.16 or curr_dur > 1.80)) or
                    (len(curr["words"]) >= 3 and curr_dur > 1.45)
                )
            elif natural_short:
                is_break = (
                    silence_gap > 0.8 or
                    (has_punct and len(curr["words"]) >= 1) or
                    (silence_gap > 0.30 and len(curr["words"]) >= 2) or
                    len(curr["words"]) >= 4 or
                    (len(curr["words"]) >= 3 and curr_dur > 1.35)
                )
            elif sound_aligned:
                is_break = (
                    (silence_gap > 0.55 and curr_dur >= 0.25) or
                    (silence_gap > 0.34 and len(curr["words"]) >= 2) or
                    (has_punct and silence_gap > 0.18 and curr_dur > 0.75) or
                    curr_dur >= 3.8 or
                    len(curr["words"]) >= 13
                )
            elif "3-5字" in mode:
                if has_punct or len(curr["words"]) >= 4:
                    if clean_w in NON_END_WORDS and not is_last_word and len(curr["words"]) < 8: is_break = False
                    else: is_break = True
                else: is_break = False
            else:
                if has_punct or len(curr["words"]) >= 10:
                    if clean_w in NON_END_WORDS and not is_last_word and len(curr["words"]) < 15: is_break = False
                    else: is_break = True
                else: is_break = False

            if not precise_chunk_mode and is_break and should_defer_subtitle_break_for_readability(
                w.get("word", ""),
                next_word,
                segment_word_count=len(curr["words"]),
                silence_gap=silence_gap,
                has_punct=has_punct,
                is_last_word=is_last_word,
            ):
                is_break = False

            if is_break:
                if sound_aligned and len(curr["words"]) >= 6:
                    mid = len(curr["words"]) // 2
                    curr["words"][mid]["text"] = "\n" + curr["words"][mid]["text"].lstrip()
                curr["text"] = format_subtitle_text_spacing(" ".join([x["text"] for x in curr["words"]]))
                curr["text"] = curr["text"].replace(" \n", "\n").replace("\n ", "\n")
                curr["pos_x"] = 0.0; curr["pos_y"] = 25.0; curr["track"] = 1
                subs.append(curr); curr = {"words": []}

        if curr["words"]:
            if sound_aligned and len(curr["words"]) >= 6:
                mid = len(curr["words"]) // 2
                curr["words"][mid]["text"] = "\n" + curr["words"][mid]["text"].lstrip()
            curr["text"] = format_subtitle_text_spacing(" ".join([x["text"] for x in curr["words"]]))
            curr["text"] = curr["text"].replace(" \n", "\n").replace("\n ", "\n")
            curr["pos_x"] = 0.0; curr["pos_y"] = 25.0; curr["track"] = 1
            subs.append(curr)

        if not precise_chunk_mode and (narrative_block or "长句" in mode or "约10" in mode):
            subs = merge_single_word_subtitle_segments(subs, max_merged_words=narrative_merge_words if narrative_block else 14)

        subs = self._apply_timing_mode(subs, timing_mode)
        pacing_merge_words = pacing_merge_word_limit_for_chunk_mode(mode)
        subs = protect_fast_subtitle_pacing(
            subs,
            allow_merge=pacing_merge_words > 0,
            max_merged_words=pacing_merge_words or 1,
        )
        return subs

    def _apply_timing_mode(self, subs, timing_mode):
        if not subs:
            return subs
        if "对齐声音" in timing_mode:
            start_pad, end_pad = 0.0, 0.03
        elif "L Cut" in timing_mode:
            start_pad, end_pad = 0.12, 0.04
        else:
            start_pad, end_pad = 0.02, 0.16

        original_starts = [float(s.get("start", 0.0)) for s in subs]
        for i, s in enumerate(subs):
            raw_start = float(s.get("start", 0.0))
            raw_end = float(s.get("end", raw_start + 0.3))
            new_start = max(0.0, raw_start - start_pad)
            new_end = raw_end + end_pad
            if i + 1 < len(subs):
                next_start = max(0.0, original_starts[i + 1] - start_pad)
                new_end = min(new_end, max(new_start + 0.05, next_start - 0.01))
            if new_end <= new_start:
                new_end = new_start + 0.05
            s["start"] = new_start
            s["end"] = new_end
        return subs

    @pyqtSlot()
    def _on_file_done(self):
        self.current_idx += 1
        if self.batch_cancel_requested:
            self.batch_finish_reason = "cancelled"
        self.process_next()

    @pyqtSlot()
    def _on_all_done(self):
        was_cancelled = self.batch_finish_reason == "cancelled" or self.batch_cancel_requested
        self.is_running = False
        self.batch_pause_requested = False
        self.batch_cancel_requested = False
        self._set_batch_run_controls(False, "批量状态：已取消" if was_cancelled else "批量状态：空闲")
        btn_start_table = self.findChild(QPushButton, "🚀 开始批量流水线")
        if btn_start_table: btn_start_table.setEnabled(True)
        if was_cancelled:
            self.log_console.append("<span style='color:#f38ba8'>批量任务已取消，后续任务没有继续启动。</span>")
            QMessageBox.information(self, "批量已取消", "当前任务已收尾，后续批量任务已停止。")
            return
        self.log_console.append("🎉 所有矩阵任务圆满完成！")
        QMessageBox.information(self, "批量完成", "恭喜，矩阵批量生成完毕！")
