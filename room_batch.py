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
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
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
    merge_single_word_subtitle_segments,
    FAITH_WORDS
)
from project_io import create_reel, sync_project_assets_to_project_dir, update_room_state, save_project
from workspace_config import WORKSPACE_MODE_CLOUD, get_active_workspace, get_workspace_config
from job_control import CooperativeJobControl
from caption_presets import (
    REFERENCE_NARRATIVE_CHUNK_MODE,
    is_reference_narrative_chunk_mode,
    merge_built_in_style_presets,
)

PRESETS_FILE = resolve_user_file("style_presets.json", legacy_root=os.getcwd(), kind="config")
SIGNATURE_PRESETS_FILE = resolve_user_file("signature_presets.json", legacy_root=os.getcwd(), kind="config")
BATCH_QUEUE_BACKUPS_FILE = resolve_user_file("batch_queue_backups.json", legacy_root=os.getcwd(), kind="state")
STYLE_PRESET_POSITION_KEY = "__position__"
SUBTITLE_SUPERSAMPLE = subtitle_supersample()
MEDIA_EXTS = (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
TEXT_EXTS = (".txt", ".md", ".srt", ".vtt", ".ass", ".lrc")
BATCH_MUSIC_MODES = (
    ("顺序循环", "cycle"),
    ("随机分配", "random"),
    ("固定第一首", "first"),
)


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


def media_sequence_id(path_or_name):
    stem = os.path.splitext(os.path.basename(path_or_name or ""))[0].strip()
    match = re.match(r"^\s*0*(\d+)(?:[\s_.\-]+|$)", stem)
    return str(int(match.group(1))) if match else ""


def normalize_media_title(path_or_name):
    stem = os.path.splitext(os.path.basename(path_or_name or ""))[0].strip().lower()
    stem = re.sub(r"^\s*\d+(?:[\s_.\-]+|$)", "", stem)
    stem = re.sub(r"[\s_.\-]+", "", stem)
    return stem


def build_audio_lookup(input_dir):
    audio_files = sorted(
        [f for f in os.listdir(input_dir) if f.lower().endswith(AUDIO_EXTS)],
        key=natural_sort_key,
    )
    by_stem = {}
    by_seq = {}
    by_title = {}
    for name in audio_files:
        stem = os.path.splitext(name)[0]
        full_path = os.path.join(input_dir, name)
        by_stem.setdefault(stem.lower(), full_path)
        seq = media_sequence_id(name)
        if seq:
            by_seq.setdefault(seq, full_path)
        title = normalize_media_title(name)
        if title:
            by_title.setdefault(title, full_path)
    return {"by_stem": by_stem, "by_seq": by_seq, "by_title": by_title}


def list_audio_paths(input_dir):
    return [
        os.path.join(input_dir, f)
        for f in sorted(
            [name for name in os.listdir(input_dir) if name.lower().endswith(AUDIO_EXTS)],
            key=natural_sort_key,
        )
    ]


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
        self.setFixedHeight(96)
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(9, 6, 9, 6)
        row_layout.setSpacing(8)

        self.btn_vid = QPushButton("➕ 选画面")
        self.btn_vid.setFixedSize(88, 28)
        self.btn_vid.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_vid.clicked.connect(self.select_video)

        self.btn_aud = QPushButton("🎵 选配音")
        self.btn_aud.setFixedSize(88, 28)
        self.btn_aud.setStyleSheet("background-color: #cba6f7; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_aud.clicked.connect(self.select_audio)

        media_layout = QVBoxLayout()
        media_layout.setSpacing(5)
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

        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("大标题 (可选)")
        self.txt_title.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; padding: 5px;")
        self.txt_title.setFixedWidth(112)
        row_layout.addWidget(self.txt_title)

        self.txt_content = QTextEdit()
        self.txt_content.setPlaceholderText("详细正文文案 (支持多行/不填则盲听)")
        self.txt_content.setStyleSheet("background-color: #11111b; color: #a6adc8; border: 1px solid #313244; padding: 5px;")
        self.txt_content.setFixedHeight(64)
        row_layout.addWidget(self.txt_content, stretch=1)
        
        # 👑 新增：预览按钮
        self.btn_preview = QPushButton("👁️ 预览")
        self.btn_preview.setFixedSize(68, 36)
        self.btn_preview.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; border-radius: 4px; border: none;")
        self.btn_preview.clicked.connect(self.preview_frame)
        row_layout.addWidget(self.btn_preview)

        self.lbl_status = QLabel("待处理")
        self.lbl_status.setFixedWidth(60)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("color: #a6adc8; border: none;")
        row_layout.addWidget(self.lbl_status)

        self.btn_del = QPushButton("X")
        self.btn_del.setToolTip("删除这一行")
        self.btn_del.setFixedSize(36, 36)
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

        btn_new_queue = QPushButton("新增队列")
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
        self.chunk_mode.addItems(["单字轰炸 (1字/句)", "智能重点短句 (3-4词为主)", "智能听译 (4-7词，适配双行按词)", REFERENCE_NARRATIVE_CHUNK_MODE, "自然短句 (1-4词)", "双词节奏 (2词/句)", "三词短句 (3词/句)", "四词短句 (4词/句)", "短句快闪 (3-5字)", "长句大段 (约10字)"])
        self.chunk_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.chunk_mode)

        top_header.addWidget(QLabel("🎚️ 时间:", styleSheet="color: #cba6f7; font-weight: bold; margin-left: 10px;"))
        self.timing_mode = QComboBox()
        self.timing_mode.addItems(["L Cut (字幕提前进入)", "J Cut (字幕稍后收尾)", "对齐声音 (按停顿)"])
        self.timing_mode.setStyleSheet("background-color: #313244; color: #cdd6f4; padding: 5px 10px; font-weight: bold; border-radius: 5px;")
        top_header.addWidget(self.timing_mode)
        
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
            "video_volume": self.video_volume_percent() if hasattr(self, "video_volume_spin") else 20,
            "music_enabled": bool(getattr(self, "chk_batch_music", None) and self.chk_batch_music.isChecked()),
            "music_path": getattr(self, "batch_music_path", ""),
            "music_paths": self._current_batch_music_paths() if hasattr(self, "batch_music_paths") else [],
            "music_mode": self._batch_music_mode() if hasattr(self, "batch_music_mode_combo") else "cycle",
            "music_volume": self.music_volume_percent() if hasattr(self, "batch_music_volume_spin") else 35,
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
            "video_volume": self.video_volume_percent(),
            "music_enabled": bool(self.chk_batch_music.isChecked()) if hasattr(self, "chk_batch_music") else False,
            "music_path": self.batch_music_path,
            "music_paths": self._current_batch_music_paths(),
            "music_mode": self._batch_music_mode(),
            "music_volume": self.music_volume_percent(),
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
            self._set_video_volume(int(state.get("video_volume", 20)), enabled=True)
            self._set_batch_music_paths(state.get("music_paths") or state.get("music_path", ""))
            self._set_batch_music_mode(state.get("music_mode", "cycle"))
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
        return sum(1 for row in state.get("table_rows", []) if row.get("video"))

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
        
        lbl = QLabel("去 Excel / 飞书 / 腾讯文档 选中内容按 Ctrl+C，在这里 Ctrl+V：\n👉 单列：只填正文；两列：大标题 + 正文\n👉 也支持：视频路径 / 配音路径 / 字幕Y值 / 大标题 / 正文")
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
                
            row_widgets = []
            for i in range(self.table_layout.count()):
                w = self.table_layout.itemAt(i).widget()
                if isinstance(w, BatchTaskRow): row_widgets.append(w)
                    
            if auto_add:
                while len(row_widgets) < len(lines):
                    self.add_table_row()
                    w = self.table_layout.itemAt(self.table_layout.count()-1).widget()
                    row_widgets.append(w)
                    
            for i, parts in enumerate(lines):
                if i >= len(row_widgets): break
                if not parts: continue
                row_obj = row_widgets[i]
                
                values = [str(p or "").strip() for p in parts]
                video_col = next((v for v in values if looks_media_path(v)), "")
                audio_col = next((v for v in values if looks_audio_path(v)), "")
                text_col = next((v for v in values if looks_text_path(v)), "")
                y_value = None
                def is_numeric_cell(value):
                    try:
                        float(str(value or "").strip().replace(",", "."))
                        return True
                    except Exception:
                        return False
                for v in values:
                    try:
                        y_value = float(v.replace(",", "."))
                        break
                    except Exception:
                        pass
                plain_values = [
                    v for v in values
                    if v and v not in (video_col, audio_col, text_col) and not looks_media_path(v) and not looks_audio_path(v) and not looks_text_path(v) and not is_numeric_cell(v)
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
                    if plain_values:
                        if body_text:
                            title_text = plain_values[0]
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
                elif len(parts) >= 2:
                    row_obj.txt_title.setText(parts[0].strip())
                    row_obj.txt_content.setPlainText(parts[1].strip())
                elif len(parts) == 1:
                    row_obj.txt_content.setPlainText(parts[0].strip())
                    
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
        
        btn_batch_vid.clicked.connect(self.batch_select_videos)
        btn_batch_aud.clicked.connect(self.batch_select_audios)
        btn_paste.clicked.connect(lambda: self.open_paste_dialog(auto_add=True))
        
        btn_start_table = QPushButton("🚀 建工程并导出")
        btn_start_table.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-size: 15px; font-weight: bold; padding: 7px 18px; border-radius: 4px;")
        btn_start_table.clicked.connect(self.start_table_batch)

        btn_build_projects = QPushButton("开始创建工程")
        btn_build_projects.setStyleSheet("background-color: #f9e2af; color: #11111b; font-size: 15px; font-weight: bold; padding: 7px 18px; border-radius: 4px;")
        btn_build_projects.clicked.connect(self.start_table_project_build)

        toolbar.addWidget(btn_batch_vid); toolbar.addWidget(btn_batch_aud); toolbar.addWidget(btn_paste)
        toolbar.addStretch(); toolbar.addWidget(btn_build_projects); toolbar.addWidget(btn_start_table)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(230)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.table_scroll = scroll
        self.table_content = QWidget()
        self.table_layout = QVBoxLayout(self.table_content)
        self.table_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.table_layout.setContentsMargins(0, 0, 0, 0)
        self.table_layout.setSpacing(6)
        scroll.setWidget(self.table_content)
        layout.addWidget(scroll)

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

    def _set_row_title_for_pair(self, row, video_path="", audio_path=""):
        if audio_path:
            row.txt_title.setText(file_stem(audio_path))
        elif video_path and not row.txt_title.text().strip():
            row.txt_title.setText(file_stem(video_path))

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
        paths = sorted(paths, key=natural_sort_key)
        video_paths = self._existing_video_paths()
        append_start = self._last_audio_row_index() + 1
        rows = self._ensure_table_rows(append_start + len(paths))
        for i, path in enumerate(paths):
            row = rows[append_start + i]
            if video_paths:
                row.set_video_path(video_paths[i % len(video_paths)])
            row.set_audio_path(path)
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

    def _table_tasks_from_state(self, state):
        tasks = []
        preset_pos_x, _ = self._load_preset_position_by_name(state.get("preset_name", ""), default_y=float(state.get("subtitle_y", 25.0) or 25.0))
        preset_style = self._load_preset_style_by_name(state.get("preset_name", ""))
        signature = self._load_signature_preset_by_name(state.get("signature_preset_name", ""))
        music_payload = self._batch_music_payload(state)
        for i, row in enumerate(state.get("table_rows", [])):
            if row.get("video"):
                task_order = len(tasks)
                tasks.append({
                    "type": "table",
                    "idx": i,
                    "video": row.get("video", ""),
                    "audio": row.get("audio", ""),
                    "title": row.get("title", ""),
                    "text": row.get("text", ""),
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
            return QMessageBox.warning(self, "提示", "表格中没有任何有效画面！")
        self._start_project_build(tasks, "表格建工程并导出", auto_render=True)

    def start_table_project_build(self):
        if self.is_running: return
        self._capture_current_queue_state()
        tasks = self._tasks_from_queue_state(self.batch_queues[self.current_queue_index])
        for row_widget in self._table_rows():
            row_widget.sync_paths_from_fields()
            if not row_widget.video_path:
                row_widget.lbl_status.setText("略过:无画面")
        if not tasks:
            return QMessageBox.warning(self, "提示", "表格中没有任何有效画面，无法建立工程。")
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
        audio_lookup = build_audio_lookup(self.input_dir)
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
        audio_lookup = build_audio_lookup(self.input_dir)
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
        audio_paths = list_audio_paths(self.input_dir)
        audio_lookup = build_audio_lookup(self.input_dir)
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
                "video": task.get("video", ""),
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
                "video", "audio", "video_volume", "music", "music_volume", "music_mode", "title", "subtitle_x", "subtitle_y", "text_chars", "error",
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

    def _build_single_project(self, task, project_dir, preset_style, c_mode, timing_mode):
        video_path = task.get("video", "")
        audio_path = task.get("audio", "")
        if not video_path or not os.path.exists(video_path):
            raise Exception("视频路径不存在")

        title = project_title_from_task(task.get("title", ""), video_path)
        base_name = title or os.path.splitext(os.path.basename(video_path))[0]
        reel_name = self._unique_reel_name(project_dir, base_name)
        project_data = create_reel(project_dir, reel_name, "edit_room")
        if task.get("batch_record"):
            project_data["batch_record"] = task.get("batch_record")

        video_dur = get_exact_duration(video_path) or 5.0
        video_stream_dur = get_video_stream_duration(video_path) or video_dur
        audio_dur = get_exact_duration(audio_path) if audio_path and os.path.exists(audio_path) else 0.0
        audio_mode = task.get("a_mode") or self.audio_mode.currentText()
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
        if not custom_text and title:
            custom_text = title

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

        edit_state = {
            "video_clips": [{"path": video_path, "start": 0.0, "end": content_dur, "dur": video_stream_dur}],
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
        accounts = local_get_cf_accounts()
        if not accounts:
            raise Exception("未配置 Cloudflare API 凭证")

        temp_audio = os.path.join(tempfile.gettempdir(), f"sh_project_build_{threading.get_ident()}.mp3")
        try:
            cmd = [get_ffmpeg_cmd(), "-y", "-i", target_path, "-vn", "-map", "a:0?", "-ar", "16000", "-ac", "1", "-b:a", "16k", temp_audio]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)
            if not os.path.exists(temp_audio) or os.path.getsize(temp_audio) <= 100:
                raise Exception("音频抽取失败")
            with open(temp_audio, "rb") as f:
                data = f.read()

            res_json = None
            last_err = ""
            for acc in accounts:
                if acc.get("id") and acc.get("token"):
                    try:
                        res = requests.post(
                            f"https://api.cloudflare.com/client/v4/accounts/{acc['id']}/ai/run/@cf/openai/whisper",
                            headers={"Authorization": f"Bearer {acc['token']}", "Content-Type": "application/octet-stream"},
                            data=data,
                            timeout=60
                        )
                        if res.status_code == 200 and res.json().get("success"):
                            res_json = res.json()
                            break
                        last_err = f"HTTP {res.status_code}: {res.text[:100]}"
                    except Exception as e:
                        last_err = str(e)
            if not res_json:
                raise Exception(f"AI 请求失败: {last_err}")
            return normalize_word_timestamps([
                {"word": re.sub(r'(?i)stereo_[^\s]+', '', w["word"]).replace(".mp3", "").replace(".wav", "").strip(), "start": w["start"], "end": w["end"]}
                for w in res_json["result"]["words"]
                if re.sub(r'(?i)stereo_[^\s]+', '', w["word"]).strip()
            ])
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
        
        threading.Thread(target=self.pipeline_worker, args=(task, out_path, c_mode, timing_mode), daemon=True).start()

    def pipeline_worker(self, task, out_path, c_mode, timing_mode):
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
            self.sig_log.emit(f"  [2/4] 呼叫 Cloudflare 大模型...", "#cdd6f4")

            accounts = local_get_cf_accounts()
            if not accounts: raise Exception("未配置 Cloudflare API 凭证！")

            res_json = None; last_err = ""
            with open(temp_audio, 'rb') as f: data = f.read()
            for acc in accounts:
                if acc.get("id") and acc.get("token"):
                    try:
                        res = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acc['id']}/ai/run/@cf/openai/whisper", headers={"Authorization": f"Bearer {acc['token']}", "Content-Type": "application/octet-stream"}, data=data, timeout=60) 
                        if res.status_code == 200 and res.json().get("success"): res_json = res.json(); break 
                    except Exception as e: last_err = str(e)
            if not res_json: raise Exception(f"AI 请求失败: {last_err}")

            clean_words = normalize_word_timestamps([
                {"word": re.sub(r'(?i)stereo_[^\s]+', '', w["word"]).strip(), "start": w["start"], "end": w["end"]}
                for w in res_json["result"]["words"]
                if re.sub(r'(?i)stereo_[^\s]+', '', w["word"]).strip()
            ])

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
            narrative_block = is_reference_narrative_chunk_mode(mode)
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

            weak_words = {
                "i", "you", "he", "she", "we", "they", "a", "an", "the", "to", "of", "in", "on",
                "for", "and", "or", "but", "is", "am", "are", "was", "were", "be", "been", "do",
                "does", "did", "not", "would", "could", "should", "have", "has", "had", "it",
                "my", "your", "his", "her", "their", "our"
            }
            is_key_word = bool(clean_w) and clean_w not in weak_words and (
                len(clean_w) >= 7 or clean_w in FAITH_WORDS or clean_w.isupper()
            )

            if "单字" in mode: is_break = True
            elif fixed_count: is_break = len(curr["words"]) >= fixed_count or silence_gap > 0.8
            elif narrative_block:
                is_break = (
                    (silence_gap > 0.8 and len(curr["words"]) >= 6) or
                    (has_punct and len(curr["words"]) >= 8) or
                    (silence_gap > 0.42 and len(curr["words"]) >= 8) or
                    (is_key_word and len(curr["words"]) >= 10 and (silence_gap > 0.16 or curr_dur > 2.6)) or
                    len(curr["words"]) >= 12
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

            if is_break and should_defer_subtitle_break_for_readability(
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

        if narrative_block or "长句" in mode or "约10" in mode:
            subs = merge_single_word_subtitle_segments(subs, max_merged_words=14)

        return self._apply_timing_mode(subs, timing_mode)

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
