# ==========================================
# 文件名: room_edit.py (加入 Ctrl+Z 时光机 & AI文案智能清洗)
# ==========================================
import os
import json
import tempfile
import threading
import requests
import re
import shutil
import subprocess
import zipfile
import sys
import copy
import time
import html
import hashlib
import random

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QTextEdit, QScrollArea, QTabWidget, QComboBox,
                             QSlider, QFileDialog, QGridLayout, QFrame,
                             QCheckBox, QMessageBox, QColorDialog, QFontComboBox,
                             QStackedWidget, QDoubleSpinBox, QSpinBox, QSplitter, QInputDialog, QProgressDialog, QLineEdit, QSizePolicy, QDialog,
                             QListWidget, QListWidgetItem, QMenu)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSlot, pyqtSignal, QLocale, QEvent, QObject, QSize
from PyQt6.QtGui import QPainter, QPixmap, QKeySequence, QShortcut, QIcon, QFontDatabase, QPen, QDesktopServices
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import QRectF

from timeline_engine import TimelineHeader, AdvancedTimeline, TRACK_H, HEADER_H, TRACK_COUNT
from core import get_ffmpeg_cmd, get_ffprobe_cmd, get_app_dir, FFMPEG_DOWNLOAD_URL, download_file_with_progress
from app_theme import apply_tinted_styles
from room_theme_bridge import apply_room_theme_bridge
from ui_components import (hex_to_rgb, get_exact_duration, get_video_dimensions, get_video_stream_duration,
                           get_video_import_metadata,
                           AspectRatioContainer, default_signature_config,
                           default_design_room_state, normalize_design_room_state,
                           normalize_signature_config, render_design_html, render_signature_html,
                           render_subtitle_html,
                           rebalance_subtitle_layout, tokenize_display_text,
                           normalize_word_timestamps, align_reference_text_to_timestamps,
                           format_subtitle_text_spacing, normalize_scripture_quote_text,
                           should_defer_subtitle_break_for_readability,
                           merge_single_word_subtitle_segments, protect_fast_subtitle_pacing,
                           FAITH_WORDS)
from project_io import copy_media_to_project_assets, load_project, save_project, sync_project_assets_to_project_dir, update_room_state
from app_config import (
    OUTPUT_RESOLUTION_OPTIONS,
    PREVIEW_PROXY_RESOLUTION_OPTIONS,
    get_output_resolution,
    get_preview_fullscreen_shortcut,
    get_preview_proxy_resolution,
    load_app_config,
    preview_proxy_settings,
    resolution_to_size,
    set_preview_proxy_resolution,
)
from ai_transcription import transcribe_audio_words
from app_storage import read_json_file, resolve_user_file, write_json_file
from font_assets import font_face_css
from render_pipeline_model import canvas_layer_rect
from render_timing import render_tail_padding_seconds
from image_asset_cache import DEFAULT_IMAGE_PROXY_MAX_SIDE, ensure_downscaled_image
from media_pool_panel import MediaPoolPanel
from caption_presets import (
    LEGACY_NARRATIVE_BLOCK_PRESET,
    LEGACY_NARRATIVE_CHUNK_MODE,
    REFERENCE_NARRATIVE_BLOCK_PRESET,
    REFERENCE_NARRATIVE_CHUNK_MODE,
    built_in_style_presets,
    fixed_word_count_for_chunk_mode,
    pacing_merge_word_limit_for_chunk_mode,
    is_exact_single_word_chunk_mode,
    is_reference_narrative_chunk_mode,
    merge_built_in_style_presets,
    narrative_chunk_merge_words,
    narrative_chunk_word_bounds,
)
from preview_proxy import (
    PROXY_STATUS_FAILED,
    PROXY_STATUS_GENERATING,
    PROXY_STATUS_PENDING,
    PROXY_STATUS_READY,
    build_preview_proxy_command,
    clip_should_auto_proxy,
    prepare_clip_for_preview_proxy,
    preview_proxy_is_ready,
    preview_source_for_clip,
)
from font_registry import (
    STATUS_NONCOMMERCIAL,
    STATUS_OPEN,
    STATUS_APPROVED,
    STATUS_REVIEW,
    STATUS_SYSTEM,
    font_record_for,
    is_safe_font,
    safe_font_keys,
)

CACHE_FILE = resolve_user_file("sh_v8_project_cache.json", legacy_root=tempfile.gettempdir(), kind="cache")
PRESETS_FILE = resolve_user_file("style_presets.json", legacy_root=os.getcwd(), kind="config")
SIGNATURE_PRESETS_FILE = resolve_user_file("signature_presets.json", legacy_root=os.getcwd(), kind="config")
LAYOUT_PRESETS_FILE = resolve_user_file("layout_presets.json", legacy_root=os.getcwd(), kind="config")
STYLE_PRESET_POSITION_KEY = "__position__"

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

def local_get_cf_accounts():
    return load_app_config().get("cf_accounts", [])

# ==========================================
# 👑 滚轮屏蔽组件：强制鼠标滚轮穿透，只滚动页面不改参数
# ==========================================
class NoScrollComboBox(QComboBox):
    def wheelEvent(self, event): event.ignore()

class NoScrollFontComboBox(QFontComboBox):
    def wheelEvent(self, event): event.ignore()

class NoScrollSlider(QSlider):
    def wheelEvent(self, event): event.ignore()

class ProScrubSpinBox(QSpinBox):
    def __init__(self, project_data=None, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().setCursor(Qt.CursorShape.SizeHorCursor)
        self._is_dragging = False
        self._last_x = 0
        self.lineEdit().installEventFilter(self)

    def wheelEvent(self, event):
        event.ignore() # 👑 强制屏蔽滚轮

    def eventFilter(self, obj, event):
        if obj == self.lineEdit():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._is_dragging = True; self._last_x = event.globalPosition().x()
            elif event.type() == QEvent.Type.MouseMove and self._is_dragging:
                dx = event.globalPosition().x() - self._last_x
                if abs(dx) >= 1.0:
                    self.blockSignals(True); self.setValue(self.value() + int(dx) * self.singleStep()); self.blockSignals(False)
                    self._last_x = event.globalPosition().x()
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._is_dragging = False; self.valueChanged.emit(self.value())
                if hasattr(self.parent(), "push_history"): self.parent().push_history()
        return super().eventFilter(obj, event)

class ProScrubDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, project_data=None, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().setCursor(Qt.CursorShape.SizeHorCursor)
        self._is_dragging = False
        self._last_x = 0
        self.lineEdit().installEventFilter(self)

    def wheelEvent(self, event):
        event.ignore() # 👑 强制屏蔽滚轮

    def eventFilter(self, obj, event):
        if obj == self.lineEdit():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._is_dragging = True; self._last_x = event.globalPosition().x()
            elif event.type() == QEvent.Type.MouseMove and self._is_dragging:
                dx = event.globalPosition().x() - self._last_x
                if abs(dx) >= 1.0:
                    self.blockSignals(True); self.setValue(self.value() + dx * self.singleStep()); self.blockSignals(False)
                    self._last_x = event.globalPosition().x()
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._is_dragging = False; self.valueChanged.emit(self.value())
                if hasattr(self.parent(), "push_history"): self.parent().push_history()
        return super().eventFilter(obj, event)


# 👑 高级丝滑折叠抽屉组件 (Accordion UI) - 纤细优化版
class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None, expanded=False):
        super().__init__(parent)
        self.toggle_button = QPushButton(f"▶  {title}")
        self.toggle_button.setStyleSheet("""
            QPushButton {
                text-align: left; padding: 6px 12px; font-weight: bold;
                font-size: 13px; background-color: #232634; color: #cdd6f4;
                border: 1px solid #313244; border-radius: 6px;
            }
            QPushButton:hover { background-color: #313244; border-color: #89b4fa; }
            QPushButton:checked { color: #a6e3a1; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; border-bottom: none;}
        """)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False) # 默认折叠状态

        self.content_area = QFrame()
        self.content_area.setStyleSheet("QFrame { background-color: #181825; border: 1px solid #313244; border-top: none; border-bottom-left-radius: 6px; border-bottom-right-radius: 6px; }")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(10, 8, 10, 10)
        self.content_layout.setSpacing(6)
        self.content_area.setVisible(False)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 2, 0, 2)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.toggle_button)
        main_layout.addWidget(self.content_area)

        self.toggle_button.clicked.connect(self.on_pressed)
        if expanded:
            self.toggle_button.setChecked(True)
            self.on_pressed()

    def on_pressed(self):
        checked = self.toggle_button.isChecked()
        title_text = self.toggle_button.text()[3:] # 去掉前面的箭头和空格
        self.toggle_button.setText(f"{'▼' if checked else '▶'}  {title_text}")
        self.content_area.setVisible(checked)

    def addLayout(self, layout):
        self.content_layout.addLayout(layout)

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)

class PreviewWorkspace(QFrame):
    def __init__(self, child_widget, controller=None, parent=None):
        super().__init__(parent)
        self.child_widget = child_widget
        self.controller = controller
        self.ratio = 1080 / 1920
        self.view_zoom = 1.0
        self.view_pan_x = 0.0
        self.view_pan_y = 0.0
        self._is_panning = False
        self._last_pan_pos = None
        self.child_widget.setParent(self)
        self.child_widget.show()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("QFrame { background-color: #07080d; border: 1px solid #171b28; border-radius: 6px; }")

    def set_ratio(self, w, h):
        if h == 0:
            return
        self.ratio = max(0.05, float(w) / float(h))
        self.update_stage_geometry()

    def set_view_transform(self, zoom, pan_x, pan_y):
        self.view_zoom = max(0.25, float(zoom or 1.0))
        self.view_pan_x = float(pan_x or 0.0)
        self.view_pan_y = float(pan_y or 0.0)
        self.update_stage_geometry()
        self.update()

    def base_stage_size(self):
        margin = 42 if self.width() >= 620 and self.height() >= 420 else 24
        avail_w = max(80, self.width() - margin * 2)
        avail_h = max(80, self.height() - margin * 2)
        if avail_w / avail_h > self.ratio:
            base_h = avail_h
            base_w = base_h * self.ratio
        else:
            base_w = avail_w
            base_h = base_w / self.ratio
        return max(64, int(base_w)), max(64, int(base_h))

    def stage_size_for_zoom(self, zoom=None):
        base_w, base_h = self.base_stage_size()
        z = max(0.25, float(self.view_zoom if zoom is None else zoom))
        return max(64, int(base_w * z)), max(64, int(base_h * z))

    def update_stage_geometry(self):
        if not self.child_widget:
            return
        stage_w, stage_h = self.stage_size_for_zoom()
        x = int((self.width() - stage_w) * 0.5 + self.view_pan_x)
        y = int((self.height() - stage_h) * 0.5 + self.view_pan_y)
        self.child_widget.setGeometry(x, y, stage_w, stage_h)
        self.child_widget.raise_()

    def resizeEvent(self, event):
        self.update_stage_geometry()
        if self.controller:
            QTimer.singleShot(0, self.controller.refresh_preview_layout)
        super().resizeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#07080d"))
        grid_color = QColor("#171b28")
        grid_color.setAlpha(95)
        painter.setPen(QPen(grid_color, 1))
        step = 32
        ox = int((self.width() * 0.5 + self.view_pan_x) % step)
        oy = int((self.height() * 0.5 + self.view_pan_y) % step)
        for x in range(ox, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(oy, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
        if self.child_widget:
            g = self.child_widget.geometry()
            shadow = QColor("#000000")
            shadow.setAlpha(120)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(QRectF(g).adjusted(-14, -12, 14, 16), 8, 8)
        painter.end()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta or not self.controller:
            event.ignore()
            return
        pos = event.position()
        self.controller.adjust_preview_zoom(1 if delta > 0 else -1, pos.x(), pos.y())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            if self.controller:
                self.controller.show_canvas_context_toolbar("canvas")
            self._is_panning = True
            self._last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning and self._last_pan_pos is not None and self.controller:
            pos = event.position()
            dx = pos.x() - self._last_pan_pos.x()
            dy = pos.y() - self._last_pan_pos.y()
            self._last_pan_pos = pos
            self.controller.pan_preview_view(dx, dy)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_panning and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._is_panning = False
            self._last_pan_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self.controller:
                self.controller.finalize_preview_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.controller:
            self.controller.reset_preview_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        if self.controller:
            self.controller.dragEnterEvent(event)
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if self.controller:
            self.controller.dropEvent(event)
            return
        super().dropEvent(event)

class WebBridge(QObject):
    def __init__(self, parent_controller):
        super().__init__()
        self.controller = parent_controller

    @pyqtSlot(float, float, float)
    def adjust_monitor_zoom(self, delta_y, anchor_x=0.0, anchor_y=0.0):
        self.controller.adjust_preview_zoom_from_stage(-1 if delta_y > 0 else 1, anchor_x, anchor_y)

    @pyqtSlot(float, float)
    def pan_monitor_view(self, dx, dy):
        self.controller.pan_preview_view(dx, dy)

    @pyqtSlot()
    def reset_monitor_view(self):
        self.controller.reset_preview_view()

    @pyqtSlot()
    def finalize_monitor_pan(self):
        self.controller.finalize_preview_pan()

    @pyqtSlot()
    def show_context_toolbar(self):
        self.controller.show_canvas_context_toolbar("canvas")

    @pyqtSlot(int, float, float)
    def update_coordinates(self, idx, x, y):
        if not getattr(self.controller, "edit_mode", False):
            return
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                c["pos_x"] = x; c["pos_y"] = y

            if self.controller.current_selected_idx == idx:
                self.controller.pos_x_spin.blockSignals(True); self.controller.pos_x_slider.blockSignals(True)
                self.controller.pos_y_spin.blockSignals(True); self.controller.pos_y_slider.blockSignals(True)

                self.controller.pos_x_spin.setValue(float(x)); self.controller.pos_x_slider.setValue(int(float(x) * 100))
                self.controller.pos_y_spin.setValue(float(y)); self.controller.pos_y_slider.setValue(int(float(y) * 100))

                self.controller.pos_x_spin.blockSignals(False); self.controller.pos_x_slider.blockSignals(False)
                self.controller.pos_y_spin.blockSignals(False); self.controller.pos_y_slider.blockSignals(False)

            self.controller.update_floating_subtitle()
            self.controller.auto_save_cache()
            self.controller.push_history()

    @pyqtSlot(int, float)
    def update_box_width(self, idx, width):
        if not getattr(self.controller, "edit_mode", False):
            return
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                if "style" not in c: c["style"] = self.controller.default_style.copy()
                c["style"]["box_width"] = width

            if self.controller.current_selected_idx == idx:
                self.controller.box_width_spin.blockSignals(True); self.controller.box_width_slider.blockSignals(True)
                self.controller.box_width_spin.setValue(float(width)); self.controller.box_width_slider.setValue(int(float(width) * 100))
                self.controller.box_width_spin.blockSignals(False); self.controller.box_width_slider.blockSignals(False)

            self.controller.update_floating_subtitle()
            self.controller.auto_save_cache()
            self.controller.push_history()

    @pyqtSlot(int)
    def notify_selected(self, idx):
        self.controller.current_selected_idx = idx
        self.controller.show_canvas_context_toolbar("subtitle")
        self.controller.switch_inspector("sub")
        self.controller.last_render_hash = None
        self.controller.update_floating_subtitle()

    @pyqtSlot(int, str)
    def update_text_from_screen(self, idx, new_text):
        if not getattr(self.controller, "edit_mode", False):
            return
        if 0 <= idx < len(self.controller.state["subs_data"]):
            self.controller.sync_text_edit(idx, new_text)
            self.controller.push_history()

    @pyqtSlot(int, int)
    def adjust_font_size(self, idx, delta):
        if not getattr(self.controller, "edit_mode", False):
            return
        if 0 <= idx < len(self.controller.state["subs_data"]):
            current_clip = self.controller.state["subs_data"][idx]
            st = current_clip.get("style", current_clip)
            new_size = max(10, min(300, st.get("size", 100) + delta))

            scope = self.controller.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.controller.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.controller.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

            for c in target_clips:
                if "style" not in c: c["style"] = {}
                c["style"]["size"] = new_size
            if self.controller.current_selected_idx == idx:
                self.controller.size_slider.blockSignals(True); self.controller.size_spin.blockSignals(True)
                self.controller.size_slider.setValue(new_size); self.controller.size_spin.setValue(new_size)
                self.controller.size_slider.blockSignals(False); self.controller.size_spin.blockSignals(False)
            self.controller.update_floating_subtitle(); self.controller.auto_save_cache()
            self.controller.push_history()

class EditView(QWidget):
    sig_ai_progress = pyqtSignal(str)
    sig_ai_success = pyqtSignal()
    sig_ai_error = pyqtSignal(str)
    sig_ai_finish = pyqtSignal()
    sig_ffmpeg_progress = pyqtSignal(int, str)
    sig_ffmpeg_success = pyqtSignal(str)
    sig_ffmpeg_error = pyqtSignal(str)
    sig_ffmpeg_canceled = pyqtSignal()

    def __init__(self, project_data=None, parent=None):
        super().__init__(parent)
        self.project_data = project_data or {}

        # 👑 时光机历史栈初始化
        self.history = []
        self.history_ptr = -1

        self.default_style = {
            "size": 100, "font": "Noto Sans SC", "font_weight": "700", "font_style": "normal", "color_txt": "#FFFFFF", "color_hl": "#FFFFFF",
            "bg_mode": "none", "bg_color": "#000000", "bg_alpha": 80, "bg_radius": 15, "bg_padding": 20, "bg_auto_resolution": True,
            "hl_bg_color": "#FF0050", "hl_bg_alpha": 100, "hl_bg_radius": 8, "hl_bg_padding": 8, "hl_trail_words": 1, "hl_trail_min_alpha": 35, "word_visual_min_seconds": 0.14,
            "stroke_width": 4, "stroke_color": "#000000", "stroke_o_width": 0, "stroke_o_color": "#000000", "stroke_softness": 0,
            "shadow_x": 5, "shadow_y": 5, "shadow_blur": 0, "shadow_color": "#000000", "shadow_alpha": 100,
            "line_height": 1.1, "layout_row_gap": 100, "text_dir": "ltr", "use_hl": True, "hl_style": "text", "hl_glow": False, "glow_size": 20,
            "anim_type": "pop", "font_motion": "none", "hl_motion": "stable", "pop_speed": 0.18, "pop_bounce": 128, "inactive_alpha": 100,
            "text_texture": "none", "text_3d_enable": False, "text_3d_depth": 0, "text_3d_x": 2, "text_3d_y": 3, "text_3d_color": "#6F3A05",
            "global_glow_enable": False, "global_glow_mode": "soft", "global_glow_motion": "stable",
            "global_glow_color": "#FFFFFF", "global_glow_size": 18, "global_glow_blur": 24,
            "global_glow_alpha": 35, "global_glow_x": 0, "global_glow_y": 0, "global_glow_z": 0,
            "hl_bg_skew": 0,
            "text_transform": "capitalize", "text_align": "center", "letter_spacing": 0, "word_spacing": 0,
            "layout_mode": "standard", "layout_variant": "auto", "box_layout": "fixed", "emphasis_scale": 145,
            "contrast_small_scale": 0.74, "layout_pattern": "auto", "smart_layout_pool": "contrast,narrative_block,reel_stack,random_focus,axis_stack",
            "layout_layer_count": 0, "layout_layer_pattern": "auto", "layout_layer_words": "auto",
            "axis_spread": 100, "axis_gap": 100,
            "box_width": 74.0, "box_height": 0.0, "max_lines": 2,
            "mask_en": False, "mask_top": 20, "mask_bottom": 20,
            "merge_bridge_enable": False, "merge_bridge_width": 160, "merge_bridge_height": 16, "merge_bridge_alpha": 100,
            "bg_pad_left": 20, "bg_pad_right": 20, "bg_pad_top": 8, "bg_pad_bottom": 8,
            "hl_pad_left": 8, "hl_pad_right": 8, "hl_pad_top": 2, "hl_pad_bottom": 2
        }
        self.state = {
            "video_clips": [], "audio_path": "", "music_path": "", "subs_data": [], "a_trim": [0.0, 10.0], "audio_source_in": 0.0, "duration": 10.0,
            "resolution": get_output_resolution(), "v_scale": 100, "v_volume": 100, "a_volume": 100, "music_volume": 35,
            "chunk_mode": "双行大段 (约10字，智能折行)",
            "timing_mode": "J Cut (字幕稍后收尾)",
            "fill_subtitle_gaps": True,
            "custom_text": "", # 👑 新增：用于保存用户文案到工程
            "project_tag": "",
            "default_pos_x": 0.0,
            "default_pos_y": 25.0,
            "default_style": self.default_style.copy(),
            "signature": default_signature_config(self.default_style)
        }
        self.current_selected_idx = -1; self.current_v_idx = 0; self.current_play_time = 0.0
        self.is_playing = False; self.ui_entries = []; self.selected_track = "empty"
        self.workspace_mode = "edit"
        self.selected_design_layer_id = ""
        self.edit_mode = True
        self._video_exts = (".mp4", ".mov", ".webm", ".mkv", ".avi")
        self._audio_exts = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
        self.zoom_factor = 50.0; self.timeline_snap_enabled = True; self.active_subs_cache = set(); self.last_render_hash = None
        self.preview_zoom = 1.0; self.preview_pan_x = 0.0; self.preview_pan_y = 0.0
        self.preview_overlay_enabled = True; self._preview_overlay_has_content = False; self._preview_frame_retry_pending = False; self._preview_frame_retry_count = 0
        self.v_wave_pixmap = None; self.a_wave_pixmap = None; self.video_thumbs = []; self.last_video_image = None
        self.proj_width = 1080; self.proj_height = 1920
        self.safe_font_only = False
        self.project_autosave_timer = QTimer(self)
        self.project_autosave_timer.setSingleShot(True)
        self.project_autosave_timer.timeout.connect(self.flush_project_autosave)
        self.project_autosave_busy = False
        self._preview_proxy_jobs = set()
        self.preview_proxy_auto_generate = True
        self.preview_proxy_resolution = get_preview_proxy_resolution()

        self.sig_ai_progress.connect(self._on_ai_progress); self.sig_ai_success.connect(self._on_ai_success)
        self.sig_ai_error.connect(self._on_ai_error); self.sig_ai_finish.connect(self._on_ai_finish)
        self.sig_ffmpeg_progress.connect(self._on_ffmpeg_progress)
        self.sig_ffmpeg_success.connect(self._on_ffmpeg_success)
        self.sig_ffmpeg_error.connect(self._on_ffmpeg_error)
        self.sig_ffmpeg_canceled.connect(self._on_ffmpeg_canceled)

        self.eng_locale = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
        self.init_ui()

    def _project_display_name(self):
        if isinstance(self.project_data, dict):
            return (
                self.project_data.get("project_name")
                or os.path.splitext(os.path.basename(self.project_data.get("project_path", "")))[0]
                or "未命名工程"
            )
        return "未命名工程"

    def _project_tag_text(self):
        project_data = self.project_data if isinstance(self.project_data, dict) else {}
        edit_state = project_data.get("room_state", {}).get("edit_room", {}) if isinstance(project_data, dict) else {}
        raw = project_data.get("project_tag") or edit_state.get("project_tag") or project_data.get("tag") or ""
        if not raw and isinstance(project_data.get("tags"), list) and project_data.get("tags"):
            raw = project_data.get("tags", [""])[0]
        return str(raw or "").strip()

    def refresh_project_header(self):
        if hasattr(self, "lbl_top_project_name"):
            self.lbl_top_project_name.setText(self._project_display_name())
        tag_text = self._project_tag_text()
        if hasattr(self, "lbl_top_project_tag"):
            self.lbl_top_project_tag.setText(f"🏷 {tag_text}" if tag_text else "")
            self.lbl_top_project_tag.setVisible(bool(tag_text))


    def _make_design_spin(self, min_v, max_v, step):
        spin = QSpinBox()
        spin.setRange(int(min_v), int(max_v))
        spin.setSingleStep(int(step))
        spin.setFixedHeight(27)
        spin.setStyleSheet("background:#10131b; color:#edf2f7; border:1px solid #31384d; border-radius:5px;")
        spin.valueChanged.connect(self._on_design_property_change)
        return spin

    def _make_design_double_spin(self, min_v, max_v, step, suffix=""):
        spin = QDoubleSpinBox()
        spin.setRange(float(min_v), float(max_v))
        spin.setSingleStep(float(step))
        spin.setDecimals(2 if step < 0.1 else 1)
        if suffix:
            spin.setSuffix(suffix)
        spin.setFixedHeight(27)
        spin.setStyleSheet("background:#10131b; color:#edf2f7; border:1px solid #31384d; border-radius:5px;")
        spin.valueChanged.connect(self._on_design_property_change)
        return spin

    def _design_is_legacy_default(self, design_state):
        state = normalize_design_room_state(design_state)
        pages = state.get("pages", [])
        layers = pages[0].get("layers", []) if pages else []
        if len(layers) == 2:
            ids = {str(layer.get("id", "")) for layer in layers}
            texts = {str(layer.get("text", "")) for layer in layers}
            if ids == {"title-1", "body-1"} and {"Prayer Title", "Write your prayer words here."}.issubset(texts):
                return True
        return False

    def _blank_design_state_if_legacy_default(self, design_state):
        state = normalize_design_room_state(design_state)
        if self._design_is_legacy_default(state):
            return default_design_room_state()
        return state

    def ensure_design_default_is_blank(self):
        project_data = self._design_project_data()
        room_state = project_data.get("room_state", {}).get("design_room", {}) if isinstance(project_data, dict) else {}
        if room_state and self._design_is_legacy_default(room_state):
            self.selected_design_layer_id = ""
            self._commit_design_state(default_design_room_state(), sync_controls=False)

    def _design_project_data(self):
        parent = self.parent_window()
        project_data = getattr(parent, "project", None) if parent else None
        return project_data or self.project_data or {"project_type": "edit_room"}

    def _current_design_state(self):
        project_data = self._design_project_data()
        room_state = project_data.get("room_state", {}).get("design_room", {}) if isinstance(project_data, dict) else {}
        return self._blank_design_state_if_legacy_default(room_state)

    def _design_page(self, state=None):
        state = state or self._current_design_state()
        pages = state.setdefault("pages", [])
        if not pages:
            pages.append(default_design_room_state()["pages"][0])
        return pages[0]

    def _design_next_z(self, page):
        return max([int(float(layer.get("zIndex", 0) or 0)) for layer in page.get("layers", [])] + [-1]) + 1

    def _design_text_layer(self, page, **kwargs):
        layer = {
            "id": f"design-text-{int(time.time() * 1000)}",
            "type": "text",
            "name": "文字",
            "text": "Text",
            "x": 120,
            "y": 420,
            "width": 840,
            "height": 180,
            "fontSize": 54,
            "fontFamily": "Noto Sans SC",
            "fontWeight": "700",
            "fill": "#FFFFFF",
            "align": "center",
            "rotation": 0,
            "opacity": 1.0,
            "start": 0.0,
            "end": 0.0,
            "timelineTrack": 6,
            "zIndex": self._design_next_z(page),
            "shadow": True,
        }
        layer.update(kwargs)
        return layer

    def _design_rect_layer(self, page, **kwargs):
        layer = {
            "id": f"design-rect-{int(time.time() * 1000)}",
            "type": "rect",
            "name": "色块",
            "x": 150,
            "y": 780,
            "width": 780,
            "height": 180,
            "fill": "#111827",
            "opacity": 0.62,
            "cornerRadius": 30,
            "rotation": 0,
            "start": 0.0,
            "end": 0.0,
            "timelineTrack": 6,
            "zIndex": self._design_next_z(page),
        }
        layer.update(kwargs)
        return layer

    def _commit_design_state(self, design_state, status_text="", sync_controls=True, sync_timeline=True):
        design_state = normalize_design_room_state(design_state)
        parent = self.parent_window()
        project_data = self._design_project_data()
        try:
            project_data = update_room_state(project_data, "design_room", design_state)
        except Exception:
            project_data.setdefault("room_state", {})["design_room"] = copy.deepcopy(design_state)
        self.project_data = project_data
        if parent and hasattr(parent, "project"):
            parent.project = project_data
        if sync_controls:
            self.sync_design_panel_controls()
        elif hasattr(self, "design_layer_combo"):
            idx = self.design_layer_combo.findData(self.selected_design_layer_id)
            layer = self._selected_design_layer(design_state)
            if idx >= 0 and layer:
                prefix = "T" if layer.get("type") == "text" else "□" if layer.get("type") == "rect" else "图"
                self.design_layer_combo.setItemText(idx, f"{prefix} {layer.get('name', '图层')}")
        self.last_render_hash = None
        self.update_floating_subtitle()
        if sync_timeline and hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        self._update_workspace_status()
        if status_text and hasattr(self, "status_lbl"):
            self.status_lbl.setText(status_text)

    def add_design_component(self, kind):
        state = self._current_design_state()
        page = self._design_page(state)
        page["duration"] = max(float(page.get("duration", 5.0) or 5.0), float(self.state.get("duration", 5.0) or 5.0))
        layers = page.setdefault("layers", [])
        new_layers = []
        if kind == "title":
            new_layers.append(self._design_text_layer(page, name="标题", text="Title", x=96, y=230, width=888, height=130, fontSize=82, fontWeight="800"))
        elif kind == "body":
            new_layers.append(self._design_text_layer(page, name="正文", text="Body text", x=120, y=430, width=840, height=300, fontSize=48, fontWeight="600"))
        elif kind == "prayer":
            new_layers.append(self._design_text_layer(page, name="祷告词", text="Lord, guide my heart today.", x=120, y=820, width=840, height=320, fontSize=48, fontWeight="600", fill="#FFF7E8"))
        elif kind == "rect":
            new_layers.append(self._design_rect_layer(page, name="色块"))
        elif kind == "highlight":
            new_layers.append(self._design_rect_layer(page, name="强调条", x=155, y=720, width=770, height=92, fill="#4EA3FF", opacity=0.72, cornerRadius=26))
        elif kind == "divider":
            new_layers.append(self._design_rect_layer(page, name="分隔线", x=240, y=950, width=600, height=8, fill="#6EE7B7", opacity=0.9, cornerRadius=6))
        elif kind == "lower":
            base_z = self._design_next_z(page)
            new_layers.append(self._design_rect_layer(page, id=f"design-lower-bg-{int(time.time() * 1000)}", name="下栏底板", x=92, y=1400, width=896, height=160, fill="#111827", opacity=0.74, cornerRadius=28, zIndex=base_z))
            new_layers.append(self._design_text_layer(page, name="下栏文字", text="Lower third title", x=140, y=1438, width=800, height=80, fontSize=44, fontWeight="800", zIndex=base_z + 1))
        elif kind == "quote":
            base_z = self._design_next_z(page)
            new_layers.append(self._design_rect_layer(page, id=f"design-quote-bg-{int(time.time() * 1000)}", name="引用卡片", x=112, y=520, width=856, height=560, fill="#20232A", opacity=0.86, cornerRadius=34, zIndex=base_z))
            new_layers.append(self._design_text_layer(page, name="引用文字", text="Peace begins with a quiet heart.", x=165, y=680, width=750, height=260, fontSize=54, fontWeight="700", fill="#FFF7E8", zIndex=base_z + 1))
        if not new_layers:
            return
        layers.extend(new_layers)
        self.selected_design_layer_id = new_layers[-1]["id"]
        self.selected_track = "design"
        self._commit_design_state(state, "🎨 已添加设计组件。")
        self.push_history()

    def add_design_image_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片素材",
            "",
            "Image Files (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
        )
        if not file_path:
            return
        project_data = self._design_project_data()
        try:
            file_path, _, _ = copy_media_to_project_assets(project_data, file_path)
        except Exception:
            pass
        image_path, original_image_path, image_proxy_info = self._prepare_design_image_asset(file_path)
        proxy_used = bool(image_proxy_info and image_proxy_info.used_proxy and image_path != file_path)
        state = self._current_design_state()
        page = self._design_page(state)
        page["duration"] = max(float(page.get("duration", 5.0) or 5.0), float(self.state.get("duration", 5.0) or 5.0))
        layer = {
            "id": f"design-image-{int(time.time() * 1000)}",
            "type": "image",
            "name": os.path.splitext(os.path.basename(file_path))[0] or "图片素材",
            "src": QUrl.fromLocalFile(image_path).toString(),
            "path": image_path,
            "source_path": original_image_path,
            "original_path": original_image_path,
            "proxy_path": image_path if proxy_used else "",
            "proxy_max_side": DEFAULT_IMAGE_PROXY_MAX_SIDE if proxy_used else 0,
            "fit": "cover",
            "x": 140,
            "y": 520,
            "width": 800,
            "height": 520,
            "opacity": 1.0,
            "rotation": 0,
            "start": 0.0,
            "end": 0.0,
            "timelineTrack": 6,
            "zIndex": self._design_next_z(page),
        }
        page.setdefault("layers", []).append(layer)
        self.selected_design_layer_id = layer["id"]
        self.selected_track = "design"
        if proxy_used:
            self._commit_design_state(state, "🖼️ 已添加图片设计层（2K 预览代理）。")
            self.push_history()
            return
        self._commit_design_state(state, "🖼️ 已添加图片设计层。")
        self.push_history()

    def _prepare_design_image_asset(self, file_path):
        try:
            image_path, info = ensure_downscaled_image(file_path, max_side=DEFAULT_IMAGE_PROXY_MAX_SIDE)
            return image_path or file_path, file_path, info
        except Exception:
            return file_path, file_path, None

    def _selected_design_layer(self, state=None):
        state = state or self._current_design_state()
        page = self._design_page(state)
        layers = page.get("layers", [])
        layer = next((item for item in layers if item.get("id") == self.selected_design_layer_id), None)
        if layer is None and layers:
            layer = sorted(layers, key=lambda item: int(float(item.get("zIndex", 0) or 0)))[-1]
            self.selected_design_layer_id = layer.get("id", "")
        return layer

    def sync_design_panel_controls(self):
        if not hasattr(self, "design_layer_combo"):
            return
        if not getattr(self, "_design_ensuring_blank", False):
            project_data = self._design_project_data()
            room_state = project_data.get("room_state", {}).get("design_room", {}) if isinstance(project_data, dict) else {}
            if room_state and self._design_is_legacy_default(room_state):
                self._design_ensuring_blank = True
                try:
                    self.selected_design_layer_id = ""
                    self._commit_design_state(default_design_room_state(), sync_controls=False)
                finally:
                    self._design_ensuring_blank = False
        state = self._current_design_state()
        page = self._design_page(state)
        layers = sorted(page.get("layers", []), key=lambda item: int(float(item.get("zIndex", 0) or 0)), reverse=True)
        selected_id = self.selected_design_layer_id
        self.design_layer_combo.blockSignals(True)
        self.design_layer_combo.clear()
        if not layers:
            self.design_layer_combo.addItem("无设计图层", "")
        else:
            for layer in layers:
                prefix = "T" if layer.get("type") == "text" else "□" if layer.get("type") == "rect" else "图"
                self.design_layer_combo.addItem(f"{prefix} {layer.get('name', '图层')}", layer.get("id", ""))
            idx = max(0, self.design_layer_combo.findData(selected_id))
            self.design_layer_combo.setCurrentIndex(idx)
            self.selected_design_layer_id = self.design_layer_combo.currentData() or ""
        self.design_layer_combo.blockSignals(False)

        self.design_page_duration_spin.blockSignals(True)
        self.design_page_duration_spin.setValue(float(page.get("duration", 5.0) or 5.0))
        self.design_page_duration_spin.blockSignals(False)

        layer = self._selected_design_layer(state)
        widgets = [
            self.design_layer_name, self.design_text_input, self.design_x_spin, self.design_y_spin,
            self.design_w_spin, self.design_h_spin, self.design_start_spin, self.design_end_spin,
            self.design_size_spin, self.design_opacity_spin, self.design_align_combo,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            has_layer = layer is not None
            self.design_layer_name.setEnabled(has_layer)
            self.design_text_input.setEnabled(has_layer and layer.get("type") == "text")
            self.design_size_spin.setEnabled(has_layer and layer.get("type") == "text")
            self.design_align_combo.setEnabled(has_layer and layer.get("type") == "text")
            for widget in [self.design_x_spin, self.design_y_spin, self.design_w_spin, self.design_h_spin, self.design_start_spin, self.design_end_spin, self.design_opacity_spin, self.btn_design_color]:
                widget.setEnabled(has_layer)
            self.design_layer_name.setText(str(layer.get("name", "")) if has_layer else "")
            self.design_text_input.setPlainText(str(layer.get("text", "")) if has_layer and layer.get("type") == "text" else "")
            self.design_x_spin.setValue(int(float(layer.get("x", 0) or 0)) if has_layer else 0)
            self.design_y_spin.setValue(int(float(layer.get("y", 0) or 0)) if has_layer else 0)
            self.design_w_spin.setValue(int(float(layer.get("width", 1) or 1)) if has_layer else 1)
            self.design_h_spin.setValue(int(float(layer.get("height", 1) or 1)) if has_layer else 1)
            self.design_start_spin.setValue(float(layer.get("start", 0.0) or 0.0) if has_layer else 0.0)
            self.design_end_spin.setValue(float(layer.get("end", 0.0) or 0.0) if has_layer else 0.0)
            self.design_size_spin.setValue(int(float(layer.get("fontSize", 48) or 48)) if has_layer else 48)
            self.design_opacity_spin.setValue(float(layer.get("opacity", 1.0) or 0.0) if has_layer else 1.0)
            self.design_align_combo.setCurrentText(str(layer.get("align", "center") or "center") if has_layer else "center")
            color = str(layer.get("fill", "#FFFFFF") if has_layer else "#FFFFFF")
            self.btn_design_color.setStyleSheet(f"background-color:{color}; color:#ffffff; border:1px solid #454f6d; border-radius:6px; font-weight:800;")
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _on_design_layer_combo_changed(self, index):
        if not hasattr(self, "design_layer_combo"):
            return
        self.selected_design_layer_id = self.design_layer_combo.currentData() or ""
        if self.selected_design_layer_id:
            self.selected_track = "design"
        self.sync_design_panel_controls()

    def _on_design_page_duration_change(self, value):
        state = self._current_design_state()
        page = self._design_page(state)
        page["duration"] = max(0.1, float(value or 5.0))
        self._commit_design_state(state, sync_controls=False)

    def _on_design_property_change(self, *args):
        if not hasattr(self, "design_layer_combo"):
            return
        state = self._current_design_state()
        layer = self._selected_design_layer(state)
        if not layer:
            return
        layer["name"] = self.design_layer_name.text().strip() or layer.get("name", "图层")
        if layer.get("type") == "text":
            layer["text"] = self.design_text_input.toPlainText()
            layer["fontSize"] = int(self.design_size_spin.value())
            layer["align"] = self.design_align_combo.currentText() or "center"
        layer["x"] = int(self.design_x_spin.value())
        layer["y"] = int(self.design_y_spin.value())
        layer["width"] = max(1, int(self.design_w_spin.value()))
        layer["height"] = max(1, int(self.design_h_spin.value()))
        layer["start"] = max(0.0, float(self.design_start_spin.value()))
        layer["end"] = max(0.0, float(self.design_end_spin.value()))
        if layer["end"] > layer["start"]:
            page = self._design_page(state)
            page["duration"] = max(float(page.get("duration", 5.0) or 5.0), layer["end"])
        layer["opacity"] = max(0.0, min(1.0, float(self.design_opacity_spin.value())))
        self.selected_track = "design"
        self._commit_design_state(state, sync_controls=False)

    def pick_design_color(self):
        state = self._current_design_state()
        layer = self._selected_design_layer(state)
        if not layer:
            return
        color = QColorDialog.getColor(QColor(str(layer.get("fill", "#FFFFFF"))), self, "选择设计颜色")
        if not color.isValid():
            return
        layer["fill"] = color.name().upper()
        self._commit_design_state(state, "🎨 已更新设计颜色。")
        self.push_history()

    def move_design_layer(self, direction):
        state = self._current_design_state()
        page = self._design_page(state)
        layers = sorted(page.get("layers", []), key=lambda item: int(float(item.get("zIndex", 0) or 0)))
        idx = next((i for i, layer in enumerate(layers) if layer.get("id") == self.selected_design_layer_id), -1)
        next_idx = idx + int(direction)
        if idx < 0 or next_idx < 0 or next_idx >= len(layers):
            return
        layers[idx]["zIndex"], layers[next_idx]["zIndex"] = layers[next_idx].get("zIndex", next_idx), layers[idx].get("zIndex", idx)
        self._commit_design_state(state)
        self.push_history()

    def delete_selected_design_layer(self):
        state = self._current_design_state()
        page = self._design_page(state)
        before = len(page.get("layers", []))
        page["layers"] = [layer for layer in page.get("layers", []) if layer.get("id") != self.selected_design_layer_id]
        if len(page["layers"]) == before:
            return
        self.selected_design_layer_id = page["layers"][-1].get("id", "") if page["layers"] else ""
        self._commit_design_state(state, "🗑️ 已删除设计图层。")
        self.push_history()

    def clear_design_layers(self):
        state = self._current_design_state()
        page = self._design_page(state)
        page["layers"] = []
        self.selected_design_layer_id = ""
        self._commit_design_state(state, "已清空设计叠层。")
        self.push_history()

    def design_timeline_layers(self):
        state = self._current_design_state()
        page = self._design_page(state)
        page_duration = max(float(page.get("duration", 5.0) or 5.0), float(self.state.get("duration", 5.0) or 5.0))
        layers = []
        for layer in page.get("layers", []) or []:
            item = copy.deepcopy(layer)
            item["_page_duration"] = page_duration
            try:
                item["timelineTrack"] = max(6, min(7, int(float(item.get("timelineTrack", 6) or 6))))
            except Exception:
                item["timelineTrack"] = 6
            if float(item.get("end", 0.0) or 0.0) <= float(item.get("start", 0.0) or 0.0):
                item["end"] = page_duration
            layers.append(item)
        return layers

    def select_design_layer_by_index(self, idx):
        state = self._current_design_state()
        page = self._design_page(state)
        layers = page.get("layers", []) or []
        if not (0 <= idx < len(layers)):
            return
        self.selected_design_layer_id = layers[idx].get("id", "")
        self.selected_track = "design"
        self.sync_design_panel_controls()
        self.show_canvas_context_toolbar("design")
        self.timeline_widget.sync_from_controller()
        self._update_workspace_status()

    def update_design_layer_timing_by_index(self, idx, new_start, new_end, new_track):
        state = self._current_design_state()
        page = self._design_page(state)
        layers = page.get("layers", []) or []
        if not (0 <= idx < len(layers)):
            return
        layer = layers[idx]
        layer["start"] = max(0.0, float(new_start or 0.0))
        layer["end"] = max(layer["start"] + 0.05, float(new_end or 0.0))
        layer["timelineTrack"] = max(6, min(7, int(new_track or 6)))
        page["duration"] = max(float(page.get("duration", 5.0) or 5.0), layer["end"])
        self.selected_design_layer_id = layer.get("id", "")
        self.selected_track = "design"
        self._commit_design_state(state, sync_controls=False, sync_timeline=False)
        self.sync_design_panel_controls()

    def init_ui(self):
        self.setAcceptDrops(True)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        main_v_splitter = QSplitter(Qt.Orientation.Vertical)
        top_h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setStyleSheet("""
            QSplitter::handle {
                background-color: #313244;
                margin: 1px;
            }
            QSplitter::handle:vertical {
                height: 9px;
                background-color: #4b5274;
            }
            QSplitter::handle:horizontal {
                width: 6px;
                background-color: #2b3040;
            }
        """)
        main_v_splitter.setHandleWidth(9)
        top_h_splitter.setHandleWidth(6)

        # Ctrl+Z / Ctrl+Y 由主窗口按当前房间统一分发，避免焦点落在控件上时快捷键冲突。
        self.shortcut_play = QShortcut(QKeySequence("Space"), self)
        self.shortcut_play.activated.connect(self.toggle_play_from_shortcut)
        self.shortcut_step_back = QShortcut(QKeySequence("Left"), self)
        self.shortcut_step_back.activated.connect(lambda: self.seek_relative_from_shortcut(-0.1))
        self.shortcut_step_forward = QShortcut(QKeySequence("Right"), self)
        self.shortcut_step_forward.activated.connect(lambda: self.seek_relative_from_shortcut(0.1))
        self.shortcut_step_back_big = QShortcut(QKeySequence("Shift+Left"), self)
        self.shortcut_step_back_big.activated.connect(lambda: self.seek_relative_from_shortcut(-1.0))
        self.shortcut_step_forward_big = QShortcut(QKeySequence("Shift+Right"), self)
        self.shortcut_step_forward_big.activated.connect(lambda: self.seek_relative_from_shortcut(1.0))
        self.shortcut_preview_zoom_in = QShortcut(QKeySequence("Ctrl++"), self)
        self.shortcut_preview_zoom_in.activated.connect(lambda: self.adjust_preview_zoom_from_shortcut(1))
        self.shortcut_preview_zoom_in_alt = QShortcut(QKeySequence("Ctrl+="), self)
        self.shortcut_preview_zoom_in_alt.activated.connect(lambda: self.adjust_preview_zoom_from_shortcut(1))
        self.shortcut_preview_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        self.shortcut_preview_zoom_out.activated.connect(lambda: self.adjust_preview_zoom_from_shortcut(-1))
        self.shortcut_preview_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_preview_zoom_reset.activated.connect(self.reset_preview_view_from_shortcut)
        self.shortcut_focus_canvas = QShortcut(QKeySequence("F11"), self)
        self.shortcut_focus_canvas.activated.connect(self.toggle_canvas_focus_mode)
        self.preview_fullscreen_shortcut_text = "Ctrl+F"
        self._preview_fullscreen_active = False
        self.shortcut_preview_fullscreen = QShortcut(QKeySequence(), self)
        self.shortcut_preview_fullscreen.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_preview_fullscreen.activated.connect(self.toggle_preview_fullscreen_from_shortcut)
        self.shortcut_preview_fullscreen_escape = QShortcut(QKeySequence("Esc"), self)
        self.shortcut_preview_fullscreen_escape.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_preview_fullscreen_escape.setEnabled(False)
        self.shortcut_preview_fullscreen_escape.activated.connect(self.exit_preview_fullscreen)
        self.apply_preview_fullscreen_shortcut()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.shortcut_prev_project = QShortcut(QKeySequence("Alt+Left"), self)
        self.shortcut_prev_project.activated.connect(lambda: self.switch_sibling_project(-1))
        self.shortcut_next_project = QShortcut(QKeySequence("Alt+Right"), self)
        self.shortcut_next_project.activated.connect(lambda: self.switch_sibling_project(1))

        top_app_bar = QFrame()
        top_app_bar.setObjectName("editorTopBar")
        top_app_bar.setFixedHeight(56)
        top_app_bar.setStyleSheet("""
            QFrame#editorTopBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #08b9c8, stop:0.45 #2f8ee6, stop:1 #7c35f4);
                border-radius: 0px;
            }
            QFrame#editorTopBar QLabel { color: white; border: none; }
            QFrame#editorTopBar QPushButton {
                background: rgba(255,255,255,0.12);
                color: white;
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 6px;
                padding: 7px 12px;
                font-weight: 700;
            }
            QFrame#editorTopBar QPushButton:hover { background: rgba(255,255,255,0.20); }
            QFrame#editorTopBar QPushButton:checked {
                background: rgba(255,255,255,0.92);
                color: #111827;
                border-color: rgba(255,255,255,0.92);
            }
        """)
        top_app_layout = QHBoxLayout(top_app_bar)
        top_app_layout.setContentsMargins(12, 8, 8, 8)
        top_app_layout.setSpacing(8)
        self.btn_top_home = QPushButton("⌂")
        self.btn_top_home.setFixedSize(34, 34)
        self.btn_top_home.setToolTip("工程大厅")
        self.btn_top_home.clicked.connect(lambda: self.parent_window().switch_room(0) if self.parent_window() and hasattr(self.parent_window(), "switch_room") else None)
        self.btn_top_size = QPushButton("调整尺寸")
        self.btn_top_size.setToolTip("在右侧参数栏里调整全局比例")
        self.btn_top_size.clicked.connect(lambda: self.switch_inspector("video") if self.state.get("video_clips") else self.tabs.setCurrentIndex(1))
        self.btn_top_edit = QPushButton("编辑模式")
        self.btn_top_edit.setCheckable(True)
        self.btn_top_edit.setToolTip("解锁字幕编辑、素材拖放、剪刀和简单转场")
        self.btn_top_edit.clicked.connect(lambda checked: self.set_edit_mode(bool(checked)))
        self.btn_top_edit.hide()
        self.btn_top_undo = QPushButton("↶")
        self.btn_top_undo.setFixedSize(34, 34)
        self.btn_top_undo.setToolTip("撤销 Ctrl+Z")
        self.btn_top_undo.clicked.connect(self.undo)
        self.btn_top_redo = QPushButton("↷")
        self.btn_top_redo.setFixedSize(34, 34)
        self.btn_top_redo.setToolTip("重做 Ctrl+Y")
        self.btn_top_redo.clicked.connect(self.redo)
        self.btn_top_save = QPushButton("云保存")
        self.btn_top_save.setToolTip("保存当前工程")
        self.btn_top_save.clicked.connect(self.manual_save)
        self.btn_toggle_left = QPushButton("侧栏")
        self.btn_toggle_left.setCheckable(True)
        self.btn_toggle_left.setChecked(True)
        self.btn_toggle_left.setToolTip("显示/隐藏左侧工程栏")
        self.btn_toggle_left.clicked.connect(lambda checked: self.set_left_sidebar_visible(bool(checked)))
        self.btn_toggle_right = QPushButton("参数")
        self.btn_toggle_right.setCheckable(True)
        self.btn_toggle_right.setChecked(True)
        self.btn_toggle_right.setToolTip("显示/隐藏右侧参数栏")
        self.btn_toggle_right.clicked.connect(lambda checked: self.set_right_sidebar_visible(bool(checked)))
        self.btn_toggle_timeline = QPushButton("时间线")
        self.btn_toggle_timeline.setCheckable(True)
        self.btn_toggle_timeline.setChecked(False)
        self.btn_toggle_timeline.setToolTip("显示/隐藏时间线")
        self.btn_toggle_timeline.clicked.connect(lambda checked: self.set_timeline_visible(bool(checked)))
        self.btn_canvas_focus = QPushButton("大画布")
        self.btn_canvas_focus.setCheckable(True)
        self.btn_canvas_focus.setToolTip("隐藏侧栏并压缩时间线，最大化预览画布")
        self.btn_canvas_focus.clicked.connect(self.toggle_canvas_focus_mode)
        self.btn_caption_effects = QPushButton("字幕效果")
        self.btn_caption_effects.setToolTip("在右侧参数栏调节字幕样式")
        self.btn_caption_effects.clicked.connect(lambda: self.open_effects_dialog("caption"))
        self.btn_media_effects = QPushButton("视频声音")
        self.btn_media_effects.setToolTip("在右侧参数栏调节视频和声音")
        self.btn_media_effects.clicked.connect(lambda: self.open_effects_dialog("media"))
        self.btn_signature_effects = QPushButton("署名")
        self.btn_signature_effects.setToolTip("在右侧参数栏调节全局署名")
        self.btn_signature_effects.clicked.connect(lambda: self.open_effects_dialog("signature"))
        top_app_layout.addWidget(self.btn_top_home)
        top_app_layout.addWidget(self.btn_top_size)
        top_app_layout.addWidget(self.btn_top_edit)
        top_app_layout.addSpacing(6)
        top_app_layout.addWidget(self.btn_top_undo)
        top_app_layout.addWidget(self.btn_top_redo)
        top_app_layout.addWidget(self.btn_top_save)
        top_app_layout.addSpacing(8)
        top_app_layout.addWidget(self.btn_toggle_left)
        top_app_layout.addWidget(self.btn_toggle_right)
        top_app_layout.addWidget(self.btn_toggle_timeline)
        top_app_layout.addWidget(self.btn_canvas_focus)
        top_app_layout.addSpacing(8)
        top_app_layout.addWidget(self.btn_caption_effects)
        top_app_layout.addWidget(self.btn_media_effects)
        top_app_layout.addWidget(self.btn_signature_effects)
        top_app_layout.addStretch()
        self.lbl_top_project_name = QLabel(self._project_display_name())
        self.lbl_top_project_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_top_project_name.setStyleSheet("font-size: 14px; font-weight: 800;")
        top_app_layout.addWidget(self.lbl_top_project_name)
        self.lbl_top_project_tag = QLabel("")
        self.lbl_top_project_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_top_project_tag.setStyleSheet("background: rgba(249,226,175,0.20); color: #fff8d9; border: 1px solid rgba(249,226,175,0.36); border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: 900;")
        top_app_layout.addWidget(self.lbl_top_project_tag)
        self.refresh_project_header()
        top_app_layout.addStretch()
        self.lbl_top_user = QLabel("ZH")
        self.lbl_top_user.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_top_user.setFixedSize(34, 34)
        self.lbl_top_user.setStyleSheet("background: rgba(255,255,255,0.16); border: 1px solid rgba(255,255,255,0.24); border-radius: 17px; font-weight: 800;")
        self.btn_top_preview = QPushButton("预览")
        self.btn_top_preview.clicked.connect(self.toggle_play)
        self.btn_top_share = QPushButton("分享")
        self.btn_top_share.setStyleSheet("background: white; color: #27164a; border-radius: 6px; padding: 8px 14px; font-weight: 900;")
        self.btn_top_share.clicked.connect(self.manual_save)
        top_app_layout.addWidget(self.lbl_top_user)
        top_app_layout.addWidget(self.btn_top_preview)
        top_app_layout.addWidget(self.btn_top_share)
        self.top_app_bar = top_app_bar

        # ================= 1. 左侧面板 =================
        left_panel = QFrame(); left_panel.setStyleSheet("background-color: #181b24; border-radius: 0px;")
        left_panel.setMinimumWidth(200)
        left_root_layout = QVBoxLayout(left_panel)
        left_root_layout.setContentsMargins(14, 14, 14, 12)
        left_root_layout.setSpacing(8)

        project_header = QHBoxLayout()
        self.lbl_side_panel_title = QLabel("工程")
        self.lbl_side_panel_title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 900;")
        self.btn_collapse_left_panel = QPushButton("收起")
        self.btn_collapse_left_panel.setFixedSize(54, 28)
        self.btn_collapse_left_panel.setToolTip("只保留左侧小导航")
        self.btn_collapse_left_panel.setStyleSheet("background-color: #242b3f; color: #cdd6f4; border: 1px solid #3a425a; border-radius: 6px; font-weight: bold;")
        self.btn_collapse_left_panel.clicked.connect(lambda: self.set_left_sidebar_visible(False))
        project_header.addWidget(self.lbl_side_panel_title)
        project_header.addStretch()
        self.btn_collapse_left_panel.setVisible(False)
        left_root_layout.addLayout(project_header)

        self.left_page_stack = QStackedWidget()
        self.left_edit_page = QWidget()
        left_layout = QVBoxLayout(self.left_edit_page)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.left_media_page = QWidget()
        self.left_media_layout = QVBoxLayout(self.left_media_page)
        self.left_media_layout.setContentsMargins(0, 0, 0, 0)
        self.left_media_layout.setSpacing(8)
        self.left_design_page = QWidget()
        self.left_design_layout = QVBoxLayout(self.left_design_page)
        self.left_design_layout.setContentsMargins(0, 0, 0, 0)
        self.left_design_layout.setSpacing(8)
        self.left_page_stack.addWidget(self.left_edit_page)
        self.left_page_stack.addWidget(self.left_media_page)
        self.left_page_stack.addWidget(self.left_design_page)
        left_root_layout.addWidget(self.left_page_stack, stretch=1)

        self.side_search = QLineEdit()
        self.side_search.setPlaceholderText("搜索工程 / 字幕 / 素材")
        self.side_search.setMinimumHeight(38)
        self.side_search.setStyleSheet("background-color: #10131b; color: #cdd6f4; border: 1px solid #363b4d; border-radius: 8px; padding: 0 12px;")
        left_layout.addWidget(self.side_search)

        top_btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("🔄 清空"); self.btn_reset.setFixedHeight(35); self.btn_reset.setStyleSheet("background-color: #313244; border-radius: 5px; color: white;"); self.btn_reset.clicked.connect(self.reset_project)
        self.btn_undo = QPushButton("↩️ 撤销"); self.btn_undo.setFixedHeight(35); self.btn_undo.setStyleSheet("background-color: #313244; border-radius: 5px; color: white;"); self.btn_undo.clicked.connect(self.undo)
        self.btn_save = QPushButton("💾 保存"); self.btn_save.setFixedHeight(35); self.btn_save.setStyleSheet("background-color: #a6e3a1; font-weight: bold; border-radius: 5px; color: #11111b;"); self.btn_save.clicked.connect(self.manual_save)
        top_btn_row.addWidget(self.btn_reset); top_btn_row.addWidget(self.btn_undo); top_btn_row.addWidget(self.btn_save); left_layout.addLayout(top_btn_row)

        self.media_pool_panel = MediaPoolPanel(self)
        self.media_pool_panel.importRequested.connect(self.import_media_dialog)
        self.media_pool_panel.addRequested.connect(self.add_selected_media_pool_item_to_timeline)
        self.media_pool_panel.refreshRequested.connect(self.refresh_media_pool)
        self.media_pool_panel.selectionChangedPayload.connect(self.select_media_pool_payload)
        self.left_media_layout.addWidget(self.media_pool_panel)
        media_hint = QLabel("素材页独立管理导入素材；双击或点“入线”加入当前播放头。")
        media_hint.setWordWrap(True)
        media_hint.setStyleSheet("color:#8f9bb3; background:#10131b; border:1px solid #252c3d; border-radius:7px; padding:8px; font-size:11px;")
        self.left_media_layout.addWidget(media_hint)

        self.assembly_media_paths = []
        self.assembly_panel = QFrame()
        self.assembly_panel.setStyleSheet("""
            QFrame { background-color:#111620; border:1px solid #2d3548; border-radius:8px; }
            QLabel { color:#cdd6f4; border:none; }
            QListWidget { background-color:#0d111a; color:#cdd6f4; border:1px solid #252c3d; border-radius:6px; padding:3px; outline:none; }
            QListWidget::item { min-height:24px; padding:3px 6px; border-radius:4px; }
            QListWidget::item:selected { background-color:#3f6fb5; color:white; }
        """)
        assembly_layout = QVBoxLayout(self.assembly_panel)
        assembly_layout.setContentsMargins(9, 8, 9, 9)
        assembly_layout.setSpacing(6)
        assembly_header = QHBoxLayout()
        assembly_title_box = QVBoxLayout()
        assembly_title_box.setSpacing(0)
        assembly_title_box.addWidget(QLabel("ASSEMBLY CUT", styleSheet="color:#f9e2af; font-weight:900; font-size:12px; letter-spacing:0px;"))
        assembly_title_box.addWidget(QLabel("多素材组接", styleSheet="color:#ffffff; font-weight:900; font-size:13px;"))
        assembly_header.addLayout(assembly_title_box)
        assembly_header.addStretch()
        self.assembly_count_label = QLabel("0 段")
        self.assembly_count_label.setStyleSheet("color:#a6adc8; font-size:11px;")
        assembly_header.addWidget(self.assembly_count_label)
        assembly_layout.addLayout(assembly_header)
        self.assembly_list = QListWidget()
        self.assembly_list.setFixedHeight(116)
        self.assembly_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.assembly_list.customContextMenuRequested.connect(self.show_assembly_media_context_menu)
        assembly_layout.addWidget(self.assembly_list)
        assembly_actions = QHBoxLayout()
        self.btn_assembly_pick = QPushButton("选择素材")
        self.btn_assembly_pick.setFixedHeight(27)
        self.btn_assembly_pick.setStyleSheet("background-color:#89b4fa; color:#11111b; border-radius:5px; font-weight:900;")
        self.btn_assembly_pick.clicked.connect(self.pick_assembly_media_dialog)
        self.btn_assembly_clear = QPushButton("清空")
        self.btn_assembly_clear.setFixedHeight(27)
        self.btn_assembly_clear.setStyleSheet("background-color:#242b3f; color:#cdd6f4; border:1px solid #3a425a; border-radius:5px; font-weight:800;")
        self.btn_assembly_clear.clicked.connect(self.clear_assembly_media)
        self.btn_assembly_cut = QPushButton("一键组接")
        self.btn_assembly_cut.setFixedHeight(27)
        self.btn_assembly_cut.setStyleSheet("background-color:#f9e2af; color:#11111b; border-radius:5px; font-weight:900;")
        self.btn_assembly_cut.clicked.connect(self.assemble_selected_media_to_timeline)
        self.btn_assembly_random = QPushButton("随机铺满")
        self.btn_assembly_random.setFixedHeight(27)
        self.btn_assembly_random.setToolTip("随机打乱选中的素材，并按配音/字幕/工程时长自动铺满时间线。")
        self.btn_assembly_random.setStyleSheet("background-color:#a6e3a1; color:#11111b; border-radius:5px; font-weight:900;")
        self.btn_assembly_random.clicked.connect(self.assemble_selected_media_random_to_timeline)
        assembly_actions.addWidget(self.btn_assembly_pick)
        assembly_actions.addWidget(self.btn_assembly_clear)
        assembly_actions.addWidget(self.btn_assembly_cut)
        assembly_actions.addWidget(self.btn_assembly_random)
        assembly_layout.addLayout(assembly_actions)
        self.left_media_layout.addWidget(self.assembly_panel)
        self.refresh_assembly_media_list()
        self.left_media_layout.addStretch(1)

        left_layout.addWidget(QLabel("🎥 V1 画面轨道控制:", styleSheet="color: #89b4fa; font-weight: bold; margin-top: 5px;"))
        self.btn_v = QPushButton("➕ 导入第一段画面 (MP4)"); self.btn_v.setFixedHeight(35); self.btn_v.setStyleSheet("background-color: #313244; color: white;"); self.btn_v.clicked.connect(self.load_video)
        self.btn_v_autofill = QPushButton("🚀 一键弹拉对齐配音"); self.btn_v_autofill.setFixedHeight(35); self.btn_v_autofill.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold;"); self.btn_v_autofill.clicked.connect(self.auto_fill_video)
        self.btn_v_del = QPushButton("🗑️ 删除片段"); self.btn_v_del.setFixedHeight(35); self.btn_v_del.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold;"); self.btn_v_del.clicked.connect(self.remove_last_video_clip)
        vid_ctrl_layout = QHBoxLayout(); vid_ctrl_layout.addWidget(self.btn_v_autofill); vid_ctrl_layout.addWidget(self.btn_v_del)
        left_layout.addWidget(self.btn_v); left_layout.addLayout(vid_ctrl_layout)

        left_layout.addWidget(QLabel("🎵 A1 配音轨道:", styleSheet="color: #a6e3a1; font-weight: bold; margin-top: 5px;"))
        aud_ctrl_layout = QHBoxLayout()
        self.btn_a = QPushButton("🎵 导入独立配音 (可选)"); self.btn_a.setFixedHeight(35); self.btn_a.setStyleSheet("background-color: #313244; font-size: 13px; border-radius: 5px; color: white;"); self.btn_a.clicked.connect(self.load_audio)
        self.btn_a_del = QPushButton("🗑️ 删除"); self.btn_a_del.setFixedWidth(80); self.btn_a_del.setFixedHeight(35); self.btn_a_del.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 5px;"); self.btn_a_del.clicked.connect(self.remove_audio)
        aud_ctrl_layout.addWidget(self.btn_a); aud_ctrl_layout.addWidget(self.btn_a_del); left_layout.addLayout(aud_ctrl_layout)

        music_ctrl_layout = QHBoxLayout()
        self.btn_music = QPushButton("🎼 导入配乐 (可选)"); self.btn_music.setFixedHeight(35); self.btn_music.setStyleSheet("background-color: #313244; font-size: 13px; border-radius: 5px; color: white;"); self.btn_music.clicked.connect(self.load_music)
        self.btn_music_match = QPushButton("匹配时长"); self.btn_music_match.setFixedWidth(88); self.btn_music_match.setFixedHeight(35); self.btn_music_match.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; border-radius: 5px;"); self.btn_music_match.clicked.connect(self.match_music_to_audio)
        self.btn_music_del = QPushButton("删除"); self.btn_music_del.setFixedWidth(60); self.btn_music_del.setFixedHeight(35); self.btn_music_del.setStyleSheet("background-color: #45475a; color: #f38ba8; font-weight: bold; border-radius: 5px;"); self.btn_music_del.clicked.connect(self.remove_music)
        music_ctrl_layout.addWidget(self.btn_music); music_ctrl_layout.addWidget(self.btn_music_match); music_ctrl_layout.addWidget(self.btn_music_del); left_layout.addLayout(music_ctrl_layout)

        design_box = QFrame()
        self.design_box = design_box
        design_box.setObjectName("leftDesignPanel")
        design_box.setStyleSheet("""
            QFrame#leftDesignPanel {
                background-color: #121620;
                border: 1px solid #2d3548;
                border-radius: 8px;
            }
            QLabel { color: #cdd6f4; border: none; }
        """)
        design_layout = QVBoxLayout(design_box)
        design_layout.setContentsMargins(9, 8, 9, 9)
        design_layout.setSpacing(7)
        design_header = QHBoxLayout()
        design_header.addWidget(QLabel("🎨 设计组件", styleSheet="color:#f9e2af; font-weight:900;"))
        design_header.addStretch()
        self.btn_design_clear = QPushButton("清空")
        self.btn_design_clear.setFixedSize(48, 24)
        self.btn_design_clear.setToolTip("清空当前设计叠层")
        self.btn_design_clear.setStyleSheet("background-color:#2a3044; color:#f38ba8; border:1px solid #454f6d; border-radius:5px; font-size:11px; font-weight:800;")
        self.btn_design_clear.clicked.connect(self.clear_design_layers)
        design_header.addWidget(self.btn_design_clear)
        design_layout.addLayout(design_header)

        design_grid = QGridLayout()
        design_grid.setSpacing(5)
        design_items = [
            ("标题", "title"), ("正文", "body"), ("祷告", "prayer"), ("色块", "rect"),
            ("强调条", "highlight"), ("下栏", "lower"), ("引用卡", "quote"), ("分隔线", "divider"),
        ]
        for i, (label, kind) in enumerate(design_items):
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setToolTip(f"添加{label}设计层")
            btn.setStyleSheet("background-color:#202433; color:#edf2f7; border:1px solid #343d52; border-radius:6px; font-size:12px; font-weight:800;")
            btn.clicked.connect(lambda checked=False, k=kind: self.add_design_component(k))
            design_grid.addWidget(btn, i // 2, i % 2)
        design_layout.addLayout(design_grid)
        self.btn_design_image = QPushButton("导入图片组件")
        self.btn_design_image.setFixedHeight(30)
        self.btn_design_image.setStyleSheet("background-color:#313244; color:#89b4fa; border:1px solid #454f6d; border-radius:6px; font-weight:800;")
        self.btn_design_image.clicked.connect(self.add_design_image_dialog)
        design_layout.addWidget(self.btn_design_image)

        design_page_row = QHBoxLayout()
        design_page_row.addWidget(QLabel("页长"))
        self.design_page_duration_spin = QDoubleSpinBox()
        self.design_page_duration_spin.setRange(0.1, 3600.0)
        self.design_page_duration_spin.setSingleStep(0.5)
        self.design_page_duration_spin.setSuffix("s")
        self.design_page_duration_spin.setFixedHeight(28)
        self.design_page_duration_spin.setStyleSheet("background:#10131b; color:white; border:1px solid #31384d; border-radius:5px;")
        self.design_page_duration_spin.valueChanged.connect(self._on_design_page_duration_change)
        design_page_row.addWidget(self.design_page_duration_spin, stretch=1)
        design_layout.addLayout(design_page_row)

        self.design_layer_combo = QComboBox()
        self.design_layer_combo.setStyleSheet("background-color:#10131b; color:#cdd6f4; border:1px solid #31384d; border-radius:6px; padding:5px;")
        self.design_layer_combo.currentIndexChanged.connect(self._on_design_layer_combo_changed)
        design_layout.addWidget(self.design_layer_combo)

        self.design_layer_name = QLineEdit()
        self.design_layer_name.setPlaceholderText("图层名称")
        self.design_layer_name.setStyleSheet("background-color:#10131b; color:#cdd6f4; border:1px solid #31384d; border-radius:6px; padding:5px;")
        self.design_layer_name.textChanged.connect(self._on_design_property_change)
        design_layout.addWidget(self.design_layer_name)

        self.design_text_input = QTextEdit()
        self.design_text_input.setFixedHeight(54)
        self.design_text_input.setPlaceholderText("文字内容")
        self.design_text_input.setStyleSheet("background-color:#10131b; color:#cdd6f4; border:1px solid #31384d; border-radius:6px; padding:5px;")
        self.design_text_input.textChanged.connect(self._on_design_property_change)
        design_layout.addWidget(self.design_text_input)

        transform_grid = QGridLayout()
        transform_grid.setSpacing(5)
        self.design_x_spin = self._make_design_spin(0, 3000, 1)
        self.design_y_spin = self._make_design_spin(0, 4000, 1)
        self.design_w_spin = self._make_design_spin(1, 3000, 1)
        self.design_h_spin = self._make_design_spin(1, 4000, 1)
        for idx, (label, spin) in enumerate([("X", self.design_x_spin), ("Y", self.design_y_spin), ("W", self.design_w_spin), ("H", self.design_h_spin)]):
            transform_grid.addWidget(QLabel(label), idx // 2, (idx % 2) * 2)
            transform_grid.addWidget(spin, idx // 2, (idx % 2) * 2 + 1)
        design_layout.addLayout(transform_grid)

        time_grid = QGridLayout()
        time_grid.setSpacing(5)
        self.design_start_spin = self._make_design_double_spin(0.0, 3600.0, 0.1, "s")
        self.design_end_spin = self._make_design_double_spin(0.0, 3600.0, 0.1, "s")
        self.design_size_spin = self._make_design_spin(8, 300, 1)
        self.design_opacity_spin = self._make_design_double_spin(0.0, 1.0, 0.05, "")
        for idx, (label, spin) in enumerate([("入", self.design_start_spin), ("出", self.design_end_spin), ("字", self.design_size_spin), ("透", self.design_opacity_spin)]):
            time_grid.addWidget(QLabel(label), idx // 2, (idx % 2) * 2)
            time_grid.addWidget(spin, idx // 2, (idx % 2) * 2 + 1)
        design_layout.addLayout(time_grid)

        design_style_row = QHBoxLayout()
        self.design_align_combo = QComboBox()
        self.design_align_combo.addItems(["left", "center", "right"])
        self.design_align_combo.setStyleSheet("background:#10131b; color:#cdd6f4; border:1px solid #31384d; border-radius:5px; padding:4px;")
        self.design_align_combo.currentTextChanged.connect(self._on_design_property_change)
        self.btn_design_color = QPushButton("颜色")
        self.btn_design_color.setStyleSheet("background-color:#313244; color:#ffffff; border:1px solid #454f6d; border-radius:6px; font-weight:800;")
        self.btn_design_color.clicked.connect(self.pick_design_color)
        design_style_row.addWidget(self.design_align_combo, stretch=1)
        design_style_row.addWidget(self.btn_design_color)
        design_layout.addLayout(design_style_row)

        design_action_row = QHBoxLayout()
        for label, callback in [("上移", lambda: self.move_design_layer(1)), ("下移", lambda: self.move_design_layer(-1)), ("删除", self.delete_selected_design_layer)]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setStyleSheet("background-color:#242b3f; color:#cdd6f4; border:1px solid #3a425a; border-radius:6px; font-size:12px; font-weight:800;")
            btn.clicked.connect(callback)
            design_action_row.addWidget(btn)
        design_layout.addLayout(design_action_row)
        self.left_design_layout.addWidget(design_box)
        self.left_design_layout.addStretch(1)

        # 👑 改造剪贴板 UI：加入一键排版按钮
        text_header_layout = QHBoxLayout()
        text_header_layout.addWidget(QLabel("📝 剪贴板参考文案:", styleSheet="margin-top: 5px; font-weight: bold; color: #a6adc8;"))
        text_header_layout.addStretch()
        self.btn_clean_text = QPushButton("🧹 一键规范化清洗")
        self.btn_clean_text.setStyleSheet("background-color: #313244; color: #a6e3a1; font-weight: bold; border-radius: 4px; padding: 2px 8px; margin-top: 5px;")
        self.btn_clean_text.clicked.connect(self.format_custom_text_manually)
        text_header_layout.addWidget(self.btn_clean_text)
        left_layout.addLayout(text_header_layout)

        self.text_editor = QTextEdit()
        self.text_editor.setStyleSheet("background-color: #1e1e2e; border: 1px solid #313244; border-radius: 5px;")
        self.text_editor.textChanged.connect(self._on_custom_text_changed) # 绑定输入事件，实时保存
        left_layout.addWidget(self.text_editor, stretch=1)

        chunk_row = QHBoxLayout()
        chunk_row.addWidget(QLabel("✂️ 断句模式:", styleSheet="color: #89b4fa; font-weight: bold;"))
        self.chunk_mode = QComboBox()
        self.chunk_mode.addItems(["短句快速 (1-3字)", "智能重点短句 (3-4词为主)", "智能听译 (4-7词，适配双行按词)", REFERENCE_NARRATIVE_CHUNK_MODE, LEGACY_NARRATIVE_CHUNK_MODE, "自然短句 (1-4词)", "双词节奏 (2词/句)", "三词短句 (3词/句)", "四词短句 (4词/句)", "双行大段 (约10字，智能折行)", "单字轰炸 (1字/句)"])
        self.chunk_mode.setStyleSheet("background-color: #313244; color: white; padding: 5px; border-radius: 4px;")
        chunk_row.addWidget(self.chunk_mode, stretch=1)
        self.chunk_mode.currentTextChanged.connect(self._on_chunk_mode_change)
        left_layout.addLayout(chunk_row)

        timing_row = QHBoxLayout()
        timing_row.addWidget(QLabel("🎚️ 时间贴合:", styleSheet="color: #cba6f7; font-weight: bold;"))
        self.timing_mode = QComboBox()
        self.timing_mode.addItems(["L Cut (字幕提前进入)", "J Cut (字幕稍后收尾)", "对齐声音 (按停顿)"])
        self.timing_mode.setStyleSheet("background-color: #313244; color: white; padding: 5px; border-radius: 4px;")
        timing_row.addWidget(self.timing_mode, stretch=1)
        self.timing_mode.currentTextChanged.connect(self._on_timing_mode_change)
        left_layout.addLayout(timing_row)

        ai_provider_row = QHBoxLayout()
        ai_provider_row.addWidget(QLabel("AI \u542c\u8bd1\u670d\u52a1:", styleSheet="color: #a6e3a1; font-weight: bold;"))
        self.ai_transcription_provider_combo = QComboBox()
        self.ai_transcription_provider_combo.addItem("\u81ea\u52a8\uff08\u6309\u8bbe\u7f6e\u4f18\u5148\u7ea7\uff09", None)
        self.ai_transcription_provider_combo.addItem("Groq \u2192 Cloudflare", ["groq", "cloudflare"])
        self.ai_transcription_provider_combo.addItem("Cloudflare \u2192 Groq", ["cloudflare", "groq"])
        self.ai_transcription_provider_combo.addItem("\u4ec5 Groq", ["groq"])
        self.ai_transcription_provider_combo.addItem("\u4ec5 Cloudflare", ["cloudflare"])
        self.ai_transcription_provider_combo.setStyleSheet("background-color: #313244; color: white; padding: 5px; border-radius: 4px;")
        ai_provider_row.addWidget(self.ai_transcription_provider_combo, stretch=1)
        left_layout.addLayout(ai_provider_row)

        self.chk_fill_gaps = QCheckBox("填空时间：用前一句补空白")
        self.chk_fill_gaps.setChecked(True)
        self.chk_fill_gaps.setStyleSheet("color: #a6e3a1; font-weight: bold; margin-top: 2px;")
        self.chk_fill_gaps.stateChanged.connect(self._on_fill_gap_change)
        left_layout.addWidget(self.chk_fill_gaps)

        self.btn_layout_audit = QPushButton("🧭 检查重叠并整理排版")
        self.btn_layout_audit.setFixedHeight(36)
        self.btn_layout_audit.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 5px;")
        self.btn_layout_audit.clicked.connect(self.audit_and_reflow_subtitles)
        left_layout.addWidget(self.btn_layout_audit)

        self.btn_extract = QPushButton("🤖 AI 听译打轴"); self.btn_extract.setStyleSheet("background-color: #f59e0b; color: #11111b; font-weight: bold; padding: 10px; border-radius: 5px;"); self.btn_extract.clicked.connect(self.start_extract); left_layout.addWidget(self.btn_extract)
        self.status_lbl = QLabel("✅ 引擎就绪"); self.status_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;"); left_layout.addWidget(self.status_lbl)

        # ================= 2. 中间面板 =================
        center_panel = QFrame(); center_panel.setStyleSheet("background-color: #090b12; border: none; border-radius: 0px;"); center_layout = QVBoxLayout(center_panel)
        self.center_panel = center_panel
        center_layout.setContentsMargins(12, 8, 12, 8)
        center_layout.setSpacing(8)
        monitor_bar = QFrame()
        self.monitor_bar = monitor_bar
        monitor_bar.setStyleSheet("QFrame { background-color: #171a23; border: 1px solid #2b3040; border-radius: 8px; } QLabel { border: none; }")
        monitor_bar_layout = QHBoxLayout(monitor_bar)
        monitor_bar_layout.setContentsMargins(10, 6, 10, 6)
        monitor_bar_layout.setSpacing(10)
        self.lbl_monitor_title = QLabel("PROGRAM MONITOR")
        self.lbl_monitor_title.setStyleSheet("color: #f9e2af; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        self.lbl_workspace_stats = QLabel("未导入素材")
        self.lbl_workspace_stats.setStyleSheet("color: #cdd6f4; font-size: 12px; font-weight: 700;")
        self.lbl_monitor_meta = QLabel("1080x1920 · 50 px/s")
        self.lbl_monitor_meta.setStyleSheet("color: #89b4fa; font-family: Consolas; font-size: 11px;")
        monitor_bar_layout.addWidget(self.lbl_monitor_title)
        monitor_bar_layout.addSpacing(4)
        monitor_bar_layout.addWidget(self.lbl_workspace_stats, stretch=1)
        self.btn_edit_mode = QPushButton("编辑模式")
        self.btn_edit_mode.setCheckable(True)
        self.btn_edit_mode.setFixedHeight(28)
        self.btn_edit_mode.setToolTip("开启后可拖放素材、移动字幕、剪刀切分和添加简单转场")
        self.btn_edit_mode.setStyleSheet("""
            QPushButton {
                background-color: #242b3f;
                color: #cdd6f4;
                border: 1px solid #3a425a;
                border-radius: 7px;
                padding: 4px 10px;
                font-weight: 800;
            }
            QPushButton:checked {
                background-color: #89b4fa;
                color: #11111b;
                border-color: #89b4fa;
            }
        """)
        self.btn_edit_mode.clicked.connect(lambda checked: self.set_edit_mode(bool(checked)))
        self.btn_edit_mode.hide()
        monitor_bar_layout.addWidget(self.btn_edit_mode)
        monitor_bar_layout.addWidget(self.lbl_monitor_meta)
        center_layout.addWidget(monitor_bar)

        self.canvas_context_toolbar = QFrame(center_panel)
        self.canvas_context_toolbar.setVisible(False)
        self.canvas_context_toolbar.setFixedHeight(42)
        self.canvas_context_toolbar.setStyleSheet("""
            QFrame {
                background-color: #1a1d27;
                border: 1px solid #2f3548;
                border-radius: 9px;
            }
            QPushButton {
                background: transparent;
                color: #f4f6ff;
                border: none;
                border-radius: 6px;
                padding: 6px 9px;
                font-weight: 800;
            }
            QPushButton:hover { background: #2a3044; }
            QPushButton:checked { background: #89b4fa; color: #11111b; }
            QPushButton:disabled { color: #6f778c; }
            QLabel { color: #5f6b84; border: none; }
        """)
        canvas_tool_layout = QHBoxLayout(self.canvas_context_toolbar)
        canvas_tool_layout.setContentsMargins(8, 5, 8, 5)
        canvas_tool_layout.setSpacing(4)
        self.lbl_canvas_context = QLabel("画面")
        self.lbl_canvas_context.setStyleSheet("color: #a6adc8; font-size: 11px; font-weight: 900;")
        canvas_tool_layout.addWidget(self.lbl_canvas_context)

        self.ctx_buttons = {}

        def add_context_button(key, text, tip, callback):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(callback)
            btn.setVisible(False)
            canvas_tool_layout.addWidget(btn)
            self.ctx_buttons[key] = btn
            return btn

        self.btn_context_edit_mode = add_context_button("edit_mode", "编辑模式", "解锁当前画布与时间线剪辑工具", lambda: self.set_edit_mode(not self.edit_mode))
        self.btn_context_edit_mode.setCheckable(True)
        self.btn_context_edit_mode.hide()
        add_context_button("import_media", "导入", "拖放或选择视频/音频素材加入时间线", self.import_media_dialog)
        add_context_button("split", "剪刀", "在播放头位置切开当前片段", self.split_at_playhead)
        add_context_button("transition", "转场", "给当前片段添加简单淡化转场", self.apply_simple_transition)
        add_context_button("monitor", "画面", "切换大画面工作区", self.toggle_canvas_focus_mode)
        add_context_button("caption", "字幕选项", "在侧栏调整字幕文字、样式与动效", lambda: self.open_effects_dialog("caption"))
        add_context_button("signature", "署名", "打开全局署名设置", lambda: self.open_effects_dialog("signature"))
        self.ctx_separator_media = QLabel("|")
        self.ctx_separator_media.setVisible(False)
        canvas_tool_layout.addWidget(self.ctx_separator_media)
        add_context_button("video_duration", "时长", "在侧栏调整视频时长与画面参数", lambda: self.open_effects_dialog("media"))
        add_context_button("audio", "音频", "在侧栏调整配音与音量", lambda: self.open_effects_dialog("audio"))
        add_context_button("speed", "速度", "打开媒体速度/时长相关调节", lambda: self.open_effects_dialog("media"))
        add_context_button("position", "位置", "打开字幕位置与排版调节", lambda: self.open_effects_dialog("position"))
        add_context_button("motion", "动效", "打开字幕动效调节", lambda: self.open_effects_dialog("motion"))
        canvas_tool_layout.addStretch()
        add_context_button("flip", "翻转", "打开视频画面设置", lambda: self.open_effects_dialog("media"))
        add_context_button("delete", "删除", "删除当前选中的字幕或视频", self.delete_context_selection)
        stack_widget = QWidget(); stack_widget.setObjectName("previewStage"); stack_widget.setAcceptDrops(False); stack_widget.setStyleSheet("QWidget#previewStage { background-color: #000; border: 1px solid #4b5168; border-radius: 2px; }")
        grid = QGridLayout(stack_widget); grid.setContentsMargins(1, 1, 1, 1); grid.setSpacing(0)
        self.video_label = QLabel(); self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player = QMediaPlayer(); self.audio_output = QAudioOutput(); self.player.setAudioOutput(self.audio_output); self.video_sink = QVideoSink()
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.setVideoOutput(self.video_sink); self.video_sink.videoFrameChanged.connect(self.on_video_frame)
        self.player.mediaStatusChanged.connect(self._on_video_media_status_changed)
        self.audio_player = QMediaPlayer(); self.audio_track_output = QAudioOutput(); self.audio_player.setAudioOutput(self.audio_track_output)
        self.audio_player.mediaStatusChanged.connect(self._on_audio_media_status_changed)
        self.music_player = QMediaPlayer(); self.music_output = QAudioOutput(); self.music_player.setAudioOutput(self.music_output)
        self.music_player.mediaStatusChanged.connect(self._on_music_media_status_changed)

        self.browser = QWebEngineView(); self.browser.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self._force_preview_web_transparency()
        self.browser.setAcceptDrops(False)
        self.video_label.setAcceptDrops(False)

        self.bridge = WebBridge(self); self.channel = QWebChannel(); self.channel.registerObject("backend", self.bridge); self.browser.page().setWebChannel(self.channel)

        grid.addWidget(self.video_label, 0, 0); grid.addWidget(self.browser, 0, 0); self.browser.hide(); self.video_label.raise_()
        self.preview_workspace = PreviewWorkspace(stack_widget, self)
        self.aspect_container = self.preview_workspace
        center_layout.addWidget(self.preview_workspace, stretch=1)
        self.canvas_context_toolbar.raise_()

        controls_panel = QFrame()
        self.preview_controls_panel = controls_panel
        controls_panel.setStyleSheet("QFrame { background-color: #171a23; border: 1px solid #2b3040; border-radius: 8px; }")
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(5)

        transport_row = QHBoxLayout()
        transport_row.setSpacing(6)
        self.btn_step_back = QPushButton("‹0.1"); self.btn_step_back.setFixedSize(48, 28); self.btn_step_back.setToolTip("后退 0.1 秒"); self.btn_step_back.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_step_back.clicked.connect(lambda: self.seek_relative(-0.1)); transport_row.addWidget(self.btn_step_back)
        self.btn_play = QPushButton("▶ 播放"); self.btn_play.setFixedSize(74, 28); self.btn_play.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 5px;"); self.btn_play.clicked.connect(self.toggle_play); transport_row.addWidget(self.btn_play)
        self.btn_step_forward = QPushButton("0.1›"); self.btn_step_forward.setFixedSize(48, 28); self.btn_step_forward.setToolTip("前进 0.1 秒"); self.btn_step_forward.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_step_forward.clicked.connect(lambda: self.seek_relative(0.1)); transport_row.addWidget(self.btn_step_forward)
        self.lbl_time = QLabel("00:00.0 / 00:00.0"); self.lbl_time.setMinimumWidth(118); self.lbl_time.setStyleSheet("font-family: Consolas; color: #f9e2af; font-size: 12px; font-weight: bold;"); transport_row.addWidget(self.lbl_time)
        self.preview_seek_slider = NoScrollSlider(Qt.Orientation.Horizontal)
        self.preview_seek_slider.setRange(0, 10000)
        self.preview_seek_slider.setMinimumWidth(180)
        self.preview_seek_slider.setToolTip("迷你播放器：拖动调整播放位置")
        self.preview_seek_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 5px; background: #2b3040; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #89b4fa; border-radius: 2px; }
            QSlider::handle:horizontal { background: #ffffff; width: 12px; margin: -4px 0; border-radius: 6px; }
        """)
        self.preview_seek_slider.sliderMoved.connect(self._on_preview_seek_slider_moved)
        self.preview_seek_slider.sliderReleased.connect(self._on_preview_seek_slider_released)
        transport_row.addWidget(self.preview_seek_slider, stretch=1)
        self.btn_prev_project = QPushButton("上个视频")
        self.btn_prev_project.setFixedSize(72, 28)
        self.btn_prev_project.setToolTip("切换到当前工程文件夹里的上一个工程 / Alt+Left")
        self.btn_prev_project.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;")
        self.btn_prev_project.clicked.connect(lambda: self.switch_sibling_project(-1))
        self.btn_next_project = QPushButton("下个视频")
        self.btn_next_project.setFixedSize(72, 28)
        self.btn_next_project.setToolTip("切换到当前工程文件夹里的下一个工程 / Alt+Right")
        self.btn_next_project.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 5px;")
        self.btn_next_project.clicked.connect(lambda: self.switch_sibling_project(1))
        transport_row.addWidget(self.btn_prev_project)
        transport_row.addWidget(self.btn_next_project)
        self.btn_add_text = QPushButton("+ 文字"); self.btn_add_text.setFixedSize(66, 28); self.btn_add_text.setToolTip("在当前时间加文字"); self.btn_add_text.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 5px;"); self.btn_add_text.clicked.connect(self.add_manual_text)
        self.chk_safe_area = QCheckBox("安全框"); self.chk_safe_area.setChecked(False); self.chk_safe_area.setStyleSheet("color: #a6e3a1; font-weight: bold;"); self.chk_safe_area.stateChanged.connect(self.toggle_safe_area)
        self.btn_add_text.setVisible(False)
        self.chk_timeline_visible = QCheckBox("时间线")
        self.chk_timeline_visible.setChecked(False)
        self.chk_timeline_visible.setStyleSheet("color: #cdd6f4; font-weight: bold;")
        self.chk_timeline_visible.stateChanged.connect(lambda state: self.set_timeline_visible(state == Qt.CheckState.Checked.value))
        self.chk_loop_playback = QCheckBox("循环")
        self.chk_loop_playback.setChecked(True)
        self.chk_loop_playback.setToolTip("播放到工程结尾后自动回到开头")
        self.chk_loop_playback.setStyleSheet("color: #f9e2af; font-weight: bold;")
        transport_row.addWidget(self.btn_add_text); transport_row.addWidget(self.chk_safe_area); transport_row.addWidget(self.chk_timeline_visible); transport_row.addWidget(self.chk_loop_playback)
        controls_layout.addLayout(transport_row)

        view_row = QHBoxLayout()
        view_row.setSpacing(6)
        self.lbl_timeline_zoom_title = QLabel("TIMELINE")
        self.lbl_timeline_zoom_title.setStyleSheet("color: #6c7086; font-family: Consolas; font-size: 10px; font-weight: bold;")
        view_row.addWidget(self.lbl_timeline_zoom_title)
        self.btn_zoom_out = QPushButton("−"); self.btn_zoom_out.setFixedSize(28, 26); self.btn_zoom_out.setToolTip("缩小时间线"); self.btn_zoom_out.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_zoom_out.clicked.connect(lambda: self.adjust_timeline_zoom(0.8)); view_row.addWidget(self.btn_zoom_out)
        self.lbl_zoom = QLabel("50 px/s"); self.lbl_zoom.setFixedWidth(62); self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter); self.lbl_zoom.setStyleSheet("font-family: Consolas; color: #a6adc8; font-size: 11px;")
        view_row.addWidget(self.lbl_zoom)
        self.btn_zoom_in = QPushButton("+"); self.btn_zoom_in.setFixedSize(28, 26); self.btn_zoom_in.setToolTip("放大时间线"); self.btn_zoom_in.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_zoom_in.clicked.connect(lambda: self.adjust_timeline_zoom(1.25)); view_row.addWidget(self.btn_zoom_in)
        view_row.addSpacing(10)
        self.lbl_preview_zoom_title = QLabel("CANVAS")
        self.lbl_preview_zoom_title.setStyleSheet("color: #6c7086; font-family: Consolas; font-size: 10px; font-weight: bold;")
        self.lbl_preview_zoom_title.setToolTip("空白拖拽平移，滚轮缩放，Ctrl+0 重置")
        view_row.addWidget(self.lbl_preview_zoom_title)
        self.btn_preview_zoom_out = QPushButton("−"); self.btn_preview_zoom_out.setFixedSize(28, 26); self.btn_preview_zoom_out.setToolTip("缩小监看预览 Ctrl+-"); self.btn_preview_zoom_out.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_preview_zoom_out.clicked.connect(lambda: self.adjust_preview_zoom(-1)); view_row.addWidget(self.btn_preview_zoom_out)
        self.lbl_preview_zoom = QLabel("100%"); self.lbl_preview_zoom.setFixedWidth(48); self.lbl_preview_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter); self.lbl_preview_zoom.setStyleSheet("font-family: Consolas; color: #f9e2af; font-size: 11px;")
        view_row.addWidget(self.lbl_preview_zoom)
        self.btn_preview_zoom_in = QPushButton("+"); self.btn_preview_zoom_in.setFixedSize(28, 26); self.btn_preview_zoom_in.setToolTip("放大监看预览 Ctrl++"); self.btn_preview_zoom_in.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px;"); self.btn_preview_zoom_in.clicked.connect(lambda: self.adjust_preview_zoom(1)); view_row.addWidget(self.btn_preview_zoom_in)
        self.btn_preview_reset = QPushButton("100"); self.btn_preview_reset.setFixedSize(38, 26); self.btn_preview_reset.setToolTip("重置监看视窗 Ctrl+0"); self.btn_preview_reset.setStyleSheet("background-color: #313244; color: #a6e3a1; font-family: Consolas; font-weight: bold; border-radius: 5px;"); self.btn_preview_reset.clicked.connect(self.reset_preview_view); view_row.addWidget(self.btn_preview_reset)
        self.btn_preview_fullscreen = QPushButton("全屏"); self.btn_preview_fullscreen.setFixedSize(52, 26); self.btn_preview_fullscreen.setToolTip(f"全屏观看预览 {self.preview_fullscreen_shortcut_text} / Esc 退出"); self.btn_preview_fullscreen.setStyleSheet("background-color: #313244; color: #f9e2af; font-weight: bold; border-radius: 5px;"); self.btn_preview_fullscreen.clicked.connect(self.toggle_preview_fullscreen); view_row.addWidget(self.btn_preview_fullscreen)
        view_row.addSpacing(8)
        self.lbl_preview_proxy_resolution = QLabel("预览清晰度")
        self.lbl_preview_proxy_resolution.setStyleSheet("color: #89b4fa; font-size: 11px; font-weight: 900; border: none;")
        self.lbl_preview_proxy_resolution.setToolTip("只调精修观看预览，不影响导出画质。")
        view_row.addWidget(self.lbl_preview_proxy_resolution)
        self.preview_proxy_resolution_combo = QComboBox()
        self.preview_proxy_resolution_combo.addItems(PREVIEW_PROXY_RESOLUTION_OPTIONS)
        self.preview_proxy_resolution_combo.setCurrentText(self.preview_proxy_resolution)
        self.preview_proxy_resolution_combo.setFixedWidth(96)
        self.preview_proxy_resolution_combo.setToolTip("只影响精修预览代理清晰度；正式导出仍使用原始素材和工程画布。卡顿时选 360p，想看细节选 720p。")
        self.preview_proxy_resolution_combo.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px; padding: 4px 6px;")
        self.preview_proxy_resolution_combo.currentTextChanged.connect(self.on_preview_proxy_resolution_changed)
        view_row.addWidget(self.preview_proxy_resolution_combo)
        view_row.addStretch()
        controls_layout.addLayout(view_row)
        center_layout.addWidget(controls_panel)

        self.edit_status_strip = QFrame()
        self.edit_status_strip.setStyleSheet("QFrame { background-color: #11131f; border: 1px solid #25283a; border-radius: 8px; } QLabel { border: none; }")
        edit_status_layout = QHBoxLayout(self.edit_status_strip)
        edit_status_layout.setContentsMargins(10, 5, 10, 5)
        self.lbl_selected_status = QLabel("未选中片段")
        self.lbl_selected_status.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self.lbl_edit_health = QLabel("Ready")
        self.lbl_edit_health.setStyleSheet("color: #a6e3a1; font-family: Consolas; font-size: 11px;")
        edit_status_layout.addWidget(self.lbl_selected_status, stretch=1)
        edit_status_layout.addWidget(self.lbl_edit_health)
        center_layout.addWidget(self.edit_status_strip)

        # ================= 3. 右侧面板 =================
        right_panel = QFrame(); right_panel.setStyleSheet("""
            QFrame {
                background-color: #151821;
                border-left: 1px solid #282d3c;
                border-radius: 0px;
            }
            QLabel { color: #dce3f4; }
        """); right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_layout.setSpacing(8)
        right_header = QHBoxLayout()
        right_header.setSpacing(8)
        self.right_panel_title = QLabel("参数")
        self.right_panel_title.setStyleSheet("font-size: 15px; font-weight: 900; color: #ffffff;")
        self.btn_right_close = QPushButton("收起")
        self.btn_right_close.setFixedSize(58, 28)
        self.btn_right_close.setToolTip("收起右侧参数栏")
        self.btn_right_close.setStyleSheet("QPushButton { background-color: #252a3a; color: #dce3f4; border: 1px solid #3a425a; border-radius: 7px; font-size: 12px; font-weight: 800; } QPushButton:hover { background-color: #30384f; }")
        self.btn_right_close.clicked.connect(lambda: self.set_right_sidebar_visible(False))
        right_header.addWidget(self.right_panel_title)
        right_header.addStretch()
        self.btn_right_close.setVisible(False)
        right_layout.addLayout(right_header)
        self.tabs = QTabWidget(); self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: transparent; }
            QTabBar::tab {
                background: transparent;
                color: #a6adc8;
                border: none;
                padding: 7px 12px;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                background: #262b3b;
                color: #ffffff;
                border-radius: 7px;
            }
        """)
        self.tabs.currentChanged.connect(self._on_right_tab_changed)
        tab_subs = QWidget(); subs_layout = QVBoxLayout(tab_subs); subs_scroll = QScrollArea(); subs_scroll.setWidgetResizable(True); subs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); subs_scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        self.scroll_content = QWidget(); self.scroll_layout = QVBoxLayout(self.scroll_content); self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setSpacing(10); self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        subs_scroll.setWidget(self.scroll_content); subs_layout.addWidget(subs_scroll)
        self.insp_stack = QStackedWidget()

        def create_slider_spinbox(layout, label, min_v, max_v, default_v, callback, is_float=False):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 12px; color: #cdd6f4;")
            row.addWidget(lbl)
            slider = NoScrollSlider(Qt.Orientation.Horizontal)
            if is_float:
                slider.setRange(int(min_v*100), int(max_v*100)); spinbox = ProScrubDoubleSpinBox(); spinbox.setRange(min_v, max_v); spinbox.setSingleStep(0.05); spinbox.setLocale(self.eng_locale)
                spinbox.setValue(float(default_v)); slider.setValue(int(default_v*100))
                slider.valueChanged.connect(lambda v: spinbox.setValue(float(v)/100.0)); spinbox.valueChanged.connect(lambda v: slider.setValue(int(v*100)))
            else:
                slider.setRange(min_v, max_v); spinbox = ProScrubSpinBox(); spinbox.setRange(min_v, max_v)
                spinbox.setValue(int(default_v)); slider.setValue(int(default_v))
                slider.valueChanged.connect(spinbox.setValue); spinbox.valueChanged.connect(slider.setValue)
            spinbox.setStyleSheet("background: #111522; border: 1px solid #30384d; color: white; padding: 2px 5px; border-radius: 5px; font-size: 12px;"); spinbox.setFixedWidth(58); spinbox.valueChanged.connect(lambda v: callback())
            row.addWidget(slider); row.addWidget(spinbox); layout.addLayout(row)
            return slider, spinbox

        def create_section_frame(title, accent="#a6e3a1"):
            frame = QFrame()
            frame.setObjectName("inspectorSection")
            frame.setStyleSheet(
                f"QFrame#inspectorSection {{ background-color: #1a1d2b; border: 1px solid #2d3548; border-radius: 8px; }}"
                f"QLabel[role='section_title'] {{ color: {accent}; font-weight: 800; font-size: 12px; padding: 0; }}"
            )
            outer = QVBoxLayout(frame)
            outer.setContentsMargins(9, 8, 9, 8)
            outer.setSpacing(6)
            title_label = QLabel(title)
            title_label.setProperty("role", "section_title")
            outer.addWidget(title_label)
            return frame, outer

        page_empty = QWidget(); QVBoxLayout(page_empty).addWidget(QLabel("没有选中任何片段\n\n请在时间线上点击以查看属性\n\n提示: 右侧已改成侧边分类，不用一直往下滑", alignment=Qt.AlignmentFlag.AlignCenter, styleSheet="color: gray;"))
        insp_scroll = QScrollArea(); insp_scroll.setWidgetResizable(True); insp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); insp_scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }"); page_sub = QWidget(); sub_layout = QVBoxLayout(page_sub)
        sub_layout.setSpacing(10)

        def create_nav_btn(text, idx):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setMinimumSize(58, 28)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #202433;
                    color: #cdd6f4;
                    border: 1px solid #31384d;
                    border-radius: 7px;
                    padding: 4px 9px;
                    font-size: 12px;
                    font-weight: 800;
                }
                QPushButton:hover {
                    background-color: #2a3044;
                    color: #ffffff;
                }
                QPushButton:checked {
                    background-color: #2d3651;
                    color: #ffffff;
                    border-color: #89b4fa;
                }
            """)
            btn.clicked.connect(lambda: self._switch_sub_page(idx))
            return btn

        top_ctrl_row = QHBoxLayout()
        self.style_scope_combo = QComboBox(); self.style_scope_combo.addItems(["🔗 样式应用到: 全部轨道", "📏 仅应用到: 当前同轨道", "🎯 仅应用到: 独立片段"]); self.style_scope_combo.setStyleSheet("background-color: #313244; color: #a6e3a1; font-weight: bold; padding: 5px;")
        self.btn_del_clip = QPushButton("🗑️ 删除"); self.btn_del_clip.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 6px; padding: 6px 10px;"); self.btn_del_clip.clicked.connect(self.delete_current_clip)
        top_ctrl_row.addWidget(self.style_scope_combo, stretch=1); top_ctrl_row.addSpacing(8); top_ctrl_row.addWidget(self.btn_del_clip); sub_layout.addLayout(top_ctrl_row)

        preset_box = QVBoxLayout()
        preset_box.setSpacing(5)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        self.preset_combo = QComboBox(); self.preset_combo.setStyleSheet("background-color: #11111b; color: #cdd6f4; font-weight: bold; border: 1px solid #313244; border-radius: 6px; padding: 6px;")
        self.btn_apply_preset = QPushButton("应用"); self.btn_apply_preset.setFixedHeight(30); self.btn_apply_preset.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 5px 10px;")
        self.btn_save_preset = QPushButton("存预设"); self.btn_save_preset.setFixedHeight(30); self.btn_save_preset.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 6px; padding: 5px 10px;")
        self.btn_manage_preset = QPushButton("管理"); self.btn_manage_preset.setFixedHeight(30); self.btn_manage_preset.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 6px; padding: 5px 10px;")
        self.btn_del_preset = QPushButton("×"); self.btn_del_preset.setFixedSize(34, 30); self.btn_del_preset.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 6px;")
        preset_row.addWidget(QLabel("预设库:")); preset_row.addWidget(self.preset_combo, stretch=1)
        preset_actions = QHBoxLayout()
        preset_actions.setSpacing(6)
        preset_actions.addStretch()
        preset_actions.addWidget(self.btn_apply_preset)
        preset_actions.addWidget(self.btn_save_preset)
        preset_actions.addWidget(self.btn_manage_preset)
        preset_actions.addWidget(self.btn_del_preset)
        preset_box.addLayout(preset_row)
        preset_box.addLayout(preset_actions)
        sub_layout.addLayout(preset_box)
        self.btn_apply_preset.clicked.connect(self.apply_style_preset); self.btn_save_preset.clicked.connect(self.save_style_preset); self.btn_manage_preset.clicked.connect(self.manage_style_presets); self.btn_del_preset.clicked.connect(self.delete_style_preset)

        self.preset_preview_label = QLabel("Text")
        self.preset_preview_label.setMinimumHeight(72)
        self.preset_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preset_preview_label.setWordWrap(True)
        self.preset_preview_label.setStyleSheet("background-color:#11111b; border:1px dashed #45475a; border-radius:10px; color:#ffffff; padding:10px;")
        sub_layout.addWidget(self.preset_preview_label)
        self.preset_preview_label.setVisible(False)
        self.preset_preview_web = QWebEngineView()
        self.preset_preview_web.setMinimumHeight(120)
        self.preset_preview_web.setMaximumHeight(160)
        self.preset_preview_web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.preset_preview_web.setStyleSheet("background-color:#11111b; border:1px dashed #45475a; border-radius:10px;")
        sub_layout.addWidget(self.preset_preview_web)
        self.preset_combo.currentIndexChanged.connect(self._update_preset_preview)

        body_col = QVBoxLayout(); body_col.setSpacing(6); body_col.setContentsMargins(0, 0, 0, 0)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFixedHeight(36)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        nav_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal {
                background: transparent;
                height: 5px;
                margin: 0px 6px;
            }
            QScrollBar::handle:horizontal {
                background: #454f6d;
                border-radius: 2px;
                min-width: 28px;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)
        nav_widget = QWidget()
        nav_strip = QHBoxLayout(nav_widget)
        nav_strip.setContentsMargins(0, 0, 0, 3)
        nav_strip.setSpacing(5)
        self.sub_page_buttons = []
        for idx, text_btn in enumerate(["时间", "字体", "排版", "动画", "字体效果"]):
            btn = create_nav_btn(text_btn, idx)
            self.sub_page_buttons.append(btn)
            nav_strip.addWidget(btn)
        nav_strip.addStretch()
        nav_scroll.setWidget(nav_widget)
        body_col.addWidget(nav_scroll)
        self.sub_page_nav_scroll = nav_scroll

        self.sub_pages = QStackedWidget(); self.sub_pages.setStyleSheet("QStackedWidget { background: transparent; }")
        body_col.addWidget(self.sub_pages, stretch=1)
        sub_layout.addLayout(body_col)

        page_timing = QWidget(); page_timing_layout = QVBoxLayout(page_timing); page_timing_layout.setSpacing(6); page_timing_layout.setContentsMargins(0, 0, 0, 0)
        sec_duration, duration_layout = create_section_frame("⏱️ 长度控制 (Duration)", "#f9e2af")
        s_time_row = QHBoxLayout()
        s_time_row.addWidget(QLabel("起点 (s):")); self.sub_start_spin = ProScrubDoubleSpinBox(); self.sub_start_spin.setRange(0, 36000); self.sub_start_spin.setSingleStep(0.1); self.sub_start_spin.setLocale(self.eng_locale); self.sub_start_spin.setStyleSheet("background: #25262b; border: 1px solid #313244; color: white; padding: 2px 5px; border-radius: 3px;"); self.sub_start_spin.valueChanged.connect(self._on_sub_time_change); s_time_row.addWidget(self.sub_start_spin)
        s_time_row.addWidget(QLabel("终点 (s):")); self.sub_end_spin = ProScrubDoubleSpinBox(); self.sub_end_spin.setRange(0, 36000); self.sub_end_spin.setSingleStep(0.1); self.sub_end_spin.setLocale(self.eng_locale); self.sub_end_spin.setStyleSheet("background: #25262b; border: 1px solid #313244; color: white; padding: 2px 5px; border-radius: 3px;"); self.sub_end_spin.valueChanged.connect(self._on_sub_time_change); s_time_row.addWidget(self.sub_end_spin)
        duration_layout.addLayout(s_time_row)
        page_timing_layout.addWidget(sec_duration)
        sec_transform, transform_layout = create_section_frame("📍 变换与位置 (Transform)", "#89b4fa")
        self.pos_x_slider, self.pos_x_spin = create_slider_spinbox(transform_layout, "X 偏移 (%):", -100, 100, 0, self._on_style_change, is_float=True)
        self.pos_y_slider, self.pos_y_spin = create_slider_spinbox(transform_layout, "Y 偏移 (%):", -100, 100, 25, self._on_style_change, is_float=True)
        self.rot_slider, self.rot_spin = create_slider_spinbox(transform_layout, "旋转角度:", -180, 180, 0, self._on_style_change)
        page_timing_layout.addWidget(sec_transform)
        sec_mask, mask_layout = create_section_frame("🌫️ 蒙版与遮罩 (Masking)", "#81c8be")
        self.chk_mask_en = QCheckBox("🌟 启用上下羽化遮罩"); self.chk_mask_en.setChecked(False); self.chk_mask_en.stateChanged.connect(self._on_style_change); mask_layout.addWidget(self.chk_mask_en)
        self.mask_top_slider, self.mask_top_spin = create_slider_spinbox(mask_layout, "顶部羽化 %:", 0, 50, 20, self._on_style_change)
        self.mask_bot_slider, self.mask_bot_spin = create_slider_spinbox(mask_layout, "底部羽化 %:", 0, 50, 20, self._on_style_change)
        page_timing_layout.addWidget(sec_mask)
        page_timing_layout.addStretch(); self.sub_pages.addWidget(page_timing)

        page_typo = QWidget(); page_typo_layout = QVBoxLayout(page_typo); page_typo_layout.setSpacing(6); page_typo_layout.setContentsMargins(0, 0, 0, 0)
        sec_typo, typo_layout = create_section_frame("字体", "#a6e3a1")
        self.font_category_combo = QComboBox(); self.font_category_combo.addItems(["全部字体", "中文优先", "拉丁/英文字体", "等宽字体"]); self.font_category_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.font_category_combo.currentTextChanged.connect(self._set_font_filter); typo_layout.addWidget(self.font_category_combo)
        self.font_category_combo.clear()
        self.font_category_combo.addItems(["全部字体", "开源打包字体", "个人/不可商用", "系统/待复核", "中文优先", "拉丁/英文字体", "等宽字体", "无衬线", "衬线", "手写/花体", "装饰/标题"])
        font_safe_row = QHBoxLayout()
        self.safe_font_only = False
        self.lbl_font_policy = QLabel("开源字体随软件打包；其他字体可自由选择，但按个人/需授权字体提示，不随包分发。")
        self.lbl_font_policy.setWordWrap(True)
        self.lbl_font_policy.setStyleSheet("color: #f9e2af; font-size: 12px; font-weight: bold;")
        self.btn_apply_safe_font = QPushButton("替换为开源默认")
        self.btn_apply_safe_font.setStyleSheet("background-color: #313244; color: #f9e2af; font-weight: bold; border-radius: 6px; padding: 6px 10px;")
        self.btn_apply_safe_font.clicked.connect(self.apply_open_font_to_targets)
        font_safe_row.addWidget(self.lbl_font_policy, stretch=1)
        font_safe_row.addWidget(self.btn_apply_safe_font)
        typo_layout.addLayout(font_safe_row)
        self.font_var = NoScrollFontComboBox(); self.font_var.setStyleSheet("background-color: #313244; color: white; padding: 6px; border-radius: 5px;"); self.font_var.currentFontChanged.connect(self._on_font_change)
        typo_layout.addWidget(self.font_var)
        font_variant_row = QHBoxLayout()
        self.font_weight_combo = QComboBox()
        for label, value in [("Regular 400", "400"), ("Medium 500", "500"), ("SemiBold 600", "600"), ("Bold 700", "700"), ("ExtraBold 800", "800"), ("Black 900", "900")]:
            self.font_weight_combo.addItem(label, value)
        self.font_weight_combo.setCurrentIndex(3)
        self.font_weight_combo.setStyleSheet("background-color: #313244; color: white; padding: 5px; border-radius: 5px;")
        self.font_weight_combo.currentIndexChanged.connect(self._on_style_change)
        self.chk_font_italic = QCheckBox("Italic")
        self.chk_font_italic.setStyleSheet("color: #cdd6f4; font-weight: bold;")
        self.chk_font_italic.stateChanged.connect(self._on_style_change)
        font_variant_row.addWidget(self.font_weight_combo)
        font_variant_row.addWidget(self.chk_font_italic)
        font_variant_row.addStretch()
        typo_layout.addLayout(font_variant_row)
        self.lbl_font_license = QLabel("")
        self.lbl_font_license.setWordWrap(True)
        self.lbl_font_license.setStyleSheet("color: #a6adc8; font-size: 12px;")
        typo_layout.addWidget(self.lbl_font_license)
        self.font_preview_input = QLineEdit("Aa 字体")
        self.font_preview_input.setPlaceholderText("输入要预览的字，比如 Text")
        self.font_preview_input.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; border-radius: 6px; padding: 6px 8px;")
        self.font_preview_input.textChanged.connect(self._update_font_preview)
        typo_layout.addWidget(self.font_preview_input)
        self.font_preview_input.setVisible(True)
        self.font_preview_label = QLabel("Text")
        self.font_preview_label.setMinimumHeight(88)
        self.font_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.font_preview_label.setWordWrap(True)
        self.font_preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.font_preview_label.setStyleSheet("background-color: #11111b; border: 1px dashed #45475a; border-radius: 8px; color: #ffffff; padding: 12px;")
        typo_layout.addWidget(self.font_preview_label)
        self.font_preview_label.setVisible(True)
        page_typo_layout.addWidget(sec_typo); page_typo_layout.addStretch(); self.sub_pages.addWidget(page_typo)

        page_layout = QWidget(); page_layout_layout = QVBoxLayout(page_layout); page_layout_layout.setSpacing(6); page_layout_layout.setContentsMargins(0, 0, 0, 0)
        sec_layout, layout_layout = create_section_frame("字幕排版", "#89b4fa")
        layout_hint = QLabel("标准模式不启用特殊排版；智能模式只从下方勾选池里自动匹配；手动模式保留原有排版并开放参数。")
        layout_hint.setWordWrap(True)
        layout_hint.setStyleSheet("color:#a6adc8; font-size:12px; padding:2px 0 6px 0;")
        layout_layout.addWidget(layout_hint)

        def make_layout_group(title, expanded=False):
            box = CollapsibleBox(title, self, expanded=expanded)
            layout_layout.addWidget(box)
            return box.content_layout

        mode_layout = make_layout_group("1. 模式", expanded=True)
        self.layout_mode_combo = QComboBox()
        self.layout_mode_combo.addItems(["标准模式（不启用排版）", "智能模式（从勾选池自动匹配）", "手动模式：大小对比排版", "手动模式：多层叙事排版", "手动模式：首尾大小叙事", "手动模式：随机重点排版", "手动模式：左右错开排版", "手动模式：中轴对比排版", "手动模式：三层模板排版"])
        self.layout_mode_combo.setStyleSheet("background-color: #313244; padding: 5px; font-weight: bold;")
        self.layout_mode_combo.currentTextChanged.connect(self._on_style_change)
        mode_layout.addWidget(self.layout_mode_combo)
        self.transform_combo = QComboBox(); self.transform_combo.addItems(["首字母大写 (Capitalize)", "全部大写 (UPPERCASE)", "全部小写 (lowercase)", "正常 (Normal)"]); self.transform_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.transform_combo.currentTextChanged.connect(self._on_style_change); mode_layout.addWidget(self.transform_combo)
        self.align_combo = QComboBox(); self.align_combo.addItems(["居中对齐 (Center)", "居中左对齐 (Center Left)", "左对齐 (Left)", "自由混合对齐 (Free Mix)", "左对齐为主混合 (Left Mix)", "右对齐 (Right)", "两端对齐 (Justify)"]); self.align_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.align_combo.currentTextChanged.connect(self._on_style_change); mode_layout.addWidget(self.align_combo)
        self.box_layout_combo = QComboBox(); self.box_layout_combo.addItems(["自适应文字宽度", "固定窗口自动换行"]); self.box_layout_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.box_layout_combo.currentTextChanged.connect(self._on_style_change); mode_layout.addWidget(self.box_layout_combo)
        self.box_width_slider, self.box_width_spin = create_slider_spinbox(mode_layout, "显示区域宽% (0=自动):", 0, 92, 74, self._on_style_change, is_float=True)
        self.box_height_slider, self.box_height_spin = create_slider_spinbox(mode_layout, "显示区域高% (0=不限):", 0, 50, 0, self._on_style_change, is_float=True)
        self.max_lines_slider, self.max_lines_spin = create_slider_spinbox(mode_layout, "标准最大行数:", 1, 5, 2, self._on_style_change)
        self.lineh_slider, self.lineh_spin = create_slider_spinbox(mode_layout, "行距缩放:", 10, 300, 110, self._on_style_change)
        self.layout_row_gap_slider, self.layout_row_gap_spin = create_slider_spinbox(mode_layout, "排版行距/层间距 %:", 60, 220, 100, self._on_style_change)
        layout_preset_row = QHBoxLayout()
        self.layout_preset_combo = QComboBox()
        self.layout_preset_combo.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:6px;")
        self.btn_apply_layout_preset = QPushButton("\u5e94\u7528")
        self.btn_save_layout_preset = QPushButton("\u5b58\u6392\u7248")
        self.btn_delete_layout_preset = QPushButton("\u5220")
        for btn in (self.btn_apply_layout_preset, self.btn_save_layout_preset, self.btn_delete_layout_preset):
            btn.setStyleSheet("background-color:#313244; color:#cdd6f4; border-radius:6px; padding:6px 8px; font-weight:800;")
        self.btn_apply_layout_preset.clicked.connect(self.apply_layout_preset)
        self.btn_save_layout_preset.clicked.connect(self.save_layout_preset)
        self.btn_delete_layout_preset.clicked.connect(self.delete_layout_preset)
        layout_preset_row.addWidget(QLabel("\u6392\u7248\u9884\u8bbe:"))
        layout_preset_row.addWidget(self.layout_preset_combo, stretch=1)
        layout_preset_row.addWidget(self.btn_apply_layout_preset)
        layout_preset_row.addWidget(self.btn_save_layout_preset)
        layout_preset_row.addWidget(self.btn_delete_layout_preset)
        mode_layout.addLayout(layout_preset_row)

        smart_layout = make_layout_group("2. 智能排版池", expanded=True)
        smart_note = QLabel("智能模式会按词数从勾选项里挑；只勾一个就固定只用那个排版。")
        smart_note.setWordWrap(True); smart_note.setStyleSheet("color:#a6adc8; font-size:12px;")
        smart_layout.addWidget(smart_note)
        self.smart_layout_checks = {}
        for label, key, checked in [
            ("大小对比", "contrast", True), ("多层叙事", "narrative_block", True), ("首尾大小", "reel_stack", True),
            ("随机重点", "random_focus", True), ("左右错开", "side_steps", False), ("中轴对比", "axis_stack", True)
        ]:
            chk = QCheckBox(label); chk.setChecked(checked); chk.setStyleSheet("color:#cdd6f4; font-weight:700;"); chk.stateChanged.connect(self._on_style_change)
            self.smart_layout_checks[key] = chk; smart_layout.addWidget(chk)

        text_layout = make_layout_group("3. 词组大小对比", expanded=True)
        self.layout_variant_combo = QComboBox(); self.layout_variant_combo.addItems(["\u81ea\u52a8\u53d8\u5316", "\u5c0f-\u5927-\u5c0f", "\u5927-\u5c0f-\u6df7\u6392", "\u6df7\u6392-\u5927-\u5c0f", "\u9996\u5b57\u6bcd\u53d8\u5927\u53d9\u4e8b", "\u5f00\u5934\u53d8\u5927\u53d9\u4e8b", "\u5c3e\u90e8\u53d8\u5927\u53d9\u4e8b", "\u4e2d\u8f74\u7ed3\u5c3e\u5206\u4e24\u8fb9", "\u4e2d\u8f74 1-2-3 \u6392", "\u5de6\u4e0a\u5c0f-\u4e2d\u95f4\u5927-\u53f3\u4e0b\u5c0f", "\u4e2d\u8f74\u968f\u673a\u53d8\u5316"]); self.layout_variant_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.layout_variant_combo.currentTextChanged.connect(self._on_style_change); text_layout.addWidget(self.layout_variant_combo)
        self.layout_pattern_input = QLineEdit("auto")
        self.layout_pattern_input.setPlaceholderText("词组规律: auto / 小大大小 / 大大小 / S,L,S,L")
        self.layout_pattern_input.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:6px 8px;")
        self.layout_pattern_input.editingFinished.connect(self._on_style_change)
        text_layout.addWidget(self.layout_pattern_input)
        self.size_slider, self.size_spin = create_slider_spinbox(text_layout, "字幕大小:", 10, 300, 100, self._on_style_change)
        self.emphasis_slider, self.emphasis_spin = create_slider_spinbox(text_layout, "大字比例 %:", 100, 280, 145, self._on_style_change)
        self.contrast_small_slider, self.contrast_small_spin = create_slider_spinbox(text_layout, "小字比例 %:", 58, 100, 74, self._on_style_change)
        self.spacing_slider, self.spacing_spin = create_slider_spinbox(text_layout, "字距缩放:", -20, 100, 0, self._on_style_change)
        self.word_spacing_slider, self.word_spacing_spin = create_slider_spinbox(text_layout, "词距:", 0, 80, 0, self._on_style_change)

        layer_layout = make_layout_group("4. 层级对比", expanded=False)
        self.layout_layer_count_slider, self.layout_layer_count_spin = create_slider_spinbox(layer_layout, "层数 0=智能:", 0, 5, 0, self._on_style_change)
        self.layout_layer_pattern_input = QLineEdit("auto")
        self.layout_layer_pattern_input.setPlaceholderText("层级大小: auto / 小大中大 / S,L,M,L")
        self.layout_layer_pattern_input.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:6px 8px;")
        self.layout_layer_pattern_input.editingFinished.connect(self._on_style_change)
        layer_layout.addWidget(self.layout_layer_pattern_input)
        self.layout_layer_words_input = QLineEdit("auto")
        self.layout_layer_words_input.setPlaceholderText("每层字数: auto / 4,3,5,剩余")
        self.layout_layer_words_input.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:6px 8px;")
        self.layout_layer_words_input.editingFinished.connect(self._on_style_change)
        layer_layout.addWidget(self.layout_layer_words_input)

        axis_layout = make_layout_group("5. 中轴/错开排版", expanded=False)
        self.axis_mode_combo = QComboBox(); self.axis_mode_combo.addItems(["\u4e2d\u8f74\u6a21\u5f0f: \u5e38\u89c4\u9519\u5f00", "\u4e2d\u8f74\u6a21\u5f0f: \u4e2d\u95f4\u4e3a\u4e3b\u672b\u5c3e\u5206\u4e24\u8fb9", "\u4e2d\u8f74\u6a21\u5f0f: \u5de6\u4e0a\u5c0f-\u4e2d\u95f4\u5927-\u53f3\u4e0b\u5c0f", "\u4e2d\u8f74\u6a21\u5f0f: \u968f\u673a\u53d8\u5316"]); self.axis_mode_combo.setStyleSheet("background-color:#313244; padding:5px; font-weight:bold;"); self.axis_mode_combo.currentTextChanged.connect(self._on_style_change); axis_layout.addWidget(self.axis_mode_combo)
        self.axis_spread_slider, self.axis_spread_spin = create_slider_spinbox(axis_layout, "左右距离/收拢 %:", 0, 180, 100, self._on_style_change)
        self.axis_gap_slider, self.axis_gap_spin = create_slider_spinbox(axis_layout, "上下间距 %:", 50, 180, 100, self._on_style_change)

        self.btn_reference_two_line_layout = QPushButton("参考视频：四层累积叙事块")
        self.btn_reference_two_line_layout.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; padding: 8px; border-radius: 6px;")
        self.btn_reference_two_line_layout.clicked.connect(self.apply_reference_two_line_layout)
        layout_layout.addWidget(self.btn_reference_two_line_layout)
        self.btn_reflow_standard = QPushButton("✨ 按显示区域重排全部字幕")
        self.btn_reflow_standard.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; padding: 8px; border-radius: 6px;")
        self.btn_reflow_standard.clicked.connect(self.audit_and_reflow_subtitles)
        layout_layout.addWidget(self.btn_reflow_standard)
        page_layout_layout.addWidget(sec_layout); page_layout_layout.addStretch(); self.sub_pages.addWidget(page_layout)

        page_anim = QWidget(); page_anim_layout = QVBoxLayout(page_anim); page_anim_layout.setSpacing(6); page_anim_layout.setContentsMargins(0, 0, 0, 0)
        sec_anim, anim_layout = create_section_frame("🎬 动态特效 (Animation)", "#f9e2af")
        self.anim_combo = QComboBox(); self.anim_combo.addItems(["🎉 逐字弹跳 (Pop-in)", "☁️ 柔和淡入 (Fade)", "🌫️ 单词模糊渐入 (Blur Fade)", "▌单词遮罩右移键入", "➡️ 平滑遮罩右移", "⬆️ 电影级向上滚动 (Roll Up)", "💥 远处砸入 (Slam In)", "🔎 慢慢放大出字 (Grow In)", "🧲 词语散开入场 (Scatter In)", "🔤 字字分散入场 (Letter Scatter)", "🎥 朝镜头推进 (Camera Push)", "🧊 3D远近推进 (Depth Push)", "🕊️ 圣息慢显 (Holy Breath)", "🚫 无动画 (None)"]); self.anim_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.anim_combo.currentTextChanged.connect(self._on_style_change); anim_layout.addWidget(self.anim_combo)
        self.font_motion_combo = QComboBox(); self.font_motion_combo.addItems(["字体动画: 无效果", "字体动画: 打字机左移", "字体动画: 波浪感", "字体动画: 水波立体流动", "字体动画: 慢呼吸放大", "字体动画: 词语慢慢分散", "字体动画: 忽大忽小跳动"]); self.font_motion_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.font_motion_combo.currentTextChanged.connect(self._on_style_change); anim_layout.addWidget(self.font_motion_combo)
        self.pop_speed_slider, self.pop_speed_spin = create_slider_spinbox(anim_layout, "动画速度(秒):", 0.05, 2.0, 0.18, self._on_style_change, is_float=True)
        self.pop_bounce_slider, self.pop_bounce_spin = create_slider_spinbox(anim_layout, "弹跳弹性 %:", 100, 220, 128, self._on_style_change)
        self.inactive_alpha_slider, self.inactive_alpha_spin = create_slider_spinbox(anim_layout, "未读文字透明:", 0, 100, 100, self._on_style_change)
        page_anim_layout.addWidget(sec_anim); page_anim_layout.addStretch(); self.sub_pages.addWidget(page_anim)

        page_fx = QWidget(); page_fx_layout = QVBoxLayout(page_fx); page_fx_layout.setSpacing(6); page_fx_layout.setContentsMargins(0, 0, 0, 0)
        sec_fx, fx_layout = create_section_frame("字体效果", "#f5c2e7")
        fx_hint = QLabel("颜色、高亮、描边、阴影、整体发光和胶带底框都会随字体样式预设保存。")
        fx_hint.setWordWrap(True)
        fx_hint.setStyleSheet("color:#a6adc8; font-size:12px; padding:2px 0 6px 0;")
        fx_layout.addWidget(fx_hint)

        def make_fx_group(title, expanded=False):
            box = CollapsibleBox(title, self, expanded=expanded)
            fx_layout.addWidget(box)
            return box.content_layout

        def create_color_control(layout, label, button_attr, target, default_color):
            row = QHBoxLayout(); row.setSpacing(6)
            name = QLabel(label); name.setMinimumWidth(72); name.setStyleSheet("color:#cdd6f4; font-weight:700; border:none;")
            edit = QLineEdit(default_color.upper()); edit.setMaxLength(7); edit.setFixedWidth(92); edit.setPlaceholderText("#F12141")
            edit.setStyleSheet("background:#10131b; color:#edf2f7; border:1px solid #31384d; border-radius:5px; padding:5px 7px; font-weight:800;")
            edit.editingFinished.connect(lambda e=edit, t=target: self._apply_color_input(t, e))
            btn = QPushButton("🎨 点击选色"); btn.setMinimumWidth(104); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{label}: 左侧可输入 #F12141，右侧点击打开调色轮")
            btn.clicked.connect(lambda _, t=target: self._pick_color(t))
            row.addWidget(name); row.addWidget(edit); row.addWidget(btn, stretch=1); layout.addLayout(row)
            setattr(self, button_attr, btn); setattr(self, f"{button_attr}_input", edit)

        base_fx_layout = make_fx_group("基础颜色", expanded=True)
        create_color_control(base_fx_layout, "正文颜色", "btn_color_txt", "txt", "#FFFFFF")

        hl_fx_layout = make_fx_group("高亮", expanded=True)
        hl_top_row = QHBoxLayout()
        self.chk_use_hl = QCheckBox("启用高亮"); self.chk_use_hl.setChecked(True); self.chk_use_hl.stateChanged.connect(self._on_style_change); hl_top_row.addWidget(self.chk_use_hl)
        self.chk_hl_glow = QCheckBox("额外高亮光晕"); self.chk_hl_glow.setChecked(False); self.chk_hl_glow.stateChanged.connect(self._on_style_change); hl_top_row.addWidget(self.chk_hl_glow)
        hl_fx_layout.addLayout(hl_top_row)
        self.hl_style_combo = QComboBox()
        self.hl_style_combo.addItems(["高亮样式: 无高亮", "高亮样式: 纯字变色", "高亮样式: 高亮底盒", "高亮样式: 下划线", "高亮样式: 边框模式", "高亮样式: 纯字发光"])
        self.hl_style_combo.setStyleSheet("background-color: #313244; padding: 5px; font-weight: bold;")
        self.hl_style_combo.currentTextChanged.connect(self._on_highlight_style_change)
        hl_fx_layout.addWidget(self.hl_style_combo)
        create_color_control(hl_fx_layout, "高亮文字", "btn_color_hl", "hl", "#FFFFFF")
        create_color_control(hl_fx_layout, "底盒/光色", "btn_color_hl_bg", "hl_bg", "#FF0050")
        self.hl_motion_combo = QComboBox(); self.hl_motion_combo.addItems(["高亮动画: 稳定贴合", "高亮动画: 放大贴合", "高亮动画: 放大并挤开两边"])
        self.hl_motion_combo.setStyleSheet("background-color: #313244; padding: 5px; color: #a6e3a1;"); self.hl_motion_combo.currentTextChanged.connect(self._on_style_change); hl_fx_layout.addWidget(self.hl_motion_combo)
        self.hl_trail_words_slider, self.hl_trail_words_spin = create_slider_spinbox(hl_fx_layout, "拖尾词数(1=单词):", 1, 8, 1, self._on_style_change)
        self.hl_trail_alpha_slider, self.hl_trail_alpha_spin = create_slider_spinbox(hl_fx_layout, "尾巴最低透明 %:", 0, 100, 35, self._on_style_change)
        self.glow_size_slider, self.glow_size_spin = create_slider_spinbox(hl_fx_layout, "高亮发光强度:", 0, 100, 20, self._on_style_change)
        self.hl_alpha_slider, self.hl_alpha_spin = create_slider_spinbox(hl_fx_layout, "高亮底盒透明度 %:", 0, 100, 100, self._on_style_change)
        self.hl_radius_slider, self.hl_radius_spin = create_slider_spinbox(hl_fx_layout, "底盒/边框圆角:", 0, 100, 8, self._on_style_change)
        self.hl_padding_slider, self.hl_padding_spin = create_slider_spinbox(hl_fx_layout, "边框粗细/扩展:", 0, 100, 8, self._on_style_change)
        self.hl_pad_left_slider, self.hl_pad_left_spin = create_slider_spinbox(hl_fx_layout, "高亮左扩展:", 0, 80, 8, self._on_style_change)
        self.hl_pad_right_slider, self.hl_pad_right_spin = create_slider_spinbox(hl_fx_layout, "高亮右扩展:", 0, 80, 8, self._on_style_change)
        self.hl_pad_top_slider, self.hl_pad_top_spin = create_slider_spinbox(hl_fx_layout, "高亮上扩展:", 0, 40, 2, self._on_style_change)
        self.hl_pad_bottom_slider, self.hl_pad_bottom_spin = create_slider_spinbox(hl_fx_layout, "高亮下扩展:", 0, 40, 2, self._on_style_change)
        self.hl_skew_slider, self.hl_skew_spin = create_slider_spinbox(hl_fx_layout, "底盒倾斜/梯形:", -35, 35, 0, self._on_style_change)

        glow_fx_layout = make_fx_group("整体发光", expanded=False)
        self.chk_global_glow = QCheckBox("启用整体发光"); self.chk_global_glow.setChecked(False); self.chk_global_glow.stateChanged.connect(self._on_style_change); glow_fx_layout.addWidget(self.chk_global_glow)
        self.global_glow_mode_combo = QComboBox(); self.global_glow_mode_combo.addItems(["发光模式: 柔光", "发光模式: 霓虹强光", "发光模式: 扫光光线"])
        self.global_glow_mode_combo.setStyleSheet("background-color:#313244; padding:5px; font-weight:bold;"); self.global_glow_mode_combo.currentTextChanged.connect(self._on_style_change); glow_fx_layout.addWidget(self.global_glow_mode_combo)
        self.global_glow_motion_combo = QComboBox(); self.global_glow_motion_combo.addItems(["光线动画: 静态", "光线动画: 呼吸", "光线动画: 扫光"])
        self.global_glow_motion_combo.setStyleSheet("background-color:#313244; padding:5px;"); self.global_glow_motion_combo.currentTextChanged.connect(self._on_style_change); glow_fx_layout.addWidget(self.global_glow_motion_combo)
        create_color_control(glow_fx_layout, "发光颜色", "btn_color_glow", "glow", "#FFFFFF")
        self.global_glow_size_slider, self.global_glow_size_spin = create_slider_spinbox(glow_fx_layout, "发光强度:", 0, 100, 18, self._on_style_change)
        self.global_glow_alpha_slider, self.global_glow_alpha_spin = create_slider_spinbox(glow_fx_layout, "发光亮度 %:", 0, 100, 35, self._on_style_change)
        self.global_glow_blur_slider, self.global_glow_blur_spin = create_slider_spinbox(glow_fx_layout, "高斯模糊:", 0, 120, 24, self._on_style_change)
        self.global_glow_x_slider, self.global_glow_x_spin = create_slider_spinbox(glow_fx_layout, "光线 X 轴:", -80, 80, 0, self._on_style_change)
        self.global_glow_y_slider, self.global_glow_y_spin = create_slider_spinbox(glow_fx_layout, "光线 Y 轴:", -80, 80, 0, self._on_style_change)
        self.global_glow_z_slider, self.global_glow_z_spin = create_slider_spinbox(glow_fx_layout, "光线 Z 轴:", 0, 160, 0, self._on_style_change)

        texture_fx_layout = make_fx_group("字体质感", expanded=False)
        self.text_texture_combo = QComboBox(); self.text_texture_combo.addItems(["字体质感: 无", "字体质感: Gold 金色金属", "字体质感: Grain 轻微颗粒", "字体质感: Noise 噪点", "字体质感: Roughen 粗糙边", "字体质感: Distress texture 破碎磨损", "字体质感: 叠加 Grain+Noise+Roughen+Distress"])
        self.text_texture_combo.setStyleSheet("background-color: #313244; padding: 5px;"); self.text_texture_combo.currentTextChanged.connect(self._on_style_change); texture_fx_layout.addWidget(self.text_texture_combo)

        text3d_fx_layout = make_fx_group("字体3D立体", expanded=False)
        self.chk_text_3d = QCheckBox("启用字体3D立体"); self.chk_text_3d.setChecked(False); self.chk_text_3d.stateChanged.connect(self._on_style_change); text3d_fx_layout.addWidget(self.chk_text_3d)
        create_color_control(text3d_fx_layout, "立体背色", "btn_color_text3d", "text3d", "#6F3A05")
        self.text_3d_depth_slider, self.text_3d_depth_spin = create_slider_spinbox(text3d_fx_layout, "立体厚度:", 0, 100, 0, self._on_style_change)
        self.text_3d_x_slider, self.text_3d_x_spin = create_slider_spinbox(text3d_fx_layout, "立体 X 方向:", -20, 20, 2, self._on_style_change)
        self.text_3d_y_slider, self.text_3d_y_spin = create_slider_spinbox(text3d_fx_layout, "立体 Y 方向:", -20, 20, 3, self._on_style_change)

        stroke_fx_layout = make_fx_group("描边", expanded=False)
        create_color_control(stroke_fx_layout, "内描边色", "btn_color_stroke", "stroke", "#000000")
        create_color_control(stroke_fx_layout, "外描边色", "btn_color_stroke_o", "stroke_o", "#000000")
        self.stroke_slider, self.stroke_spin = create_slider_spinbox(stroke_fx_layout, "内描边粗细:", 0, 50, 4, self._on_style_change)
        self.stroke_o_slider, self.stroke_o_spin = create_slider_spinbox(stroke_fx_layout, "外描边粗细:", 0, 50, 0, self._on_style_change)
        self.stroke_soft_slider, self.stroke_soft_spin = create_slider_spinbox(stroke_fx_layout, "描边柔边 %:", 0, 100, 0, self._on_style_change)

        shadow_fx_layout = make_fx_group("阴影", expanded=False)
        create_color_control(shadow_fx_layout, "阴影颜色", "btn_color_sh", "sh", "#000000")
        self.sh_x_slider, self.sh_x_spin = create_slider_spinbox(shadow_fx_layout, "阴影 X 偏移:", -50, 50, 5, self._on_style_change)
        self.sh_y_slider, self.sh_y_spin = create_slider_spinbox(shadow_fx_layout, "阴影 Y 偏移:", -50, 50, 5, self._on_style_change)
        self.sh_blur_slider, self.sh_blur_spin = create_slider_spinbox(shadow_fx_layout, "阴影模糊:", 0, 50, 0, self._on_style_change)
        self.sh_a_slider, self.sh_a_spin = create_slider_spinbox(shadow_fx_layout, "阴影透明度 %:", 0, 100, 100, self._on_style_change)

        bg_fx_layout = make_fx_group("胶带底框", expanded=False)
        self.chk_bg_enabled = QCheckBox("启用胶带底框"); self.chk_bg_enabled.setChecked(False); self.chk_bg_enabled.stateChanged.connect(self._on_style_change); bg_fx_layout.addWidget(self.chk_bg_enabled)
        self.bg_mode_combo = QComboBox(); self.bg_mode_combo.addItems(["胶带底框: 贴合文字", "胶带底框: 逐字单点", "胶带底框: 扫光渐变", "胶带底框: 全局底框", "胶带底框: 全屏框架", "胶带底框: 柔光玻璃"])
        self.bg_mode_combo.setStyleSheet("background-color: #313244; padding: 5px; font-weight: bold;"); self.bg_mode_combo.currentTextChanged.connect(self._on_style_change); bg_fx_layout.addWidget(self.bg_mode_combo)
        create_color_control(bg_fx_layout, "胶带颜色", "btn_color_bg", "bg", "#000000")
        self.alpha_slider, self.alpha_spin = create_slider_spinbox(bg_fx_layout, "胶带透明度 %:", 0, 100, 80, self._on_style_change)
        self.radius_slider, self.radius_spin = create_slider_spinbox(bg_fx_layout, "胶带圆角:", 0, 100, 15, self._on_style_change)
        self.chk_bg_auto_resolution = QCheckBox("自动按画布分辨率缩放底框"); self.chk_bg_auto_resolution.setChecked(True); self.chk_bg_auto_resolution.setStyleSheet("color:#a6e3a1; font-weight:700; padding:4px 0;"); self.chk_bg_auto_resolution.stateChanged.connect(self._on_style_change); bg_fx_layout.addWidget(self.chk_bg_auto_resolution)
        self.padding_slider, self.padding_spin = create_slider_spinbox(bg_fx_layout, "整体扩展:", 0, 100, 20, self._on_style_change)
        self.bg_pad_left_slider, self.bg_pad_left_spin = create_slider_spinbox(bg_fx_layout, "左扩展:", 0, 120, 20, self._on_style_change)
        self.bg_pad_right_slider, self.bg_pad_right_spin = create_slider_spinbox(bg_fx_layout, "右扩展:", 0, 120, 20, self._on_style_change)
        self.bg_pad_top_slider, self.bg_pad_top_spin = create_slider_spinbox(bg_fx_layout, "上扩展:", 0, 80, 8, self._on_style_change)
        self.bg_pad_bottom_slider, self.bg_pad_bottom_spin = create_slider_spinbox(bg_fx_layout, "下扩展:", 0, 80, 8, self._on_style_change)
        self.chk_merge_bridge = QCheckBox("启用底框桥接黑层"); self.chk_merge_bridge.setChecked(False); self.chk_merge_bridge.stateChanged.connect(self._on_style_change); bg_fx_layout.addWidget(self.chk_merge_bridge)
        self.merge_bridge_width_slider, self.merge_bridge_width_spin = create_slider_spinbox(bg_fx_layout, "桥接层宽度:", 20, 400, 160, self._on_style_change)
        self.merge_bridge_height_slider, self.merge_bridge_height_spin = create_slider_spinbox(bg_fx_layout, "桥接层厚度:", 4, 80, 16, self._on_style_change)
        self.merge_bridge_alpha_slider, self.merge_bridge_alpha_spin = create_slider_spinbox(bg_fx_layout, "桥接层透明度 %:", 0, 100, 100, self._on_style_change)

        page_fx_layout.addWidget(sec_fx); page_fx_layout.addStretch(); self.sub_pages.addWidget(page_fx)

        sub_layout.addStretch(); insp_scroll.setWidget(page_sub)

        page_vid = QWidget(); vid_layout = QVBoxLayout(page_vid)
        vid_layout.addWidget(QLabel("⏱️ 复合片段长度控制:", styleSheet="color: #89b4fa; font-weight: bold; margin-top: 10px;"))
        v_time_row = QHBoxLayout()
        v_time_row.addWidget(QLabel("起点 (s):")); self.v_start_spin = ProScrubDoubleSpinBox(); self.v_start_spin.setRange(0, 36000); self.v_start_spin.setLocale(self.eng_locale); self.v_start_spin.setStyleSheet("background: #25262b; border: 1px solid #313244; color: white; padding: 2px 5px; border-radius: 3px;"); self.v_start_spin.valueChanged.connect(self._on_v_time_change); v_time_row.addWidget(self.v_start_spin)
        v_time_row.addWidget(QLabel("终点 (s):")); self.v_end_spin = ProScrubDoubleSpinBox(); self.v_end_spin.setRange(0, 36000); self.v_end_spin.setLocale(self.eng_locale); self.v_end_spin.setStyleSheet("background: #25262b; border: 1px solid #313244; color: white; padding: 2px 5px; border-radius: 3px;"); self.v_end_spin.valueChanged.connect(self._on_v_time_change); v_time_row.addWidget(self.v_end_spin)
        vid_layout.addLayout(v_time_row)

        self.res_combo = QComboBox(); self.res_combo.addItems(OUTPUT_RESOLUTION_OPTIONS); self.res_combo.setCurrentText(get_output_resolution()); self.res_combo.setStyleSheet("background-color: #313244; padding: 5px; color: #f9e2af; margin-top: 10px;"); self.res_combo.currentTextChanged.connect(self.on_resolution_changed); vid_layout.addWidget(QLabel("📐 画布分辨率:")); vid_layout.addWidget(self.res_combo)
        vid_layout.addWidget(QLabel("🎞️ 画面设置", alignment=Qt.AlignmentFlag.AlignCenter)); self.v_scale_slider, self.v_scale_spin = create_slider_spinbox(vid_layout, "画面缩放 %:", 10, 300, 100, self._on_vid_prop_change); self.v_vol_slider, self.v_vol_spin = create_slider_spinbox(vid_layout, "原声音量 %:", 0, 100, 100, self._on_vid_prop_change)
        vid_layout.addStretch()

        page_aud = QWidget(); aud_layout = QVBoxLayout(page_aud); aud_layout.addWidget(QLabel("🎵 音频音量设置", alignment=Qt.AlignmentFlag.AlignCenter)); self.a_vol_slider, self.a_vol_spin = create_slider_spinbox(aud_layout, "配音音量 %:", 0, 100, 100, self._on_aud_prop_change); self.music_vol_slider, self.music_vol_spin = create_slider_spinbox(aud_layout, "配乐音量 %:", 0, 100, 35, self._on_music_prop_change); aud_layout.addStretch()

        page_signature = QWidget(); signature_layout = QVBoxLayout(page_signature); signature_layout.setSpacing(10)
        sec_signature, sig_layout = create_section_frame("✒️ 全局署名 (Signature)", "#f9e2af")
        sig_template_row = QHBoxLayout()
        self.signature_template_combo = QComboBox()
        self.signature_template_combo.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; border-radius: 6px; padding: 6px;")
        self.btn_signature_apply_template = QPushButton("应用模板")
        self.btn_signature_apply_template.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_apply_template.clicked.connect(self.apply_signature_preset)
        self.btn_signature_save_template = QPushButton("存模板")
        self.btn_signature_save_template.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_save_template.clicked.connect(self.save_signature_preset)
        self.btn_signature_delete_template = QPushButton("删")
        self.btn_signature_delete_template.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_delete_template.clicked.connect(self.delete_signature_preset)
        sig_template_row.addWidget(self.signature_template_combo, stretch=1)
        sig_template_row.addWidget(self.btn_signature_apply_template)
        sig_template_row.addWidget(self.btn_signature_save_template)
        sig_template_row.addWidget(self.btn_signature_delete_template)
        sig_layout.addLayout(sig_template_row)
        self.chk_signature_enabled = QCheckBox("启用署名")
        self.chk_signature_enabled.setStyleSheet("color: #f9e2af; font-weight: bold;")
        self.chk_signature_enabled.stateChanged.connect(self._on_signature_change)
        sig_layout.addWidget(self.chk_signature_enabled)
        self.signature_text_input = QLineEdit()
        self.signature_text_input.setPlaceholderText("输入署名，例如 @Name / Studio")
        self.signature_text_input.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; border-radius: 6px; padding: 7px 9px;")
        self.signature_text_input.textChanged.connect(self._on_signature_change)
        sig_layout.addWidget(self.signature_text_input)
        self.signature_position_combo = QComboBox()
        self.signature_position_combo.addItems(["右上角", "左上角", "右下角", "左下角", "顶部居中", "底部居中", "自定义位置"])
        self.signature_position_combo.setStyleSheet("background-color: #313244; padding: 6px; color: white; border-radius: 5px;")
        self.signature_position_combo.currentTextChanged.connect(self._on_signature_change)
        sig_layout.addWidget(self.signature_position_combo)
        self.signature_size_slider, self.signature_size_spin = create_slider_spinbox(sig_layout, "署名大小:", 12, 300, 42, self._on_signature_change)
        self.signature_margin_x_slider, self.signature_margin_x_spin = create_slider_spinbox(sig_layout, "左右边距 %:", 0, 30, 5, self._on_signature_change, is_float=True)
        self.signature_margin_y_slider, self.signature_margin_y_spin = create_slider_spinbox(sig_layout, "上下边距 %:", 0, 30, 4, self._on_signature_change, is_float=True)
        self.signature_bg_combo = QComboBox()
        self.signature_bg_combo.addItems(["柔光玻璃背景", "纯色底框", "无背景"])
        self.signature_bg_combo.setStyleSheet("background-color: #313244; padding: 6px; color: white; border-radius: 5px;")
        self.signature_bg_combo.currentTextChanged.connect(self._on_signature_change)
        sig_layout.addWidget(self.signature_bg_combo)
        sig_color_row = QHBoxLayout()
        self.btn_signature_text_color = QPushButton("文字色")
        self.btn_signature_text_color.setStyleSheet("background-color: #313244; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_text_color.clicked.connect(lambda: self._pick_signature_color("text"))
        self.btn_signature_bg_color = QPushButton("背景色")
        self.btn_signature_bg_color.setStyleSheet("background-color: #313244; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_bg_color.clicked.connect(lambda: self._pick_signature_color("bg"))
        sig_color_row.addWidget(self.btn_signature_text_color)
        sig_color_row.addWidget(self.btn_signature_bg_color)
        sig_layout.addLayout(sig_color_row)
        self.signature_bg_alpha_slider, self.signature_bg_alpha_spin = create_slider_spinbox(sig_layout, "背景透明度 %:", 0, 100, 45, self._on_signature_change)
        self.signature_bg_radius_slider, self.signature_bg_radius_spin = create_slider_spinbox(sig_layout, "背景圆角:", 0, 80, 26, self._on_signature_change)
        self.signature_bg_padding_slider, self.signature_bg_padding_spin = create_slider_spinbox(sig_layout, "背景内边距:", 0, 80, 10, self._on_signature_change)
        sig_btn_row = QHBoxLayout()
        self.btn_signature_from_current = QPushButton("用当前字幕做署名模板")
        self.btn_signature_from_current.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_from_current.clicked.connect(self.capture_signature_template)
        self.btn_signature_default = QPushButton("恢复右上角默认")
        self.btn_signature_default.setStyleSheet("background-color: #313244; color: #f9e2af; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_default.clicked.connect(self.reset_signature_template)
        sig_btn_row.addWidget(self.btn_signature_from_current)
        sig_btn_row.addWidget(self.btn_signature_default)
        sig_layout.addLayout(sig_btn_row)
        signature_layout.addWidget(sec_signature)
        signature_layout.addStretch()

        self.insp_stack.addWidget(page_empty); self.insp_stack.addWidget(insp_scroll); self.insp_stack.addWidget(page_vid); self.insp_stack.addWidget(page_aud)
        self.signature_panel = page_signature
        self.tabs.addTab(tab_subs, "精修"); self.tabs.addTab(self.insp_stack, "画面"); self.tabs.addTab(page_signature, "署名"); right_layout.addWidget(self.tabs)

        timeline_outer = QFrame(); timeline_outer.setStyleSheet("background-color: #171a23; border: none; border-top: 1px solid #2b3040; border-radius: 0px;"); timeline_outer.setMinimumHeight(HEADER_H + TRACK_H * TRACK_COUNT + 38); tl_outer_layout = QVBoxLayout(timeline_outer); tl_outer_layout.setContentsMargins(0,0,0,0); tl_outer_layout.setSpacing(0)
        timeline_bar = QFrame(); timeline_bar.setStyleSheet("QFrame { background-color: #10131b; border: none; border-bottom: 1px solid #2b3040; border-radius: 0px; } QLabel { border: none; }")
        timeline_bar_layout = QHBoxLayout(timeline_bar); timeline_bar_layout.setContentsMargins(12, 5, 12, 5); timeline_bar_layout.setSpacing(8)
        self.lbl_timeline_title = QLabel("TIMELINE")
        self.lbl_timeline_title.setStyleSheet("color: #f9e2af; font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        self.lbl_timeline_summary = QLabel("0 clips")
        self.lbl_timeline_summary.setStyleSheet("color: #a6adc8; font-family: Consolas; font-size: 11px;")
        self.btn_timeline_snap = QPushButton("SNAP ON")
        self.btn_timeline_snap.setCheckable(True)
        self.btn_timeline_snap.setChecked(True)
        self.btn_timeline_snap.setFixedSize(78, 24)
        self.btn_timeline_snap.setStyleSheet("QPushButton { background-color: #313244; color: #a6e3a1; border: 1px solid #45475a; border-radius: 5px; font-family: Consolas; font-size: 10px; font-weight: bold; } QPushButton:!checked { color: #a6adc8; }")
        self.btn_timeline_snap.clicked.connect(self.set_timeline_snap_enabled)
        timeline_bar_layout.addWidget(self.lbl_timeline_title)
        timeline_bar_layout.addWidget(self.lbl_timeline_summary, stretch=1)
        self.btn_timeline_compact = QPushButton("COMPACT")
        self.btn_timeline_compact.setFixedSize(82, 24)
        self.btn_timeline_compact.setToolTip("压缩时间线，给画布更多空间")
        self.btn_timeline_compact.setStyleSheet("QPushButton { background-color: #252a3a; color: #cdd6f4; border: 1px solid #45475a; border-radius: 5px; font-family: Consolas; font-size: 10px; font-weight: bold; }")
        self.btn_timeline_compact.clicked.connect(lambda: self.set_timeline_height_preset("compact"))
        self.btn_timeline_large = QPushButton("LARGE")
        self.btn_timeline_large.setFixedSize(68, 24)
        self.btn_timeline_large.setToolTip("放大时间线，方便精修剪辑")
        self.btn_timeline_large.setStyleSheet("QPushButton { background-color: #252a3a; color: #cdd6f4; border: 1px solid #45475a; border-radius: 5px; font-family: Consolas; font-size: 10px; font-weight: bold; }")
        self.btn_timeline_large.clicked.connect(lambda: self.set_timeline_height_preset("large"))
        self.btn_timeline_hide = QPushButton("HIDE")
        self.btn_timeline_hide.setFixedSize(58, 24)
        self.btn_timeline_hide.setToolTip("隐藏时间线")
        self.btn_timeline_hide.setStyleSheet("QPushButton { background-color: #252a3a; color: #f9e2af; border: 1px solid #45475a; border-radius: 5px; font-family: Consolas; font-size: 10px; font-weight: bold; }")
        self.btn_timeline_hide.clicked.connect(lambda: self.set_timeline_visible(False))
        timeline_bar_layout.addWidget(self.btn_timeline_compact)
        timeline_bar_layout.addWidget(self.btn_timeline_large)
        timeline_bar_layout.addWidget(self.btn_timeline_hide)
        timeline_bar_layout.addWidget(self.btn_timeline_snap)
        tl_outer_layout.addWidget(timeline_bar)
        timeline_body = QWidget(); timeline_body_layout = QHBoxLayout(timeline_body); timeline_body_layout.setContentsMargins(0,0,0,0); timeline_body_layout.setSpacing(0)
        self.tl_header = TimelineHeader(controller=self); timeline_body_layout.addWidget(self.tl_header)
        self.timeline_widget = AdvancedTimeline(controller=self); timeline_body_layout.addWidget(self.timeline_widget, stretch=1)
        tl_outer_layout.addWidget(timeline_body, stretch=1)

        self.main_v_splitter = main_v_splitter
        self.top_h_splitter = top_h_splitter
        self.timeline_outer = timeline_outer
        self.center_panel = center_panel
        left_shell = QFrame()
        left_shell.setStyleSheet("QFrame { background-color: #181b24; border-right: 1px solid #2b3040; }")
        left_shell_layout = QHBoxLayout(left_shell)
        left_shell_layout.setContentsMargins(0, 0, 0, 0)
        left_shell_layout.setSpacing(0)

        side_rail = QFrame()
        side_rail.setFixedWidth(68)
        side_rail.setStyleSheet("QFrame { background-color: #11141d; border-right: 1px solid #2b3040; }")
        side_rail_layout = QVBoxLayout(side_rail)
        side_rail_layout.setContentsMargins(7, 10, 7, 10)
        side_rail_layout.setSpacing(6)
        self.side_nav_buttons = []

        def make_side_nav(text, tip, callback=None, active=False):
            btn = QPushButton(text)
            btn.setFixedSize(54, 54)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #aeb6c8;
                    border: none;
                    border-radius: 10px;
                    font-size: 11px;
                    font-weight: 800;
                    text-align: center;
                }
                QPushButton:hover { background: #202433; color: #ffffff; }
                QPushButton:checked { background: #242b3f; color: #ffffff; border: 1px solid #4f5b7a; }
            """)
            def on_clicked():
                for other in self.side_nav_buttons:
                    other.setChecked(False)
                btn.setChecked(True)
                if callback:
                    callback()
            btn.clicked.connect(on_clicked)
            self.side_nav_buttons.append(btn)
            side_rail_layout.addWidget(btn)
            if active:
                btn.setChecked(True)
            return btn

        self.btn_side_edit_workspace = make_side_nav("⌂\n精修", "字幕精修与剪辑", lambda: self.set_workspace_mode("edit"), active=True)
        self.btn_side_media_pool = make_side_nav("▦\n素材", "打开素材池", self.focus_media_pool)
        self.btn_side_design = make_side_nav("◇\n设计", "打开设计组件", self.focus_design_panel)
        side_rail_layout.addStretch()
        make_side_nav("⚙\n设置", "前往设置界面", lambda: self.parent_window().switch_room(4) if self.parent_window() and hasattr(self.parent_window(), "switch_room") else self.manual_save())

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(220)
        left_scroll.setStyleSheet("QScrollArea { background-color: #181b24; border: none; }")
        left_scroll.setWidget(left_panel)
        left_shell_layout.addWidget(side_rail)
        left_shell_layout.addWidget(left_scroll)
        self.left_content_panel = left_scroll
        self.side_rail = side_rail
        left_shell.setMinimumWidth(318)
        self.left_shell = left_shell
        self.right_panel = right_panel
        self.right_layout = right_layout
        center_panel.setMinimumWidth(360)
        right_panel.setMinimumWidth(430)

        top_h_splitter.addWidget(left_shell)
        top_h_splitter.addWidget(center_panel)
        top_h_splitter.addWidget(right_panel)
        top_h_splitter.setStretchFactor(0, 0)
        top_h_splitter.setStretchFactor(1, 4)
        top_h_splitter.setStretchFactor(2, 1)
        top_h_splitter.setSizes([328, 820, 460])
        right_panel.setVisible(False)
        top_h_splitter.setSizes([328, 1120, 0])

        main_v_splitter.addWidget(top_h_splitter)
        main_v_splitter.addWidget(timeline_outer)
        main_v_splitter.setStretchFactor(0, 1)
        main_v_splitter.setStretchFactor(1, 0)
        main_v_splitter.setCollapsible(1, False)
        timeline_outer.setVisible(False)
        main_v_splitter.setSizes([928, 0])

        main_layout.addWidget(main_v_splitter)
        self._create_floating_panel_toggles()

        self.load_project_on_boot(); self.init_web_engine_once(); self._switch_sub_page(1); self._apply_font_license_filter(); self._update_font_preview(); self._update_preset_preview(); self.switch_inspector("empty")
        self.refresh_preset_combo()
        self.refresh_signature_preset_combo()
        self.refresh_layout_preset_combo()
        self._update_workspace_status()
        self._sync_panel_toggle_buttons()
        self._refresh_edit_mode_controls()
        self.set_workspace_mode(self.workspace_mode, initial=True)
        QTimer.singleShot(300, self.update_floating_subtitle)
        QTimer.singleShot(500, self.auto_fit_editor_layout)
        QTimer.singleShot(680, self.refresh_media_pool)

        QTimer.singleShot(1000, self.check_and_download_ffmpeg)

    def _create_floating_panel_toggles(self):
        btn_style = """
            QPushButton {
                background-color: rgba(25, 29, 42, 238);
                color: #f4f6ff;
                border: 1px solid #4f5b7a;
                border-radius: 15px;
                font-size: 18px;
                font-weight: 900;
            }
            QPushButton:hover {
                background-color: #30384f;
                border-color: #89b4fa;
            }
        """
        self.btn_left_float_toggle = QPushButton("‹", self)
        self.btn_left_float_toggle.setObjectName("leftFloatingToggle")
        self.btn_left_float_toggle.setFixedSize(30, 52)
        self.btn_left_float_toggle.setToolTip("收起/展开工程栏")
        self.btn_left_float_toggle.setStyleSheet(btn_style)
        self.btn_left_float_toggle.clicked.connect(lambda: self.set_left_sidebar_visible(not self.left_content_panel.isVisible()))

        self.btn_right_float_toggle = QPushButton("‹", self)
        self.btn_right_float_toggle.setObjectName("rightFloatingToggle")
        self.btn_right_float_toggle.setFixedSize(30, 52)
        self.btn_right_float_toggle.setToolTip("收起/展开参数栏")
        self.btn_right_float_toggle.setStyleSheet(btn_style)
        self.btn_right_float_toggle.clicked.connect(lambda: self.set_right_sidebar_visible(not self.right_panel.isVisible()))

        self.btn_left_edge_toggle = self.btn_left_float_toggle
        self.btn_left_float_toggle.show()
        self.btn_right_float_toggle.show()
        self._position_floating_panel_toggles()

    def _position_floating_panel_toggles(self):
        if not hasattr(self, "top_h_splitter") or not hasattr(self, "btn_left_float_toggle"):
            return
        geom = self.top_h_splitter.geometry()
        sizes = self.top_h_splitter.sizes()
        if not sizes or geom.width() <= 0:
            return
        left_w = sizes[0] if len(sizes) > 0 else 0
        center_w = sizes[1] if len(sizes) > 1 else max(0, geom.width() - left_w)
        right_w = sizes[2] if len(sizes) > 2 else 0
        y = geom.y() + max(78, min(geom.height() - 78, geom.height() // 2 - 26))

        left_x = geom.x() + max(54, left_w) - 15
        if right_w > 0 and getattr(self, "right_panel", None) is not None and self.right_panel.isVisible():
            right_x = geom.x() + left_w + center_w - 15
        else:
            right_x = geom.x() + geom.width() - 34

        self.btn_left_float_toggle.move(max(4, min(left_x, self.width() - 68)), y)
        self.btn_right_float_toggle.move(max(4, min(right_x, self.width() - 34)), y)
        left_visible = getattr(self, "left_content_panel", None) is not None and self.left_content_panel.isVisible()
        right_visible = getattr(self, "right_panel", None) is not None and self.right_panel.isVisible()
        self.btn_left_float_toggle.setText("‹" if left_visible else "›")
        self.btn_right_float_toggle.setText("›" if right_visible else "‹")
        self.btn_left_float_toggle.raise_()
        self.btn_right_float_toggle.raise_()
        self._position_canvas_context_toolbar()

    def _position_canvas_context_toolbar(self):
        if not hasattr(self, "canvas_context_toolbar") or not hasattr(self, "preview_workspace"):
            return
        g = self.preview_workspace.geometry()
        if g.width() <= 0 or g.height() <= 0:
            return
        hint_w = self.canvas_context_toolbar.sizeHint().width() + 18
        tool_w = min(max(420, hint_w), max(320, g.width() - 48))
        x = g.x() + max(12, (g.width() - tool_w) // 2)
        y = g.y() + 12
        self.canvas_context_toolbar.setGeometry(x, y, tool_w, 42)
        if self.canvas_context_toolbar.isVisible():
            self.canvas_context_toolbar.raise_()

    def _layout_top_splitter(self):
        if not hasattr(self, "top_h_splitter"):
            return
        sizes = self.top_h_splitter.sizes()
        total = max(900, sum(sizes) or self.width())
        left_visible = getattr(self, "left_content_panel", None) is not None and self.left_content_panel.isVisible()
        right_visible = getattr(self, "right_panel", None) is not None and self.right_panel.isVisible()
        left_w = 328 if left_visible else 72
        right_w = 460 if right_visible else 0
        center_w = max(500, total - left_w - right_w)
        self.top_h_splitter.setSizes([left_w, center_w, right_w])
        QTimer.singleShot(0, self._position_floating_panel_toggles)

    def _on_right_tab_changed(self, index=None):
        if hasattr(self, "right_panel") and self.right_panel.isVisible():
            self._layout_top_splitter()

    def _sync_panel_toggle_buttons(self):
        if hasattr(self, "btn_toggle_left") and hasattr(self, "left_content_panel"):
            self.btn_toggle_left.blockSignals(True)
            self.btn_toggle_left.setChecked(self.left_content_panel.isVisible())
            self.btn_toggle_left.blockSignals(False)
        if hasattr(self, "btn_toggle_right") and hasattr(self, "right_panel"):
            self.btn_toggle_right.blockSignals(True)
            self.btn_toggle_right.setChecked(self.right_panel.isVisible())
            self.btn_toggle_right.blockSignals(False)
        if hasattr(self, "btn_toggle_timeline") and hasattr(self, "timeline_outer"):
            self.btn_toggle_timeline.blockSignals(True)
            self.btn_toggle_timeline.setChecked(self.timeline_outer.isVisible())
            self.btn_toggle_timeline.blockSignals(False)
        if hasattr(self, "chk_timeline_visible") and hasattr(self, "timeline_outer"):
            self.chk_timeline_visible.blockSignals(True)
            self.chk_timeline_visible.setChecked(self.timeline_outer.isVisible())
            self.chk_timeline_visible.blockSignals(False)

    def set_left_sidebar_visible(self, visible):
        if not hasattr(self, "left_content_panel"):
            return
        self.left_content_panel.setVisible(bool(visible))
        if hasattr(self, "left_shell"):
            self.left_shell.setMinimumWidth(318 if visible else 68)
        self._layout_top_splitter()
        self._sync_panel_toggle_buttons()
        self._position_floating_panel_toggles()
        self._schedule_preview_layout_refresh()

    def set_right_sidebar_visible(self, visible):
        if not hasattr(self, "right_panel"):
            return
        self.right_panel.setVisible(bool(visible))
        self._layout_top_splitter()
        self._sync_panel_toggle_buttons()
        self._position_floating_panel_toggles()
        self._schedule_preview_layout_refresh()

    def set_timeline_visible(self, visible):
        if not hasattr(self, "timeline_outer"):
            return
        self.timeline_outer.setVisible(bool(visible))
        if visible:
            self.set_timeline_height_preset("normal")
        elif hasattr(self, "main_v_splitter"):
            total = max(600, sum(self.main_v_splitter.sizes()) or self.height())
            self.main_v_splitter.setSizes([total, 0])
        self._sync_panel_toggle_buttons()
        self._position_floating_panel_toggles()
        self._schedule_preview_layout_refresh()

    def set_timeline_height_preset(self, preset="normal"):
        if not hasattr(self, "main_v_splitter") or not hasattr(self, "timeline_outer"):
            return
        self.timeline_outer.setVisible(True)
        total = max(620, sum(self.main_v_splitter.sizes()) or self.height())
        if preset == "compact":
            timeline_h = 112
        elif preset == "large":
            timeline_h = 280
        else:
            timeline_h = 168
        timeline_h = min(max(92, timeline_h), max(92, total - 300))
        self.main_v_splitter.setSizes([max(260, total - timeline_h), timeline_h])
        self._sync_panel_toggle_buttons()
        self._schedule_preview_layout_refresh()

    def _schedule_preview_layout_refresh(self):
        for delay in (0, 60, 180):
            QTimer.singleShot(delay, self.refresh_preview_layout)

    def refresh_preview_layout(self):
        if hasattr(self, "preview_workspace"):
            self.preview_workspace.update_stage_geometry()
        self._position_floating_panel_toggles()
        self.redraw_video_preview()
        self._sync_preview_overlay_transform(sync_js=True, update_status=True)
        self.last_render_hash = None
        self.update_floating_subtitle()

    def toggle_canvas_focus_mode(self):
        focus_on = not getattr(self, "_canvas_focus_mode", False)
        self._canvas_focus_mode = focus_on
        if focus_on:
            self._canvas_restore = {
                "left": hasattr(self, "left_shell") and self.left_shell.isVisible(),
                "right": hasattr(self, "right_panel") and self.right_panel.isVisible(),
                "timeline": hasattr(self, "timeline_outer") and self.timeline_outer.isVisible(),
            }
            self.set_left_sidebar_visible(False)
            self.set_right_sidebar_visible(False)
            self.set_timeline_visible(False)
        else:
            restore = getattr(self, "_canvas_restore", {"left": True, "right": True, "timeline": True})
            self.set_left_sidebar_visible(bool(restore.get("left", True)))
            self.set_right_sidebar_visible(bool(restore.get("right", True)))
            self.set_timeline_visible(bool(restore.get("timeline", True)))
        if hasattr(self, "btn_canvas_focus"):
            self.btn_canvas_focus.blockSignals(True)
            self.btn_canvas_focus.setChecked(focus_on)
            self.btn_canvas_focus.blockSignals(False)

    def show_canvas_context_toolbar(self, context="canvas"):
        if not hasattr(self, "canvas_context_toolbar"):
            return
        label_map = {
            "canvas": "画面",
            "subtitle": "字幕",
            "video": "视频",
            "audio": "音频",
            "design": "设计",
        }
        if hasattr(self, "lbl_canvas_context"):
            self.lbl_canvas_context.setText(label_map.get(context, "画面"))
        if context == "canvas" and self.state.get("video_clips"):
            self.current_selected_idx = -1
            self.selected_track = "video"
            self.update_floating_subtitle()
        for btn in getattr(self, "ctx_buttons", {}).values():
            btn.setVisible(False)
        visible_map = {
            "canvas": ["import_media", "split", "transition", "monitor", "video_duration", "audio", "delete"],
            "video": ["import_media", "split", "transition", "monitor", "video_duration", "audio", "delete"],
            "audio": ["import_media", "split", "audio", "delete"],
            "subtitle": ["split", "caption", "position", "motion", "delete"],
            "signature": ["signature"],
            "design": ["monitor", "delete"],
        }
        for key in visible_map.get(context, visible_map["canvas"]):
            if key in getattr(self, "ctx_buttons", {}):
                self.ctx_buttons[key].setVisible(True)
        if hasattr(self, "ctx_separator_media"):
            self.ctx_separator_media.setVisible(context in ("canvas", "video"))
        self._position_canvas_context_toolbar()
        self.canvas_context_toolbar.setVisible(True)
        self.canvas_context_toolbar.raise_()
        self._refresh_edit_mode_controls()
        self._update_workspace_status()

    def hide_canvas_context_toolbar(self):
        if hasattr(self, "canvas_context_toolbar"):
            self.canvas_context_toolbar.setVisible(False)

    def request_parent_workspace(self, workspace_key):
        if workspace_key == "design":
            self.focus_design_panel()
            return
        if workspace_key in ("media", "素材"):
            self.focus_media_pool()
            return
        parent = self.parent_window()
        if parent and hasattr(parent, "switch_room"):
            parent.switch_room(1, workspace_key=workspace_key)
        else:
            self.set_workspace_mode(workspace_key)

    def set_workspace_mode(self, mode="edit", initial=False):
        requested_mode = str(mode or "edit").lower()
        if requested_mode == "design":
            self.focus_design_panel()
            return
        if requested_mode in ("media", "素材"):
            self.focus_media_pool()
            return
        mode = "edit"
        self.workspace_mode = mode
        parent = self.parent_window()
        if parent and getattr(parent, "current_room_index", None) == 1:
            parent.current_workspace_key = mode
            if hasattr(parent, "_update_nav_selection"):
                parent._update_nav_selection()
        if hasattr(self, "design_box"):
            self.design_box.setVisible(False)
        if hasattr(self, "left_page_stack"):
            self.left_page_stack.setCurrentIndex(0)
            if hasattr(self, "left_content_panel") and self.left_content_panel.verticalScrollBar():
                self.left_content_panel.verticalScrollBar().setValue(0)
        if hasattr(self, "lbl_side_panel_title"):
            self.lbl_side_panel_title.setText("精修")
        if hasattr(self, "side_search"):
            self.side_search.setPlaceholderText("搜索工程 / 字幕 / 素材")
        if hasattr(self, "lbl_monitor_title"):
            self.lbl_monitor_title.setText("PROGRAM MONITOR")
        if hasattr(self, "lbl_timeline_title"):
            self.lbl_timeline_title.setText("TIMELINE")
        self._set_side_nav_active(getattr(self, "btn_side_edit_workspace", None))
        if not initial:
            if self.selected_track == "design":
                self.switch_inspector("empty")
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("🎬 精修工作台：字幕、素材、音频和逐句调整。")
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        self._update_workspace_status()

    def _set_side_nav_active(self, active_button):
        for btn in getattr(self, "side_nav_buttons", []) or []:
            btn.blockSignals(True)
            btn.setChecked(btn is active_button)
            btn.blockSignals(False)

    def focus_media_pool(self):
        self._set_side_nav_active(getattr(self, "btn_side_media_pool", None))
        if hasattr(self, "left_content_panel"):
            self.set_left_sidebar_visible(True)
        if hasattr(self, "left_page_stack"):
            self.left_page_stack.setCurrentIndex(1)
            if hasattr(self, "left_content_panel") and self.left_content_panel.verticalScrollBar():
                self.left_content_panel.verticalScrollBar().setValue(0)
        if hasattr(self, "lbl_side_panel_title"):
            self.lbl_side_panel_title.setText("素材")
        if hasattr(self, "media_pool_panel"):
            self.media_pool_panel.setVisible(True)
            self.media_pool_panel.set_highlighted(True)
            self.refresh_media_pool()
            QTimer.singleShot(900, lambda: self.media_pool_panel.set_highlighted(False) if hasattr(self, "media_pool_panel") else None)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("素材池已打开：可导入、选择素材，或拖到时间线。")

    def focus_design_panel(self):
        self._set_side_nav_active(getattr(self, "btn_side_design", None))
        if hasattr(self, "left_content_panel"):
            self.set_left_sidebar_visible(True)
        if hasattr(self, "left_page_stack"):
            self.left_page_stack.setCurrentIndex(2)
            if hasattr(self, "left_content_panel") and self.left_content_panel.verticalScrollBar():
                self.left_content_panel.verticalScrollBar().setValue(0)
        if hasattr(self, "lbl_side_panel_title"):
            self.lbl_side_panel_title.setText("设计")
        if hasattr(self, "design_box"):
            self.design_box.setVisible(True)
        if hasattr(self, "sync_design_panel_controls"):
            self.sync_design_panel_controls()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("设计组件已打开：可添加文字、色块、图片组件。")

    def set_edit_mode(self, enabled=True):
        self.edit_mode = True
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("编辑工具默认可用；时间线可按需手动打开。")
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        if hasattr(self, "browser"):
            self.browser.page().runJavaScript("if(typeof setEditMode === 'function') setEditMode(true);")
        self._refresh_edit_mode_controls()
        self._update_workspace_status()

    def _refresh_edit_mode_controls(self):
        self.edit_mode = True
        checked_widgets = ["btn_top_edit", "btn_edit_mode", "btn_context_edit_mode"]
        for name in checked_widgets:
            btn = getattr(self, name, None)
            if btn is not None:
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
                btn.setVisible(False)
                if name == "btn_top_edit":
                    btn.setText("编辑已开启")
                elif name == "btn_edit_mode":
                    btn.setText("编辑已开启")
                elif name == "btn_context_edit_mode":
                    btn.setText("编辑已开启")
        for key, btn in getattr(self, "ctx_buttons", {}).items():
            if key in {"import_media", "split", "transition", "delete"}:
                btn.setEnabled(True)
        if hasattr(self, "btn_add_text"):
            self.btn_add_text.setVisible(True)
        if hasattr(self, "insp_stack"):
            self.insp_stack.setEnabled(True)

    def _ensure_edit_mode(self, action="剪辑"):
        self.edit_mode = True
        return True

    def _supported_media_path(self, file_path):
        ext = os.path.splitext(file_path or "")[1].lower()
        if ext in self._video_exts:
            return "video"
        if ext in self._audio_exts:
            return "audio"
        return ""

    def import_media_dialog(self):
        if not self._ensure_edit_mode("导入素材"):
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择素材",
            "",
            "Media Files (*.mp4 *.mov *.webm *.mkv *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg)"
        )
        if file_path:
            self.add_media_from_path(file_path)

    def add_media_paths_to_timeline(self, file_paths, start_t=None):
        paths = [path for path in file_paths or [] if path and self._supported_media_path(path)]
        if not paths:
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("⚠️ 没有可导入的素材。")
            return False

        cursor = max(0.0, float(start_t if start_t is not None else self.current_play_time or 0.0))
        added_video = 0
        added_audio = 0
        for path in paths:
            media_type = self._supported_media_path(path)
            if media_type == "video":
                before = len(self.state.get("video_clips", []) or [])
                if self.add_video_clip_from_path(path, start_t=cursor):
                    clips = self.state.get("video_clips", []) or []
                    new_clip = self.state["video_clips"][self.current_v_idx] if 0 <= self.current_v_idx < len(clips) else None
                    if new_clip:
                        cursor = max(cursor, float(new_clip.get("end", cursor) or cursor))
                    elif len(clips) > before:
                        cursor = max(cursor, float(clips[-1].get("end", cursor) or cursor))
                    added_video += 1
            elif media_type == "audio" and added_audio == 0:
                if self.set_audio_path_from_file(path):
                    added_audio += 1

        if added_video or added_audio:
            if hasattr(self, "status_lbl"):
                parts = []
                if added_video:
                    parts.append(f"{added_video} 段画面已顺序入线")
                if added_audio:
                    parts.append("配音已导入")
                self.status_lbl.setText("；".join(parts) + "。")
            return True
        return False

    def pick_assembly_media_dialog(self):
        if not self._ensure_edit_mode("多素材组接"):
            return
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要组接的视频素材",
            "",
            "Video Files (*.mp4 *.mov *.webm *.mkv *.avi)"
        )
        if not file_paths:
            return
        self.assembly_media_paths = [
            path for path in file_paths
            if path and os.path.exists(path) and self._supported_media_path(path) == "video"
        ]
        self.refresh_assembly_media_list()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText(f"已选择 {len(self.assembly_media_paths)} 段素材，点击“一键组接”放入时间线。")

    def clear_assembly_media(self):
        self.assembly_media_paths = []
        self.refresh_assembly_media_list()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("组接素材已清空。")

    def _open_local_path_folder(self, path):
        path = str(path or "").strip()
        if not path:
            return
        folder = os.path.dirname(path) if os.path.splitext(path)[1] else path
        target = path if os.path.exists(path) else folder
        if os.name == "nt":
            try:
                if os.path.exists(path) and os.path.isfile(path):
                    subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
                elif folder and os.path.isdir(folder):
                    os.startfile(folder)
                else:
                    QMessageBox.warning(self, "找不到文件夹", f"路径不存在：\n{path}")
                return
            except Exception:
                pass
        if target and os.path.exists(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target if os.path.isdir(target) else os.path.dirname(target)))
        else:
            QMessageBox.warning(self, "找不到文件夹", f"路径不存在：\n{path}")

    def _open_local_file(self, path):
        path = str(path or "").strip()
        if not path or not os.path.exists(path):
            return QMessageBox.warning(self, "找不到素材", f"素材文件不存在：\n{path}")
        if os.name == "nt":
            try:
                os.startfile(path)
                return
            except Exception:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_assembly_media_context_menu(self, pos):
        if not hasattr(self, "assembly_list"):
            return
        item = self.assembly_list.itemAt(pos)
        if item is None:
            return
        self.assembly_list.setCurrentItem(item)
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return
        menu = QMenu(self)
        show_action = menu.addAction("显示完整路径")
        copy_action = menu.addAction("复制素材路径")
        folder_action = menu.addAction("打开所在文件夹")
        file_action = menu.addAction("打开素材文件")
        if not os.path.exists(path):
            file_action.setEnabled(False)
        chosen = menu.exec(self.assembly_list.viewport().mapToGlobal(pos))
        if chosen == show_action:
            QMessageBox.information(self, "素材完整路径", path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("已复制素材路径。")
        elif chosen == folder_action:
            self._open_local_path_folder(path)
        elif chosen == file_action:
            self._open_local_file(path)

    def refresh_assembly_media_list(self):
        if not hasattr(self, "assembly_list"):
            return
        self.assembly_list.clear()
        for idx, path in enumerate(getattr(self, "assembly_media_paths", []) or []):
            item = QListWidgetItem(f"{idx + 1}. {os.path.basename(path)}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.assembly_list.addItem(item)
        if not self.assembly_media_paths:
            self.assembly_list.addItem(QListWidgetItem("暂无组接素材，点击“选择素材”"))
        if hasattr(self, "assembly_count_label"):
            self.assembly_count_label.setText(f"{len(self.assembly_media_paths)} 段")

    def assemble_selected_media_to_timeline(self):
        paths = [
            path for path in getattr(self, "assembly_media_paths", []) or []
            if path and os.path.exists(path) and self._supported_media_path(path) == "video"
        ]
        if not paths:
            return QMessageBox.information(self, "没有组接素材", "请先在组接面板里选择几个视频素材。")
        start_t = self.current_play_time if self.edit_mode else None
        if self.assemble_media_paths_to_audio_duration(paths, start_t=start_t):
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"已按配音/字幕时长组接 {len(paths)} 段画面素材。")
            self.focus_media_pool()
            return True
        return False

    def assemble_selected_media_random_to_timeline(self):
        paths = [
            path for path in getattr(self, "assembly_media_paths", []) or []
            if path and os.path.exists(path) and self._supported_media_path(path) == "video"
        ]
        if not paths:
            return QMessageBox.information(self, "没有组接素材", "请先在组接面板里选择几个视频素材。")
        shuffled = list(paths)
        random.shuffle(shuffled)
        start_t = 0.0
        if self.assemble_media_paths_to_audio_duration(shuffled, start_t=start_t, replace_existing=True, assembly_mode="random_fill"):
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"已随机铺满 {len(shuffled)} 段画面素材。")
            self.focus_media_pool()
            return True
        return False

    def _assembly_target_duration(self, paths):
        audio_path = self.state.get("audio_path", "")
        if audio_path and os.path.exists(audio_path):
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    trimmed = max(0.0, float(a_trim[1]) - float(a_trim[0]))
                    if trimmed > 0:
                        return trimmed, "配音"
                except Exception:
                    pass
            audio_dur = float(get_exact_duration(audio_path) or 0.0)
            if audio_dur > 0:
                return audio_dur, "配音"
        sub_end = max([float(s.get("end", 0.0) or 0.0) for s in self.state.get("subs_data", []) or []] or [0.0])
        if sub_end > 0:
            return sub_end, "字幕"
        content_dur = float(self.state.get("content_duration", 0.0) or 0.0)
        if content_dur > 0:
            return content_dur, "工程"
        state_dur = float(self.state.get("duration", 0.0) or 0.0)
        if state_dur > 1.0:
            return max(1.0, state_dur - render_tail_padding_seconds()), "工程"
        return 0.0, "素材"

    def _build_assembly_clip_plan(self, paths, target_duration=0.0):
        valid = []
        for path in paths or []:
            if not path or not os.path.exists(path) or self._supported_media_path(path) != "video":
                continue
            try:
                meta = get_video_import_metadata(path)
                dur = float(meta.get("duration", 0.0) or 0.0)
                duration_info = meta.get("duration_info", {})
                width = int(meta.get("width", 0) or 0)
                height = int(meta.get("height", 0) or 0)
            except Exception:
                dur, duration_info, width, height = 0.0, {}, 0, 0
            if dur <= 0:
                dur = float(get_video_stream_duration(path) or get_exact_duration(path) or 0.0)
            if dur <= 0:
                dur = 5.0
            if width <= 0 or height <= 0:
                try:
                    width, height = get_video_dimensions(path)
                except Exception:
                    width, height = 0, 0
            valid.append({
                "path": path,
                "dur": max(0.05, dur),
                "duration_info": duration_info,
                "width": int(width or 0),
                "height": int(height or 0),
            })
        if not valid:
            return []

        source_total = sum(item["dur"] for item in valid)
        target_duration = float(target_duration or 0.0)
        if target_duration <= 0:
            target_duration = source_total
        if target_duration <= 0:
            target_duration = len(valid) * 5.0

        timeline_segments = []
        remaining = max(0.05, target_duration)
        for idx, item in enumerate(valid):
            if idx == len(valid) - 1:
                clip_len = remaining
            else:
                weight = item["dur"] / source_total if source_total > 0 else 1.0 / len(valid)
                clip_len = max(0.20, target_duration * weight)
                clip_len = min(clip_len, max(0.20, remaining - 0.20 * (len(valid) - idx - 1)))
                remaining -= clip_len
            timeline_segments.append({
                "path": item["path"],
                "timeline_duration": max(0.05, clip_len),
                "source_duration": item["dur"],
                "source_in": 0.0,
                "source_out": item["dur"],
                "speed": 1.0,
                "duration_info": item.get("duration_info", {}),
                "width": item.get("width", 0),
                "height": item.get("height", 0),
            })
        return timeline_segments

    def assemble_media_paths_to_audio_duration(self, paths, start_t=None, replace_existing=False, assembly_mode="audio_matched"):
        if not paths:
            return False
        target_duration, target_label = self._assembly_target_duration(paths)
        plan = self._build_assembly_clip_plan(paths, target_duration)
        if not plan:
            return False

        start = max(0.0, float(start_t if start_t is not None else 0.0))
        cursor = start
        clips = [] if replace_existing else list(self.state.get("video_clips", []) or [])
        new_clips = []
        for item in plan:
            clip_len = float(item.get("timeline_duration", 0.0) or 0.0)
            if clip_len <= 0:
                continue
            new_clip = {
                "path": self.cloud_import_media_if_needed(item["path"]),
                "start": cursor,
                "end": cursor + clip_len,
                "dur": float(item.get("source_duration", clip_len) or clip_len),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
                "scale": 100,
                "volume": 100,
                "duration_probe": item.get("duration_info", {}),
                "source_in": float(item.get("source_in", 0.0) or 0.0),
                "source_out": float(item.get("source_out", item.get("source_duration", clip_len)) or clip_len),
                "speed": float(item.get("speed", 1.0) or 1.0),
                "transition": {"type": "cut", "duration": 0.0},
                "assembly_mode": assembly_mode,
            }
            clips.append(new_clip)
            new_clips.append(new_clip)
            cursor = new_clip["end"]

        if not new_clips:
            return False
        clips.sort(key=lambda c: float(c.get("start", 0.0) or 0.0))
        self.state["video_clips"] = clips
        self.current_v_idx = clips.index(new_clips[0])
        self.current_selected_idx = -1
        self.selected_track = "video"
        if hasattr(self, "btn_v"):
            self.btn_v.setText("✅ 已组接素材")
        self._prepare_preview_proxies_for_clips(new_clips, announce=True)
        self._prime_video_preview_source(new_clips[0], announce=True)
        self.on_resolution_changed(self.state.get("resolution", get_output_resolution()))
        self.generate_waveform(new_clips[0]["path"], "v_wave_pixmap", max_seconds=90)
        threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
        self._recalc_duration()
        self.render_ui_list()
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.refresh_media_pool()
        self.auto_save_cache()
        self.switch_inspector("video")
        self.sync_player_to_time(new_clips[0]["start"])
        QTimer.singleShot(0, self._request_preview_video_refresh)
        QTimer.singleShot(280, self._request_preview_video_refresh)
        self.push_history()
        total = max(0.0, cursor - start)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText(f"已组接 {len(new_clips)} 段素材，按{target_label}时长分配到 {total:.1f}s。")
        return True

    def dragEnterEvent(self, event):
        if not self.edit_mode:
            event.ignore()
            return
        mime = event.mimeData()
        if mime and any(self._supported_media_path(url.toLocalFile()) for url in mime.urls()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        if not self._ensure_edit_mode("拖放素材"):
            event.ignore()
            return
        accepted = False
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path and self.add_media_from_path(local_path):
                accepted = True
        if accepted:
            event.acceptProposedAction()
        else:
            event.ignore()

    def add_media_from_path(self, file_path):
        media_type = self._supported_media_path(file_path)
        if media_type == "video":
            return self.add_video_clip_from_path(file_path, start_t=self.current_play_time if self.edit_mode else None)
        if media_type == "audio":
            return self.set_audio_path_from_file(file_path)
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 暂不支持这个素材格式。")
        return False

    def add_media_from_path_at_time(self, file_path, time_sec):
        media_type = self._supported_media_path(file_path)
        drop_time = max(0.0, float(time_sec or 0.0))
        if media_type == "video":
            return self.add_video_clip_from_path(file_path, start_t=drop_time)
        if media_type == "audio":
            if self.set_audio_path_from_file(file_path, record_history=False):
                a_dur = get_exact_duration(self.state.get("audio_path", ""))
                if a_dur > 0:
                    self.state["a_trim"] = [drop_time, drop_time + a_dur]
                    self.state["audio_source_in"] = 0.0
                    self._recalc_duration()
                    self.update_timeline_size()
                    self.auto_save_cache()
                    self.push_history()
                return True
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 暂不支持拖入这个素材格式。")
        return False

    def refresh_media_pool(self):
        if not hasattr(self, "media_pool_panel"):
            return
        items = []

        for idx, clip in enumerate(self.state.get("video_clips", []) or []):
            path = clip.get("path", "")
            name = os.path.basename(path) or f"视频 {idx + 1}"
            start = self._format_monitor_time(clip.get("start", 0.0)) if hasattr(self, "_format_monitor_time") else f"{float(clip.get('start', 0.0) or 0.0):.1f}s"
            end = self._format_monitor_time(clip.get("end", 0.0)) if hasattr(self, "_format_monitor_time") else f"{float(clip.get('end', 0.0) or 0.0):.1f}s"
            items.append((f"V{idx + 1}  {name}  {start}-{end}", {"type": "video", "path": path, "index": idx}))
        if self.state.get("audio_path"):
            items.append((f"A1  {os.path.basename(self.state.get('audio_path'))}", {"type": "audio", "path": self.state.get("audio_path"), "index": 0}))
        if self.state.get("music_path"):
            items.append((f"M1  {os.path.basename(self.state.get('music_path'))}", {"type": "music", "path": self.state.get("music_path"), "index": 0}))
        self.media_pool_panel.set_items(items)

    def add_selected_media_pool_item_to_timeline(self):
        if not hasattr(self, "media_pool_panel"):
            return False
        payload = self.media_pool_panel.current_payload()
        if not isinstance(payload, dict) or payload.get("type") == "empty":
            self.import_media_dialog()
            return False
        path = payload.get("path", "")
        media_type = payload.get("type", "")
        if media_type == "video" and path:
            return self.add_video_clip_from_path(path, start_t=self.current_play_time)
        if media_type == "audio" and path:
            return self.set_audio_path_from_file(path)
        if media_type == "music" and path:
            return self.set_music_path_from_file(path)
        return False

    def select_media_pool_payload(self, payload):
        if not isinstance(payload, dict) or payload.get("type") == "empty":
            return
        media_type = payload.get("type")
        idx = int(payload.get("index", 0) or 0)
        if media_type == "video":
            clips = self.state.get("video_clips", []) or []
            if 0 <= idx < len(clips):
                self.current_v_idx = idx
                self.current_selected_idx = -1
                self.selected_track = "video"
                self.switch_inspector("video")
                self.sync_player_to_time(float(clips[idx].get("start", 0.0) or 0.0))
        elif media_type == "audio":
            self.current_selected_idx = -1
            self.selected_track = "audio"
            self.switch_inspector("audio")
        elif media_type == "music":
            self.current_selected_idx = -1
            self.selected_track = "music"
            if hasattr(self, "timeline_widget"):
                self.timeline_widget.sync_from_controller()
            self._update_workspace_status()

    def _find_subtitle_at_time(self, time_sec):
        for i, s in enumerate(self.state.get("subs_data", [])):
            if float(s.get("start", 0.0) or 0.0) < time_sec < float(s.get("end", 0.0) or 0.0):
                return i
        return -1

    def _find_video_at_time(self, time_sec):
        for i, clip in enumerate(self.state.get("video_clips", [])):
            if float(clip.get("start", 0.0) or 0.0) < time_sec < float(clip.get("end", 0.0) or 0.0):
                return i
        return -1

    def split_at_playhead(self):
        if not self._ensure_edit_mode("剪刀切分"):
            return
        t = float(self.current_play_time or 0.0)
        if self.selected_track == "sub":
            idx = self.current_selected_idx if 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])) else self._find_subtitle_at_time(t)
            if idx >= 0 and self._split_subtitle_clip(idx, t):
                return
        if self.selected_track == "video":
            idx = self.current_v_idx if 0 <= self.current_v_idx < len(self.state.get("video_clips", [])) else self._find_video_at_time(t)
            if idx >= 0 and self._split_video_clip(idx, t):
                return
        sub_idx = self._find_subtitle_at_time(t)
        if sub_idx >= 0 and self._split_subtitle_clip(sub_idx, t):
            return
        vid_idx = self._find_video_at_time(t)
        if vid_idx >= 0 and self._split_video_clip(vid_idx, t):
            return
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("⚠️ 播放头没有落在可切分的片段中间。")

    def _split_subtitle_clip(self, idx, time_sec):
        subs = self.state.get("subs_data", [])
        if not (0 <= idx < len(subs)):
            return False
        clip = subs[idx]
        start = float(clip.get("start", 0.0) or 0.0)
        end = float(clip.get("end", start) or start)
        if not (start + 0.05 < time_sec < end - 0.05):
            return False
        left = copy.deepcopy(clip)
        right = copy.deepcopy(clip)
        left["end"] = time_sec
        right["start"] = time_sec
        left_words, right_words = [], []
        for word in clip.get("words", []):
            w = copy.deepcopy(word)
            ws = float(w.get("start", start) or start)
            we = float(w.get("end", end) or end)
            if we <= time_sec:
                left_words.append(w)
            elif ws >= time_sec:
                right_words.append(w)
            else:
                lw = copy.deepcopy(w); rw = copy.deepcopy(w)
                lw["end"] = time_sec; rw["start"] = time_sec
                left_words.append(lw); right_words.append(rw)
        if left_words and right_words:
            left["words"] = left_words
            right["words"] = right_words
            left["text"] = " ".join(str(w.get("text", "")).strip() for w in left_words).strip()
            right["text"] = " ".join(str(w.get("text", "")).strip() for w in right_words).strip()
        else:
            text = str(clip.get("text", "") or "")
            cut = max(1, min(len(text) - 1, int(len(text) * ((time_sec - start) / max(0.001, end - start)))))
            left["text"] = text[:cut].strip() or text
            right["text"] = text[cut:].strip() or text
            left["words"] = [{"text": left["text"], "start": start, "end": time_sec}]
            right["words"] = [{"text": right["text"], "start": time_sec, "end": end}]
        subs[idx:idx + 1] = [left, right]
        self.state["subs_data"] = sorted(subs, key=lambda x: float(x.get("start", 0.0) or 0.0))
        self.current_selected_idx = self.state["subs_data"].index(right)
        self.selected_track = "sub"
        self.render_ui_list()
        self.switch_inspector("sub")
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✂️ 字幕已在播放头切分。")
        return True

    def _split_video_clip(self, idx, time_sec):
        clips = self.state.get("video_clips", [])
        if not (0 <= idx < len(clips)):
            return False
        clip = clips[idx]
        start = float(clip.get("start", 0.0) or 0.0)
        end = float(clip.get("end", start) or start)
        if not (start + 0.05 < time_sec < end - 0.05):
            return False
        left = copy.deepcopy(clip)
        right = copy.deepcopy(clip)
        left["end"] = time_sec
        right["start"] = time_sec
        source_in = float(clip.get("source_in", 0.0) or 0.0)
        source_out = float(clip.get("source_out", clip.get("dur", end - start)) or clip.get("dur", end - start) or 0.0)
        source_len = max(0.001, source_out - source_in)
        timeline_offset = max(0.0, time_sec - start)
        source_cut = min(source_out, source_in + (timeline_offset % source_len))
        left["source_in"] = source_in
        left["source_out"] = max(source_in, source_cut)
        right["source_in"] = source_cut
        right["source_out"] = source_out
        right.setdefault("transition", {"type": "cut", "duration": 0.0})
        clips[idx:idx + 1] = [left, right]
        self.state["video_clips"] = sorted(clips, key=lambda x: float(x.get("start", 0.0) or 0.0))
        self.current_v_idx = self.state["video_clips"].index(right)
        self.current_selected_idx = -1
        self.selected_track = "video"
        self.switch_inspector("video")
        self._recalc_duration()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✂️ 视频片段已在播放头切分。")
        return True

    def apply_simple_transition(self):
        if not self._ensure_edit_mode("添加转场"):
            return
        if self.selected_track == "video" and 0 <= self.current_v_idx < len(self.state.get("video_clips", [])):
            clip = self.state["video_clips"][self.current_v_idx]
            clip["transition"] = {"type": "fade", "duration": 0.35}
            self.timeline_widget.sync_from_controller()
            self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText("✨ 已给当前视频片段标记 0.35s 淡化转场。")
            return
        if self.selected_track == "sub" and 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])):
            clip = self.state["subs_data"][self.current_selected_idx]
            clip.setdefault("style", {}).update({"anim_type": "fade", "pop_speed": 0.28})
            self.sync_inspector_to_clip()
            self.update_floating_subtitle()
            self.timeline_widget.sync_from_controller()
            self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText("✨ 已给当前字幕设置柔和淡入。")
            return
        self.status_lbl.setText("⚠️ 请先选中视频或字幕片段，再添加转场。")

    def apply_reference_two_line_layout(self):
        if not self._ensure_edit_mode("应用参考排版"):
            return
        if not self.state.get("subs_data"):
            return QMessageBox.information(self, "没有字幕", "当前工程还没有字幕片段可以应用排版。")

        if self.current_selected_idx == -1:
            current_clip = self.state["subs_data"][0]
            target_clips = self.state["subs_data"]
        else:
            current_clip = self.state["subs_data"][self.current_selected_idx]
            scope = self.style_scope_combo.currentIndex()
            if scope == 0:
                target_clips = self.state["subs_data"]
            elif scope == 1:
                target_clips = [c for c in self.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else:
                target_clips = [current_clip]

        reference_layout = copy.deepcopy(built_in_style_presets()[REFERENCE_NARRATIVE_BLOCK_PRESET])
        reference_position = reference_layout.pop(STYLE_PRESET_POSITION_KEY, {"pos_x": -23.0, "pos_y": 20.0})
        for clip in target_clips:
            clip["pos_x"] = reference_position["pos_x"]
            clip["pos_y"] = reference_position["pos_y"]
            clip.setdefault("style", {}).update(reference_layout)

        if self.style_scope_combo.currentIndex() == 0:
            self.state["default_pos_x"] = reference_position["pos_x"]
            self.state["default_pos_y"] = reference_position["pos_y"]
            self.default_style.update(reference_layout)

        smart_chunk_mode = REFERENCE_NARRATIVE_CHUNK_MODE
        self.state["chunk_mode"] = smart_chunk_mode
        if hasattr(self, "chunk_mode"):
            self.chunk_mode.blockSignals(True)
            if self.chunk_mode.findText(smart_chunk_mode) < 0:
                self.chunk_mode.addItem(smart_chunk_mode)
            self.chunk_mode.setCurrentText(smart_chunk_mode)
            self.chunk_mode.blockSignals(False)
        if self.current_selected_idx != -1:
            self.sync_inspector_to_clip()
        self._switch_sub_page(2)
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText("✅ 已应用参考视频四层累积叙事块：14-18词、左下左对齐、小大大小对比。")

    def delete_context_selection(self):
        if not self._ensure_edit_mode("删除片段"):
            return
        if self.selected_track == "design" and self.selected_design_layer_id:
            self.delete_selected_design_layer()
        elif self.selected_track == "music" and self.state.get("music_path"):
            self.remove_music()
        elif self.current_selected_idx != -1:
            self.delete_current_clip()
        elif self.state.get("video_clips"):
            self.remove_last_video_clip()

    def delete_timeline_selection(self, show_message=True):
        if not self._ensure_edit_mode("删除时间线片段"):
            return False
        selected = set(getattr(getattr(self, "timeline_widget", None), "selected_items", set()) or set())
        if not selected:
            before = copy.deepcopy(self._make_history_snapshot())
            self.delete_context_selection()
            return before != self._make_history_snapshot()

        parsed = []
        for key in selected:
            try:
                clip_type, idx_text = str(key).split(":", 1)
                parsed.append((clip_type, int(idx_text)))
            except Exception:
                continue
        if not parsed:
            return False

        changed = False
        video_indices = sorted({idx for clip_type, idx in parsed if clip_type == "video"}, reverse=True)
        sub_indices = sorted({idx for clip_type, idx in parsed if clip_type == "sub"}, reverse=True)
        design_indices = sorted({idx for clip_type, idx in parsed if clip_type == "design"}, reverse=True)

        clips = self.state.get("video_clips", []) or []
        for idx in video_indices:
            if 0 <= idx < len(clips):
                clips.pop(idx)
                changed = True
        if video_indices:
            self.state["video_clips"] = clips
            self.current_v_idx = min(max(0, self.current_v_idx), len(clips) - 1) if clips else -1
            if not clips:
                self.v_wave_pixmap = None
                self.video_thumbs = []
                self.last_video_image = None
                if hasattr(self, "btn_v"):
                    self.btn_v.setText("➕ 导入第一段画面 (MP4)")
                try:
                    self.player.stop()
                    self.player.setSource(QUrl())
                except Exception:
                    pass

        subs = self.state.get("subs_data", []) or []
        for idx in sub_indices:
            if 0 <= idx < len(subs):
                subs.pop(idx)
                changed = True
        if sub_indices:
            self.state["subs_data"] = subs
            self.current_selected_idx = -1

        if any(clip_type == "audio" for clip_type, _ in parsed) and self.state.get("audio_path"):
            self.state["audio_path"] = ""
            self.a_wave_pixmap = None
            try:
                self.audio_player.stop()
                self.audio_player.setSource(QUrl())
            except Exception:
                pass
            if hasattr(self, "btn_a"):
                self.btn_a.setText("🎵 导入独立配音 (可选)")
            changed = True

        if any(clip_type == "music" for clip_type, _ in parsed) and self.state.get("music_path"):
            self.state["music_path"] = ""
            self.state.pop("music_dur", None)
            self.state.pop("music_match_duration", None)
            self.state.pop("music_loop", None)
            try:
                self.music_player.stop()
                self.music_player.setSource(QUrl())
            except Exception:
                pass
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            changed = True

        if design_indices:
            state = self._current_design_state()
            page = self._design_page(state)
            layers = page.get("layers", []) or []
            delete_ids = {
                layers[idx].get("id", "")
                for idx in design_indices
                if 0 <= idx < len(layers)
            }
            if delete_ids:
                page["layers"] = [layer for layer in layers if layer.get("id", "") not in delete_ids]
                self.selected_design_layer_id = page["layers"][-1].get("id", "") if page["layers"] else ""
                self._commit_design_state(state, sync_controls=True, sync_timeline=False)
                changed = True

        if not changed:
            return False
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.selected_items.clear()
        self._recalc_duration()
        self.render_ui_list()
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.refresh_media_pool()
        self.auto_save_cache()
        self.push_history()
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        if hasattr(self, "status_lbl"):
            self.status_lbl.setText("🗑️ 已删除选中的时间线片段。")
        if show_message:
            QMessageBox.information(self, "已删除", "选中的时间线片段已删除。")
        return True

    def open_effects_dialog(self, target="caption"):
        title_map = {
            "caption": "字幕精修",
            "position": "字幕位置",
            "motion": "字幕动效",
            "media": "视频编辑",
            "audio": "音频选项",
            "signature": "固定署名",
            "design": "设计图层",
        }
        panel_title = title_map.get(target, "参数")
        if getattr(self, "_tabs_floating", False):
            self._dock_effect_tabs()

        if target == "caption":
            if self.current_selected_idx != -1:
                self.switch_inspector("sub")
                self._switch_sub_page(1)
            else:
                self.tabs.setCurrentIndex(0)
        elif target in ("position", "motion"):
            if self.current_selected_idx != -1:
                self.switch_inspector("sub")
            self.tabs.setCurrentIndex(1)
            self._switch_sub_page(0 if target == "position" else 3)
        elif target == "audio":
            self.switch_inspector("audio")
        elif target == "media":
            if self.selected_track == "audio":
                self.switch_inspector("audio")
            elif self.state.get("video_clips"):
                self.switch_inspector("video")
            else:
                self.tabs.setCurrentIndex(1)
        elif target == "signature":
            self.tabs.setCurrentWidget(self.signature_panel)
        elif target == "design":
            self.set_workspace_mode("design")

        if hasattr(self, "right_panel_title"):
            self.right_panel_title.setText(panel_title)
        self.set_right_sidebar_visible(True)
        if hasattr(self, "tabs"):
            self.tabs.raise_()

    def _dock_effect_tabs(self):
        if not getattr(self, "_tabs_floating", False):
            return
        if hasattr(self, "tabs") and hasattr(self, "right_layout"):
            self.tabs.setParent(self.right_panel)
            self.right_layout.addWidget(self.tabs)
        self._tabs_floating = False
        self.effects_dialog = None

    def apply_theme(self, colors, theme_key=None):
        self._theme_colors = colors
        self._theme_key = theme_key or ""
        apply_tinted_styles(self, colors)
        apply_room_theme_bridge(self, colors)
        if hasattr(self, "tl_header"):
            self.tl_header.update()
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.setStyleSheet(f"background-color: {colors['bg']}; border: none;")
            self.timeline_widget.scene.update()
            self.timeline_widget.viewport().update()
        self._refresh_editor_overlay_styles()
        self._position_canvas_context_toolbar()

    def _refresh_editor_overlay_styles(self):
        c = getattr(self, "_theme_colors", None)
        if not c:
            return
        if hasattr(self, "right_panel"):
            self.right_panel.setStyleSheet(f"""
                QFrame {{
                    background-color: {c['panel']};
                    border-left: 1px solid {c['border']};
                    border-radius: 0px;
                }}
                QLabel {{ color: {c['text']}; }}
            """)
        if hasattr(self, "canvas_context_toolbar"):
            self.canvas_context_toolbar.setStyleSheet(f"""
                QFrame {{
                    background-color: {c['panel']};
                    border: 1px solid {c['border']};
                    border-radius: 10px;
                }}
                QPushButton {{
                    background: transparent;
                    color: {c['text']};
                    border: none;
                    border-radius: 6px;
                    padding: 6px 9px;
                    font-weight: 800;
                }}
                QPushButton:hover {{
                    background: {c['panel_2']};
                }}
                QLabel {{
                    color: {c['muted']};
                    border: none;
                }}
            """)
        btn_style = f"""
            QPushButton {{
                background-color: {c['panel']};
                color: {c['text']};
                border: 1px solid {c['border']};
                border-radius: 15px;
                font-size: 18px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background-color: {c['panel_2']};
                border-color: {c['accent']};
            }}
        """
        for name in ("btn_left_float_toggle", "btn_right_float_toggle"):
            if hasattr(self, name):
                getattr(self, name).setStyleSheet(btn_style)

    def auto_fit_editor_layout(self):
        if getattr(self, "_auto_layout_applied", False):
            return
        self._auto_layout_applied = True
        screen_w = self.window().width() if self.window() else self.width()
        if screen_w and screen_w < 1450:
            self.set_left_sidebar_visible(False)
            self.set_right_sidebar_visible(False)
            self.set_timeline_visible(False)
        elif screen_w and screen_w < 1700:
            self.set_right_sidebar_visible(False)
            self.set_timeline_visible(False)
        elif hasattr(self, "timeline_outer") and self.timeline_outer.isVisible():
            self.set_timeline_visible(False)
        self._sync_panel_toggle_buttons()
        self._position_floating_panel_toggles()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._position_floating_panel_toggles)
        QTimer.singleShot(0, self._position_canvas_context_toolbar)
        if hasattr(self, "video_label"):
            QTimer.singleShot(0, self.redraw_video_preview)
            QTimer.singleShot(0, self._sync_preview_overlay_transform)

    # 👑 时光机核心引擎
    def _history_design_state(self):
        try:
            project_data = self._design_project_data()
            room_state = project_data.get("room_state", {}) if isinstance(project_data, dict) else {}
            return copy.deepcopy(room_state.get("design_room", default_design_room_state()))
        except Exception:
            return default_design_room_state()

    def _make_history_snapshot(self):
        return {
            "kind": "edit_snapshot_v2",
            "state": copy.deepcopy(self.state),
            "design_room": self._history_design_state(),
            "selected_track": self.selected_track,
            "current_selected_idx": self.current_selected_idx,
            "current_v_idx": self.current_v_idx,
            "selected_design_layer_id": self.selected_design_layer_id,
            "current_play_time": self.current_play_time,
        }

    def _restore_design_state_from_history(self, design_state):
        if not isinstance(design_state, dict):
            return
        design_state = normalize_design_room_state(design_state)
        parent = self.parent_window()
        project_data = self._design_project_data()
        try:
            project_data = update_room_state(project_data, "design_room", design_state)
        except Exception:
            project_data.setdefault("room_state", {})["design_room"] = copy.deepcopy(design_state)
        self.project_data = project_data
        if parent and hasattr(parent, "project"):
            parent.project = project_data

    def _apply_history_snapshot(self, snapshot):
        if isinstance(snapshot, list):
            self.state["subs_data"] = copy.deepcopy(snapshot)
            return
        if not isinstance(snapshot, dict):
            return
        if snapshot.get("kind") == "edit_snapshot_v2":
            self.state = copy.deepcopy(snapshot.get("state", self.state))
            self._restore_design_state_from_history(snapshot.get("design_room", {}))
            self.selected_track = snapshot.get("selected_track", "empty")
            self.current_selected_idx = int(snapshot.get("current_selected_idx", -1) or -1)
            self.current_v_idx = int(snapshot.get("current_v_idx", 0) or 0)
            self.selected_design_layer_id = snapshot.get("selected_design_layer_id", "")
            self.current_play_time = float(snapshot.get("current_play_time", 0.0) or 0.0)

    def _refresh_after_history_restore(self):
        self.last_render_hash = None
        clips = self.state.get("video_clips", []) or []
        if clips:
            self.current_v_idx = max(0, min(self.current_v_idx, len(clips) - 1))
            if hasattr(self, "btn_v"):
                self.btn_v.setText("✅ 已导原素材")
            try:
                clip = clips[self.current_v_idx]
                self._queue_preview_proxy_for_clip(clip, announce=True)
                self._prime_video_preview_source(clip, announce=True)
            except Exception:
                pass
        else:
            self.current_v_idx = -1
            self.last_video_image = None
            self.video_thumbs = []
            self.v_wave_pixmap = None
            if hasattr(self, "btn_v"):
                self.btn_v.setText("➕ 导入第一段画面 (MP4)")
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except Exception:
                pass
        audio_path = self.state.get("audio_path", "")
        if audio_path and os.path.exists(audio_path):
            if hasattr(self, "btn_a"):
                self.btn_a.setText("✅ " + os.path.basename(audio_path)[:15])
            try:
                self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
            except Exception:
                pass
        else:
            self.state["audio_path"] = ""
            self.a_wave_pixmap = None
            if hasattr(self, "btn_a"):
                self.btn_a.setText("🎵 导入独立配音 (可选)")
            try:
                self.audio_player.stop()
                self.audio_player.setSource(QUrl())
            except Exception:
                pass
        music_path = self.state.get("music_path", "")
        if music_path and os.path.exists(music_path):
            if hasattr(self, "btn_music"):
                self.btn_music.setText("✅ " + os.path.basename(music_path)[:15])
            try:
                self.music_player.setSource(QUrl.fromLocalFile(music_path))
                self.music_player.setLoops(QMediaPlayer.Loops.Infinite)
            except Exception:
                pass
        else:
            self.state["music_path"] = ""
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            try:
                self.music_player.stop()
                self.music_player.setSource(QUrl())
            except Exception:
                pass
        if self.current_selected_idx >= len(self.state.get("subs_data", []) or []):
            self.current_selected_idx = -1
        self.render_ui_list()
        if hasattr(self, "sync_design_panel_controls"):
            self.sync_design_panel_controls()
        self.update_timeline_size()
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.sync_from_controller()
        self.update_floating_subtitle()
        self.refresh_media_pool()
        self._update_workspace_status()
        self.auto_save_cache()

    def push_history(self):
        if not hasattr(self, "history"):
            self.history = []
            self.history_ptr = -1

        if self.history_ptr < len(self.history) - 1:
            self.history = self.history[:self.history_ptr + 1]

        current_state = self._make_history_snapshot()

        if self.history and self.history[-1] == current_state:
            return

        self.history.append(current_state)
        self.history_ptr += 1

        if len(self.history) > 50:
            self.history.pop(0)
            self.history_ptr -= 1

    def undo(self):
        if getattr(self, "history_ptr", -1) > 0:
            self.history_ptr -= 1
            self._apply_history_snapshot(self.history[self.history_ptr])
            self._refresh_after_history_restore()
            self.status_lbl.setText("↩️ 已撤销操作")

    def redo(self):
        if hasattr(self, "history") and self.history_ptr < len(self.history) - 1:
            self.history_ptr += 1
            self._apply_history_snapshot(self.history[self.history_ptr])
            self._refresh_after_history_restore()
            self.status_lbl.setText("↪️ 已重做操作")

    def check_and_download_ffmpeg(self):
        try:
            flags = 0x08000000 if os.name == 'nt' else 0
            subprocess.run([get_ffmpeg_cmd(), "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
            subprocess.run([get_ffprobe_cmd(), "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
            return
        except:
            pass

        reply = QMessageBox.question(self, '检测到核心引擎缺失', '首次运行或未打包环境。\n为了正常进行“AI 听译”和“音视频处理”，是否立即自动从云端节点下载部署引擎？（文件约130MB，请保持网络畅通）', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.download_ffmpeg()
        else:
            self.status_lbl.setText("❌ 缺少 FFmpeg，听译将不可用")

    def download_ffmpeg(self):
        self.ffmpeg_download_cancelled = False
        self.progress = QProgressDialog("正在从云端安全节点极速拉取引擎...", "取消", 0, 100, self)
        self.progress.setWindowTitle("自动部署引擎环境")
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.setMinimumDuration(0)
        self.progress.canceled.connect(self._cancel_ffmpeg_download)
        self.progress.setValue(0)
        self.progress.setLabelText("准备连接下载节点...")
        self.progress.show()
        self.progress.raise_()
        self.progress.activateWindow()
        self.dl_thread = threading.Thread(target=self._dl_ffmpeg_task, daemon=True)
        self.dl_thread.start()

    def _cancel_ffmpeg_download(self):
        self.ffmpeg_download_cancelled = True

    def _format_bytes(self, size):
        try:
            size = float(size)
        except Exception:
            return "0 B"
        units = ["B", "KB", "MB", "GB"]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024

    def _dl_ffmpeg_task(self):
        zip_path = os.path.join(tempfile.gettempdir(), "sh_ffmpeg_temp.zip")
        try:
            def report(percent, downloaded, total_size, speed):
                if total_size > 0:
                    msg = (
                        f"正在下载 FFmpeg 引擎... {percent}%\n"
                        f"{self._format_bytes(downloaded)} / {self._format_bytes(total_size)}   "
                        f"速度: {self._format_bytes(speed)}/s"
                    )
                else:
                    msg = (
                        "正在下载 FFmpeg 引擎... 已连接节点\n"
                        f"已下载 {self._format_bytes(downloaded)}   "
                        f"速度: {self._format_bytes(speed)}/s"
                    )
                self.sig_ffmpeg_progress.emit(percent, msg)

            download_file_with_progress(
                FFMPEG_DOWNLOAD_URL,
                zip_path,
                on_progress=report,
                is_cancelled=lambda: getattr(self, "ffmpeg_download_cancelled", False),
            )
            if getattr(self, "ffmpeg_download_cancelled", False):
                raise Exception("用户取消了下载。")

            self.sig_ffmpeg_progress.emit(99, "下载完成，正在解压并安装 ffmpeg.exe / ffprobe.exe...")

            app_dir = get_app_dir()
            extracted = []
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename.endswith('ffmpeg.exe'):
                        file_info.filename = 'ffmpeg.exe'
                        zip_ref.extract(file_info, app_dir)
                        extracted.append("ffmpeg.exe")
                    elif file_info.filename.endswith('ffprobe.exe'):
                        file_info.filename = 'ffprobe.exe'
                        zip_ref.extract(file_info, app_dir)
                        extracted.append("ffprobe.exe")

            if not extracted:
                raise Exception("压缩包里没有找到 ffmpeg.exe 或 ffprobe.exe。")

            flags = 0x08000000 if os.name == 'nt' else 0
            subprocess.run([get_ffmpeg_cmd(), "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
            subprocess.run([get_ffprobe_cmd(), "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
            self.sig_ffmpeg_success.emit("、".join(extracted))

        except Exception as e:
            if "取消" not in str(e):
                self.sig_ffmpeg_error.emit(str(e))
            else:
                self.sig_ffmpeg_canceled.emit()
        finally:
            if os.path.exists(zip_path):
                try: os.remove(zip_path)
                except: pass

    def _on_ffmpeg_progress(self, percent, message):
        if hasattr(self, "progress") and self.progress:
            if int(percent) <= 0:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 100)
            self.progress.setLabelText(message)
            self.progress.setValue(int(percent))
        self.status_lbl.setText(message.splitlines()[0])

    def _on_ffmpeg_success(self, extracted_files):
        if hasattr(self, "progress") and self.progress:
            self.progress.setRange(0, 100)
            self.progress.setLabelText("FFmpeg 引擎部署完成。")
            self.progress.setValue(100)
            self.progress.close()
            self.progress = None
        self.status_lbl.setText("✅ FFmpeg 引擎就绪")
        QTimer.singleShot(
            0,
            lambda: QMessageBox.information(
                self,
                "部署成功",
                f"🎉 FFmpeg 引擎已下载并安装完成！\n已安装: {extracted_files}\n现在可以继续打轴和渲染。"
            )
        )

    def _on_ffmpeg_error(self, message):
        if hasattr(self, "progress") and self.progress:
            self.progress.close()
            self.progress = None
        self.status_lbl.setText("❌ FFmpeg 部署失败")
        QMessageBox.critical(
            self,
            "部署受挫",
            f"自动下载似乎遇到了点麻烦：\n{message}\n\n您可以检查网络，或者手动把 ffmpeg.exe 和 ffprobe.exe 放进软件目录。"
        )

    def _on_ffmpeg_canceled(self):
        if hasattr(self, "progress") and self.progress:
            self.progress.close()
            self.progress = None
        self.status_lbl.setText("已取消 FFmpeg 下载")

    def _on_chunk_mode_change(self, text):
        self.state["chunk_mode"] = text
        self.auto_save_cache()

    def _on_timing_mode_change(self, text):
        self.state["timing_mode"] = text
        self.auto_save_cache()

    def _on_fill_gap_change(self, state):
        self.state["fill_subtitle_gaps"] = state == Qt.CheckState.Checked.value
        self.auto_save_cache()

    def init_web_engine_once(self):
        html_content = r"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                __FONT_FACE_CSS__
                html, body { margin: 0; padding: 0; background: transparent !important; overflow: hidden; width: 100vw; height: 100vh; display: flex; justify-content: center; align-items: center; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; touch-action: none; }
                #scale-wrapper { width: 100vw; height: 100vh; position: absolute; left: 0; top: 0; cursor: grab; will-change: transform; background: transparent !important; }
                #scale-wrapper.panning { cursor: grabbing; }
                #design-layer { position: absolute; inset: 0; pointer-events: none; z-index: 2; }
                #signature-layer { position: absolute; inset: 0; pointer-events: none; z-index: 90; }

                .drag-container { position: absolute; transform: translate(-50%, -50%); width: max-content; max-width: 92%; will-change: left, top, transform; overflow: visible; }
                .sub-box { outline: none; overflow: visible; }

                #safe-area { position: absolute; top: 15%; bottom: 20%; left: 10%; right: 10%; border: 2px dashed rgba(255, 255, 255, 0.4); pointer-events: none; z-index: 999; display: none; }

                /* 👑 冰蓝呼吸灯高光边框 */
                .selected-box {
                    border: 2px solid rgba(137, 180, 250, 1) !important;
                    box-shadow: 0 0 15px 4px rgba(137, 180, 250, 0.5) !important;
                    border-radius: 8px;
                    z-index: 9999 !important;
                }
                .sub-box:hover { border: 2px dashed rgba(255,255,255,0.7); cursor: move; }

                .width-handle {
                    position: absolute; width: 6px; height: 24px; background: white;
                    border: 2px solid rgba(137, 180, 250, 1); border-radius: 4px;
                    display: none; z-index: 20; top: 50%; transform: translateY(-50%);
                    cursor: ew-resize; box-shadow: 0 0 5px rgba(0,0,0,0.5);
                }
                .selected-box .width-handle { display: block; }
                .ml { left: -10px; }
                .mr { right: -10px; }
            </style>
        </head>
        <body id="canvas">
            <div id="scale-wrapper">
                <div id="safe-area"></div>
                <div id="design-layer"></div>
                <div id="signature-layer"></div>
            </div>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <script>
                var backend;
                new QWebChannel(qt.webChannelTransport, function (channel) { backend = channel.objects.backend; });

                let PROJ_W = 1080;
                let PROJ_H = 1920;

                function setResolution(w, h) {
                    PROJ_W = w; PROJ_H = h;
                }

                // 监看器缩放 / 平移只影响预览，不改工程输出参数。
                let currentZoom = 1.0;
                let monitorPanX = 0.0;
                let monitorPanY = 0.0;
                let EDIT_MODE = true;
                function setEditMode(enabled) {
                    EDIT_MODE = !!enabled;
                    document.body.classList.toggle('edit-mode', EDIT_MODE);
                }
                function applyMonitorTransform() {
                    const wrapper = document.getElementById('scale-wrapper');
                    if (!wrapper) return;
                    wrapper.dataset.previewZoom = currentZoom.toFixed(4);
                }
                function setMonitorView(zoom, panX, panY) {
                    currentZoom = Math.min(Math.max(Number(zoom) || 1.0, 0.25), 8.0);
                    monitorPanX = Number(panX) || 0.0;
                    monitorPanY = Number(panY) || 0.0;
                    applyMonitorTransform();
                }
                window.addEventListener('wheel', (e) => {
                    if (e.ctrlKey) {
                        e.preventDefault();
                        if (backend) backend.adjust_monitor_zoom(e.deltaY, e.clientX, e.clientY);
                    }
                }, {passive: false});

                let isMonitorPanning = false;
                let panLastX = 0;
                let panLastY = 0;
                function isOverlayControlTarget(target) {
                    if (!target || typeof target.closest !== 'function') return false;
                    return !!(target && (
                        target.closest('.drag-container') ||
                        target.closest('.sub-box') ||
                        target.classList.contains('width-handle')
                    ));
                }
                window.addEventListener('mousedown', (e) => {
                    if (e.button !== 0 || isOverlayControlTarget(e.target)) return;
                    if (backend) backend.show_context_toolbar();
                    isMonitorPanning = true;
                    panLastX = e.clientX;
                    panLastY = e.clientY;
                    document.getElementById('scale-wrapper')?.classList.add('panning');
                    e.preventDefault();
                });
                window.addEventListener('mousemove', (e) => {
                    if (!isMonitorPanning) return;
                    const dx = e.clientX - panLastX;
                    const dy = e.clientY - panLastY;
                    panLastX = e.clientX;
                    panLastY = e.clientY;
                    monitorPanX += dx;
                    monitorPanY += dy;
                    applyMonitorTransform();
                    if (backend) backend.pan_monitor_view(dx, dy);
                    e.preventDefault();
                });
                window.addEventListener('mouseup', () => {
                    if (!isMonitorPanning) return;
                    isMonitorPanning = false;
                    document.getElementById('scale-wrapper')?.classList.remove('panning');
                    if (backend) backend.finalize_monitor_pan();
                });
                window.addEventListener('dblclick', (e) => {
                    if (isOverlayControlTarget(e.target)) return;
                    if (backend) backend.reset_monitor_view();
                });

                function ensureSubtitleBox(el, idx) {
                    let box = el.querySelector('.sub-box');
                    if(!box) return null;
                    if(!box.hasAttribute('data-handles')) {
                        box.setAttribute('data-handles', 'true');
                        ['ml', 'mr'].forEach(pos => {
                            let c = document.createElement('div'); c.className = `width-handle ${pos}`; box.appendChild(c);
                        });
                        makeResizable(box, idx);
                    }
                    if(el.dataset.dragBound !== 'true') {
                        makeDraggable(el);
                        el.dataset.dragBound = 'true';
                    }
                    return box;
                }

                function syncSubs(subsJson) {
                    const subs = JSON.parse(subsJson);
                    const canvas = document.getElementById('scale-wrapper');
                    const currentIds = new Set(subs.map(s => s.idx));
                    Array.from(canvas.children).forEach(el => {
                        if(el.classList.contains('drag-container') && !currentIds.has(parseInt(el.dataset.idx))) el.remove();
                    });

                    subs.forEach(sub => {
                        let el = document.getElementById('drag-' + sub.idx);
                        if(!el) {
                            el = document.createElement('div');
                            el.id = 'drag-' + sub.idx;
                            el.className = 'drag-container';
                            el.dataset.idx = sub.idx;
                            canvas.appendChild(el);
                        }

                        if(sub.htmlText.trim() === "") {
                            el.style.display = 'none';
                        } else {
                            el.style.display = 'block';
                            if (el.innerHTML !== sub.htmlText) {
                                el.innerHTML = sub.htmlText;
                            }
                        }

                        let box = ensureSubtitleBox(el, sub.idx);
                        if(box) {
                            if(sub.isSelected) { box.classList.add('selected-box'); }
                            else { box.classList.remove('selected-box'); }
                        }

                        if (sub.box_width > 0) {
                            el.style.width = sub.box_width + 'vw';
                        } else {
                            el.style.width = 'max-content';
                        }

                        el.dataset.pos_x = sub.pos_x;
                        el.dataset.pos_y = sub.pos_y;
                        el.style.left = `calc(50% + ${sub.pos_x}%)`;
                        el.style.top = `calc(50% + ${sub.pos_y}%)`;
                        el.style.zIndex = sub.track === 0 ? "30" : "20";
                    });
                }

                function syncSignature(htmlText) {
                    const layer = document.getElementById('signature-layer');
                    if (!layer) return;
                    if (!htmlText || htmlText.trim() === "") {
                        layer.innerHTML = "";
                        layer.style.display = "none";
                    } else {
                        layer.style.display = "block";
                        if (layer.innerHTML !== htmlText) layer.innerHTML = htmlText;
                    }
                }

                function syncDesign(htmlText) {
                    const layer = document.getElementById('design-layer');
                    if (!layer) return;
                    if (!htmlText || htmlText.trim() === "") {
                        layer.innerHTML = "";
                        layer.style.display = "none";
                    } else {
                        layer.style.display = "block";
                        if (layer.innerHTML !== htmlText) layer.innerHTML = htmlText;
                    }
                }

                function makeDraggable(el) {
                    if(el.dataset.dragBound === 'true') return;
                    let isDragging = false, hasMoved = false, startX, startY;
                    el.addEventListener('mousedown', e => {
                        let box = el.querySelector('.sub-box');
                        if ((box && box.isContentEditable) || e.target.classList.contains('width-handle')) return;
                        if(backend) backend.notify_selected(parseInt(el.dataset.idx));
                        if(!EDIT_MODE) return;
                        isDragging = true; hasMoved = false; startX = e.clientX; startY = e.clientY;
                        el.dataset.ox = parseFloat(el.dataset.pos_x) || 0;
                        el.dataset.oy = parseFloat(el.dataset.pos_y) || 0;
                        delete el.dataset.vx;
                        delete el.dataset.vy;
                    });
                    window.addEventListener('mousemove', e => {
                        if (!isDragging) return;
                        const rawDx = e.clientX - startX;
                        const rawDy = e.clientY - startY;
                        if (!hasMoved && Math.hypot(rawDx, rawDy) < 3) return;
                        hasMoved = true;
                        let wrapper = document.getElementById('scale-wrapper');
                        let rect = wrapper.getBoundingClientRect();

                        // 修正缩放后的拖拽比例
                        let dx_pct = rawDx / rect.width * 100;
                        let dy_pct = rawDy / rect.height * 100;

                        let cx = parseFloat(el.dataset.ox) + dx_pct;
                        let cy = parseFloat(el.dataset.oy) + dy_pct;

                        el.style.left = `calc(50% + ${cx}%)`;
                        el.style.top = `calc(50% + ${cy}%)`;
                        el.dataset.vx = cx; el.dataset.vy = cy;
                    });
                    window.addEventListener('mouseup', e => {
                        if (isDragging) {
                            isDragging = false;
                            if(hasMoved && backend && el.dataset.vx) backend.update_coordinates(parseInt(el.dataset.idx), parseFloat(el.dataset.vx), parseFloat(el.dataset.vy));
                            delete el.dataset.vx;
                            delete el.dataset.vy;
                        }
                    });
                    el.addEventListener('dblclick', () => {
                        if(!EDIT_MODE) return;
                        let box = el.querySelector('.sub-box');
                        if(box) {
                            box.classList.add('editing');
                            box.contentEditable = true;
                            box.focus();
                        }
                    });
                    el.addEventListener('blur', (e) => {
                        let box = el.querySelector('.sub-box');
                        if(box) {
                            box.classList.remove('editing');
                            box.contentEditable = false;
                            if(backend) backend.update_text_from_screen(parseInt(el.dataset.idx), box.innerText);
                        }
                    }, true);
                    el.addEventListener('wheel', (e) => {
                        if(!EDIT_MODE) return;
                        let box = el.querySelector('.sub-box');
                        if (box && box.isContentEditable) return;
                        // 不再拦截非 Ctrl 的滚轮（修复冲突）
                        if(!e.ctrlKey) {
                            e.preventDefault();
                            if(backend) backend.adjust_font_size(parseInt(el.dataset.idx), e.deltaY < 0 ? 2 : -2);
                        }
                    });
                }

                window.__activeWidthResize = null;

                function makeResizable(box, idx) {
                    const handles = box.querySelectorAll('.width-handle');
                    let isResizingWidth = false;
                    let dragContainer = box.closest('.drag-container');

                    handles.forEach(c => {
                        c.addEventListener('mousedown', e => {
                            if(!EDIT_MODE) return;
                            e.stopPropagation();
                            window.__activeWidthResize = { dragContainer, idx };
                            isResizingWidth = true;
                        });
                    });

                    if (window.__resizeListenersReady) return;
                    window.__resizeListenersReady = true;
                    window.addEventListener('mousemove', e => {
                        if (window.__activeWidthResize) {
                            let dragContainer = window.__activeWidthResize.dragContainer;
                            if (!dragContainer) return;
                            let wrapper = document.getElementById('scale-wrapper');
                            let rect = wrapper.getBoundingClientRect();
                            let boxRect = dragContainer.getBoundingClientRect();
                            let boxCx = boxRect.left + boxRect.width/2;

                            // 舞台由原生窗口尺寸缩放，rect 已经是当前视图尺寸。
                            let newHalfWidth = Math.abs(e.clientX - boxCx);
                            let newWidthPx = Math.max(newHalfWidth * 2, 100);
                            let newWidthVw = (newWidthPx / rect.width) * 100;
                            dragContainer.style.width = newWidthVw + 'vw';
                            dragContainer.dataset.newWidth = newWidthVw;
                        }
                    });

                    window.addEventListener('mouseup', () => {
                        if (window.__activeWidthResize) {
                            let dragContainer = window.__activeWidthResize.dragContainer;
                            let idx = window.__activeWidthResize.idx;
                            window.__activeWidthResize = null;
                            isResizingWidth = false;
                            if (backend && dragContainer && dragContainer.dataset.newWidth) {
                                backend.update_box_width(idx, parseFloat(dragContainer.dataset.newWidth));
                                dragContainer.dataset.newWidth = "";
                            }
                        }
                    });
                }
            </script>
        </body>
        </html>
        """
        html_content = html_content.replace("__FONT_FACE_CSS__", font_face_css())
        self._force_preview_web_transparency()
        self.browser.setHtml(html_content)
        QTimer.singleShot(0, self._force_preview_web_transparency)
        QTimer.singleShot(0, lambda: self._set_preview_overlay_visible(getattr(self, "_preview_overlay_has_content", False)))
        QTimer.singleShot(160, self._force_preview_web_transparency)
    # 👑 新增：实时将文案同步到内存，按 Ctrl+S 时就会一起写入工程文件
    def _on_custom_text_changed(self):
        self.state["custom_text"] = self.text_editor.toPlainText()
        self.auto_save_cache()

    # 👑 新增：手动触发 NLP 清洗文案并展示出来
    def format_custom_text_manually(self):
        raw_text = self.text_editor.toPlainText().strip()
        if not raw_text:
            return QMessageBox.information(self, "提示", "剪贴板是空的，无需清洗哦！")

        cleaned_text = self._clean_and_format_user_text(raw_text)

        self.text_editor.blockSignals(True)
        self.text_editor.setPlainText(cleaned_text)
        self.text_editor.blockSignals(False)

        self.state["custom_text"] = cleaned_text
        self.auto_save_cache()
        self.status_lbl.setText("🧹 文案清洗完毕！空格与大小写已修正。")

    def load_style_presets(self):
        presets = {}
        loaded = read_json_file(PRESETS_FILE, default={})
        if isinstance(loaded, dict):
            presets = loaded
        return merge_built_in_style_presets(presets)

    def save_style_presets(self, data):
        try:
            built_ins = set(built_in_style_presets().keys())
            user_data = {k: v for k, v in (data or {}).items() if k not in built_ins}
            write_json_file(PRESETS_FILE, user_data, indent=2)
        except: pass

    def notify_batch_presets_changed(self, style_name=None, signature_name=None):
        parent = self.parent_window()
        room_batch = getattr(parent, "room_batch", None) if parent else None
        if room_batch and hasattr(room_batch, "refresh_external_presets"):
            room_batch.refresh_external_presets(style_name=style_name, signature_name=signature_name)

    def _built_in_signature_presets(self):
        return {
            "右上角柔光玻璃": default_signature_config(self.default_style),
            "右上角纯色小标": {
                **default_signature_config(self.default_style),
                "style": {
                    **default_signature_config(self.default_style)["style"],
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
                **default_signature_config(self.default_style),
                "style": {
                    **default_signature_config(self.default_style)["style"],
                    "bg_mode": "none",
                    "bg_alpha": 0,
                    "stroke_width": 2,
                    "shadow_alpha": 70,
                },
            },
        }

    def load_signature_presets(self):
        presets = self._built_in_signature_presets()
        saved = read_json_file(SIGNATURE_PRESETS_FILE, default={})
        if isinstance(saved, dict):
            presets.update(saved)
        return presets

    def save_signature_presets(self, data):
        try:
            built_ins = set(self._built_in_signature_presets().keys())
            user_data = {k: v for k, v in data.items() if k not in built_ins}
            write_json_file(SIGNATURE_PRESETS_FILE, user_data, indent=2)
        except Exception:
            pass

    def refresh_signature_preset_combo(self):
        if not hasattr(self, "signature_template_combo"):
            return
        self.signature_template_combo.blockSignals(True)
        self.signature_template_combo.clear()
        for name in self.load_signature_presets().keys():
            self.signature_template_combo.addItem(name, userData=name)
        self.signature_template_combo.blockSignals(False)

    # 👑 视觉预设下拉框核心引擎：调用 QPainter 纯手工绘制所见即所得
    def refresh_preset_combo(self):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()

        # 放大下拉框的图标尺寸，以容纳视觉预览图
        self.preset_combo.setIconSize(QSize(220, 40))
        self.preset_combo.setStyleSheet("""
            QComboBox { background-color: #11111b; border: 1px solid #313244; border-radius: 6px; padding: 5px; }
            QComboBox::drop-down { border: none; width: 30px; }
            QComboBox QAbstractItemView { background-color: #1e1e2e; selection-background-color: #313244; outline: none; border-radius: 6px; border: 1px solid #313244; }
            QComboBox QAbstractItemView::item { min-height: 50px; padding: 4px; }
        """)

        presets = self.load_style_presets()
        if presets:
            for name, raw_preset in presets.items():
                st, _ = split_style_preset(raw_preset)
                # 👑 开启 2D 硬件加速画笔，直接在内存中绘制图标
                pixmap = QPixmap(220, 40)
                pixmap.fill(Qt.GlobalColor.transparent) # 透明底色
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

                # 1. 提取并绘制背景 (支持胶带和全局底框)
                bg_mode = st.get("bg_mode", "none")
                if bg_mode != "none":
                    try:
                        bg_col = st.get("bg_color", "#000000").lstrip('#')
                        r, g, b = tuple(int(bg_col[i:i+2], 16) for i in (0, 2, 4))
                        alpha = int(st.get("bg_alpha", 80) * 2.55) # 转换 0-100 为 0-255
                        radius = 6 # 预览图固定小圆角

                        painter.setBrush(QColor(r, g, b, alpha))
                        painter.setPen(Qt.PenStyle.NoPen)
                        # 在中间画一个完美的底框
                        painter.drawRoundedRect(QRectF(10, 4, 200, 32), radius, radius)
                    except: pass

                # 2. 提取字体并设置字号
                family = st.get("font", "Segoe UI")
                # 限制字号在 12-16 之间，防止撑爆预览框
                size = max(11, min(15, int(st.get("size", 100) * 0.10)))
                font = QFont(family, size)
                try:
                    font.setWeight(QFont.Weight(int(st.get("font_weight", "700"))))
                except Exception:
                    font.setWeight(QFont.Weight.Bold)
                font.setItalic(str(st.get("font_style", "normal")).lower() == "italic")
                painter.setFont(font)

                # 3. 提取文字颜色
                txt_col = st.get("color_txt", "#FFFFFF").lstrip('#')
                try:
                    tr, tg, tb = tuple(int(txt_col[i:i+2], 16) for i in (0, 2, 4))
                    color_obj = QColor(tr, tg, tb)
                except:
                    color_obj = QColor(Qt.GlobalColor.white)

                # 4. 绘制硬阴影或描边 (稍微偏移画一层深色，增加立体感)
                if st.get("shadow_alpha", 100) > 0 or st.get("stroke_width", 0) > 0:
                    painter.setPen(QColor(0, 0, 0, 180)) # 黑色半透明阴影
                    painter.drawText(QRectF(11, 5, 200, 32), Qt.AlignmentFlag.AlignCenter, name)

                # 5. 绘制主文字
                painter.setPen(color_obj)
                painter.drawText(QRectF(10, 4, 200, 32), Qt.AlignmentFlag.AlignCenter, name)

                painter.end() # 结束绘制

                # 将绘制好的绝美画卷，作为图标塞进下拉框！
                self.preset_combo.addItem(QIcon(pixmap), "", userData=name)
        else:
            self.preset_combo.addItem("暂无自定义预设", userData="none")

        self.preset_combo.blockSignals(False)

    # 👑 2. 适配视觉预设的读取逻辑
    def save_style_preset(self):
        if self.current_selected_idx == -1: return QMessageBox.warning(self, "提示", "请先在时间线上选中一个调好的字幕片段！")
        name, ok = QInputDialog.getText(self, "💾 存为预设", "给这个酷炫的样式起个名字吧\n(例如: 爆款红底白字):")
        if ok and name.strip():
            clip = self.state["subs_data"][self.current_selected_idx]
            style_data = copy.deepcopy(clip.get("style", self.default_style))
            style_data[STYLE_PRESET_POSITION_KEY] = {
                "pos_x": float(clip.get("pos_x", self.state.get("default_pos_x", 0.0))),
                "pos_y": float(clip.get("pos_y", self.state.get("default_pos_y", 25.0))),
            }
            presets = self.load_style_presets()
            presets[name.strip()] = style_data
            self.save_style_presets(presets)
            self.refresh_preset_combo()
            self.notify_batch_presets_changed(style_name=name.strip())

            # 👑 修复：根据隐藏的 userData 找到刚保存的预设并选中它
            idx = self.preset_combo.findData(name.strip(), Qt.ItemDataRole.UserRole)
            if idx >= 0: self.preset_combo.setCurrentIndex(idx)

            self._update_preset_preview()
            QMessageBox.information(self, "成功", f"预设 '{name.strip()}' 已保存入库！")

    def delete_style_preset(self):
        # 👑 修复：读取隐藏的 userData
        name = self.preset_combo.currentData(Qt.ItemDataRole.UserRole)
        if not name or name == "none": return
        presets = self.load_style_presets()
        if name in presets:
            reply = QMessageBox.question(self, '删除预设', f'确定要删除预设 "{name}" 吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                del presets[name]
                self.save_style_presets(presets)
                self.refresh_preset_combo()
                self.notify_batch_presets_changed()
                self._update_preset_preview()

    def manage_style_presets(self):
        presets = self.load_style_presets()
        if not presets:
            return QMessageBox.information(self, "预设库", "当前还没有保存过字幕预设。")
        names = list(presets.keys())
        name, ok = QInputDialog.getItem(self, "管理预设库", "选择预设:", names, 0, False)
        if not ok or not name:
            return
        action, ok = QInputDialog.getItem(self, "管理预设库", "选择操作:", ["重命名", "删除", "用当前字幕覆盖"], 0, False)
        if not ok:
            return
        target_name = name
        if action == "重命名":
            new_name, ok = QInputDialog.getText(self, "重命名预设", "新名称:", text=name)
            if ok and new_name.strip() and new_name.strip() != name:
                if new_name.strip() in presets:
                    return QMessageBox.warning(self, "重名", "预设库里已经有这个名称。")
                target_name = new_name.strip()
                presets[target_name] = presets.pop(name)
                self.save_style_presets(presets)
        elif action == "删除":
            reply = QMessageBox.question(self, "删除预设", f'确定删除预设 "{name}" 吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            presets.pop(name, None)
            self.save_style_presets(presets)
            target_name = ""
        elif action == "用当前字幕覆盖":
            if self.current_selected_idx == -1:
                return QMessageBox.warning(self, "提示", "请先选中一个已经调好样式的字幕。")
            clip = self.state["subs_data"][self.current_selected_idx]
            style_data = copy.deepcopy(clip.get("style", self.default_style))
            style_data[STYLE_PRESET_POSITION_KEY] = {
                "pos_x": float(clip.get("pos_x", self.state.get("default_pos_x", 0.0))),
                "pos_y": float(clip.get("pos_y", self.state.get("default_pos_y", 25.0))),
            }
            presets[name] = style_data
            self.save_style_presets(presets)
        self.refresh_preset_combo()
        self.notify_batch_presets_changed(style_name=target_name or None)
        if target_name:
            idx = self.preset_combo.findData(target_name, Qt.ItemDataRole.UserRole)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)
        self._update_preset_preview()

    def apply_style_preset(self):
        if self.current_selected_idx == -1: return QMessageBox.warning(self, "提示", "请先在时间线上选中要应用的字幕！")
        # 👑 修复：读取隐藏的 userData
        name = self.preset_combo.currentData(Qt.ItemDataRole.UserRole)
        if not name or name == "none": return
        presets = self.load_style_presets()

        if name in presets:
            preset_style, preset_position = split_style_preset(presets[name])
            targets = self._get_target_clips()
            for c in targets:
                if "style" not in c: c["style"] = {}
                c["style"].update(preset_style)
                if preset_position:
                    c["pos_x"] = preset_position["pos_x"]
                    c["pos_y"] = preset_position["pos_y"]

            self.default_style.update(preset_style)
            if preset_position:
                self.state["default_pos_x"] = preset_position["pos_x"]
                self.state["default_pos_y"] = preset_position["pos_y"]
            self.sync_inspector_to_clip()
            self._update_preset_preview()
            self.update_floating_subtitle()
            self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText(f"✅ 预设 '{name}' 已完美应用！")

    def _built_in_layout_presets(self):
        base = {
            "box_layout": "fixed", "box_width": 74.0, "box_height": 0.0, "max_lines": 2,
            "size": 100, "letter_spacing": 0, "word_spacing": 0, "line_height": 1.1, "layout_row_gap": 100,
            "emphasis_scale": 145, "contrast_small_scale": 0.74,
            "text_transform": "uppercase", "text_align": "center",
            "layout_pattern": "auto", "layout_layer_pattern": "auto", "layout_layer_words": "auto",
            "layout_layer_count": 0, "axis_spread": 100, "axis_gap": 100,
        }
        return {
            "\u6807\u51c6\u65e0\u6392\u7248": {
                **base,
                "layout_mode": "standard",
                "layout_variant": "auto",
                "smart_layout_pool": "standard",
                "text_transform": "capitalize",
                "max_lines": 2,
            },
            "\u667a\u80fd\u7cbe\u9009\u6c60": {
                **base,
                "layout_mode": "smart_caption",
                "layout_variant": "auto",
                "smart_layout_pool": "contrast,narrative_block,reel_stack,random_focus,axis_stack",
                "max_lines": 4,
            },
            "\u56db\u5c42\u53d9\u4e8b 14-18": {
                **base,
                "layout_mode": "narrative_block",
                "layout_variant": "auto",
                "smart_layout_pool": "narrative_block",
                "layout_layer_count": 4,
                "layout_layer_pattern": "\u5c0f\u5927\u4e2d\u5927",
                "layout_layer_words": "auto",
                "box_width": 78.0,
                "max_lines": 4,
                "line_height": 0.96,
                "layout_row_gap": 112,
                "emphasis_scale": 170,
                "contrast_small_scale": 0.66,
                "text_align": "left_mix",
            },
            "\u4e24\u5c42\u5927\u5c0f\u94a9\u5b50": {
                **base,
                "layout_mode": "contrast",
                "layout_variant": "small-big-small",
                "smart_layout_pool": "contrast",
                "layout_pattern": "\u5c0f\u5927",
                "box_width": 76.0,
                "max_lines": 2,
                "emphasis_scale": 176,
                "contrast_small_scale": 0.64,
            },
            "\u4e2d\u8f74\u6536\u62e2\u9519\u5f00": {
                **base,
                "layout_mode": "axis_stack",
                "layout_variant": "axis-split-tail",
                "smart_layout_pool": "axis_stack",
                "layout_layer_count": 3,
                "axis_spread": 58,
                "axis_gap": 82,
                "box_width": 72.0,
                "max_lines": 3,
                "emphasis_scale": 162,
                "contrast_small_scale": 0.70,
            },
            "\u5de6\u4e0a\u4e2d\u5927\u53f3\u4e0b": {
                **base,
                "layout_mode": "axis_stack",
                "layout_variant": "axis-diagonal",
                "smart_layout_pool": "axis_stack",
                "layout_layer_count": 3,
                "axis_spread": 92,
                "axis_gap": 96,
                "box_width": 74.0,
                "max_lines": 3,
                "emphasis_scale": 172,
                "contrast_small_scale": 0.62,
            },
            "\u4e2d\u8f74\u968f\u673a\u590d\u7528": {
                **base,
                "layout_mode": "axis_stack",
                "layout_variant": "axis-random",
                "smart_layout_pool": "axis_stack",
                "layout_layer_count": 0,
                "axis_spread": 86,
                "axis_gap": 96,
                "box_width": 74.0,
                "max_lines": 4,
                "emphasis_scale": 166,
                "contrast_small_scale": 0.66,
            },
        }

    def _layout_preset_fields(self):
        return (
            "layout_mode", "layout_variant", "layout_pattern", "smart_layout_pool",
            "layout_layer_count", "layout_layer_pattern", "layout_layer_words",
            "axis_spread", "axis_gap", "layout_row_gap", "box_layout", "box_width", "box_height", "max_lines",
            "size", "letter_spacing", "word_spacing", "line_height", "emphasis_scale", "contrast_small_scale",
            "text_transform", "text_align",
        )

    def load_layout_presets(self):
        presets = self._built_in_layout_presets()
        saved = read_json_file(LAYOUT_PRESETS_FILE, default={})
        if isinstance(saved, dict):
            for name, preset in saved.items():
                if isinstance(preset, dict):
                    presets[name] = preset
        return presets

    def save_layout_presets(self, data):
        try:
            built_ins = set(self._built_in_layout_presets().keys())
            user_data = {k: v for k, v in (data or {}).items() if k not in built_ins}
            write_json_file(LAYOUT_PRESETS_FILE, user_data, indent=2)
        except Exception:
            pass

    def refresh_layout_preset_combo(self, prefer_name=None):
        if not hasattr(self, "layout_preset_combo"):
            return
        self.layout_preset_combo.blockSignals(True)
        self.layout_preset_combo.clear()
        for name in self.load_layout_presets().keys():
            self.layout_preset_combo.addItem(name, userData=name)
        if prefer_name:
            idx = self.layout_preset_combo.findData(prefer_name, Qt.ItemDataRole.UserRole)
            if idx >= 0:
                self.layout_preset_combo.setCurrentIndex(idx)
        self.layout_preset_combo.blockSignals(False)

    def _current_layout_preset_data(self):
        if self.current_selected_idx == -1 or not self.state.get("subs_data"):
            source = self.default_style
        else:
            self._apply_styles_to_targets("params")
            source = self.state["subs_data"][self.current_selected_idx].get("style", self.default_style)
        return {key: copy.deepcopy(source.get(key, self.default_style.get(key))) for key in self._layout_preset_fields() if source.get(key, self.default_style.get(key)) is not None}

    def save_layout_preset(self):
        name, ok = QInputDialog.getText(self, "\u5b58\u6392\u7248\u9884\u8bbe", "\u7ed9\u8fd9\u5957\u6392\u7248\u8d77\u4e2a\u540d\u5b57:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._built_in_layout_presets():
            return QMessageBox.warning(self, "\u63d0\u793a", "\u8fd9\u4e2a\u540d\u5b57\u662f\u5185\u7f6e\u6392\u7248\u9884\u8bbe\uff0c\u8bf7\u6362\u4e00\u4e2a\u540d\u5b57\u4fdd\u5b58\u3002")
        presets = self.load_layout_presets()
        presets[name] = self._current_layout_preset_data()
        self.save_layout_presets(presets)
        self.refresh_layout_preset_combo(name)
        QMessageBox.information(self, "\u4fdd\u5b58\u6210\u529f", f"\u6392\u7248\u9884\u8bbe '{name}' \u5df2\u4fdd\u5b58\u3002")

    def apply_layout_preset(self):
        if self.current_selected_idx == -1:
            return QMessageBox.warning(self, "\u63d0\u793a", "\u8bf7\u5148\u5728\u65f6\u95f4\u7ebf\u4e0a\u9009\u4e2d\u8981\u5e94\u7528\u6392\u7248\u7684\u5b57\u5e55\u3002")
        name = self.layout_preset_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "layout_preset_combo") else None
        if not name:
            return
        preset = self.load_layout_presets().get(name)
        if not isinstance(preset, dict):
            return
        fields = set(self._layout_preset_fields())
        clean_preset = {k: copy.deepcopy(v) for k, v in preset.items() if k in fields}
        for clip in self._get_target_clips():
            clip.setdefault("style", {}).update(clean_preset)
        self.default_style.update(clean_preset)
        self.sync_inspector_to_clip()
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText(f"\u6392\u7248\u9884\u8bbe '{name}' \u5df2\u5e94\u7528")

    def delete_layout_preset(self):
        name = self.layout_preset_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "layout_preset_combo") else None
        if not name:
            return
        if name in self._built_in_layout_presets():
            return QMessageBox.information(self, "\u63d0\u793a", "\u5185\u7f6e\u6392\u7248\u9884\u8bbe\u4e0d\u80fd\u5220\u9664\u3002")
        reply = QMessageBox.question(self, "\u5220\u9664\u6392\u7248\u9884\u8bbe", f'\u786e\u5b9a\u5220\u9664\u6392\u7248\u9884\u8bbe "{name}" \u5417\uff1f', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        presets = self.load_layout_presets()
        presets.pop(name, None)
        self.save_layout_presets(presets)
        self.refresh_layout_preset_combo()

    def _update_preset_preview(self, *args):
        if not hasattr(self, "preset_preview_label"): return
        preview_text = self.font_preview_input.text().strip() if hasattr(self, "font_preview_input") else "Text"
        preview_text = preview_text or "Text"

        # 👑 修复：读取隐藏的 userData 获取真实名字
        name = self.preset_combo.currentData(Qt.ItemDataRole.UserRole)
        presets = self.load_style_presets() if hasattr(self, "load_style_presets") else {}
        st = split_style_preset(presets.get(name))[0] if name and presets.get(name) else None

        if st is None and self.current_selected_idx != -1 and self.current_selected_idx < len(self.state.get("subs_data", [])):
            st = self.state["subs_data"][self.current_selected_idx].get("style", self.default_style)
        st = st or self.default_style

        family = st.get("font", "Segoe UI")
        size = max(18, min(54, int(st.get("size", 100) * 0.35)))
        color = st.get("color_txt", "#FFFFFF")
        bg = st.get("bg_color", "#000000")
        font_weight = st.get("font_weight", "700")
        font_style = st.get("font_style", "normal")

        self.preset_preview_label.setText(preview_text)
        self.preset_preview_label.setStyleSheet(f"background-color:{bg}; border:1px dashed #45475a; border-radius:10px; color:{color}; padding:10px; font-family:'{family}'; font-size:{size}px; font-weight:{font_weight}; font-style:{font_style};")
        if hasattr(self, "preset_preview_web"):
            tokens = [token for token in re.split(r"\s+", preview_text) if token]
            if not tokens:
                tokens = [preview_text]
            words = []
            start = 0.15
            step = 1.6 / max(1, len(tokens))
            for i, token in enumerate(tokens):
                words.append({"text": token, "start": start + i * step, "end": start + (i + 1) * step})
            preview_style = copy.deepcopy(st)
            preview_style.setdefault("box_width", 82.0)
            preview_sub = {
                "text": preview_text,
                "words": words,
                "start": 0.0,
                "end": 2.0,
                "style": preview_style,
                "pos_x": 0.0,
                "pos_y": 25.0,
            }
            rendered = render_subtitle_html(preview_sub, 1.05, self.proj_width, self.proj_height)
            preview_html = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{font_face_css()}
html, body {{
    margin: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: #11111b;
    color: white;
}}
body {{
    display: flex;
    align-items: center;
    justify-content: center;
    background:
        linear-gradient(90deg, rgba(255,255,255,0.055) 1px, transparent 1px),
        linear-gradient(0deg, rgba(255,255,255,0.045) 1px, transparent 1px),
        #11111b;
    background-size: 54px 54px;
}}
#stage {{
    position: relative;
    width: 100vw;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    transform: scale(0.9);
}}
</style>
</head>
<body><div id="stage">{rendered}</div></body>
</html>
"""
            self.preset_preview_web.setHtml(preview_html)


    def toggle_safe_area(self):
        show = 'block' if self.chk_safe_area.isChecked() else 'none'
        self.browser.page().runJavaScript(f"document.getElementById('safe-area').style.display = '{show}';")

    def select_entire_track(self, track_type, track_idx):
        if track_type == "sub":
            for i, s in enumerate(self.state["subs_data"]):
                if s.get("track") == track_idx:
                    self.current_selected_idx = i; self.style_scope_combo.setCurrentIndex(1); self.switch_inspector("sub"); return
            QMessageBox.information(self, "提示", f"该轨道目前没有字幕片段。")
        elif track_type == "design":
            for i, layer in enumerate(self.design_timeline_layers()):
                if int(layer.get("timelineTrack", 6) or 6) == int(track_idx):
                    self.select_design_layer_by_index(i)
                    return
            QMessageBox.information(self, "提示", "该设计轨道目前没有图层。")

    def manual_save(self):
        self.save_to_project(silent=True)
        self.generate_cover_async()
        self.status_lbl.setText("✅ 工程已安全保存，并更新封面！")
        QMessageBox.information(self, "保存成功", "所有轨道排版数据已保存，封面图已在后台更新。")

    def parent_window(self):
        parent = self.parent()
        while parent is not None and not hasattr(parent, "project"):
            parent = parent.parent()
        return parent

    def _current_project_path(self):
        parent = self.parent_window()
        project_data = getattr(parent, "project", None) if parent else None
        if not isinstance(project_data, dict):
            project_data = self.project_data if isinstance(self.project_data, dict) else {}
        return os.path.abspath(project_data.get("project_path", "")) if project_data.get("project_path") else ""

    def _natural_project_sort_key(self, path):
        name = os.path.basename(path).lower()
        return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]

    def _sibling_project_paths(self):
        current_path = self._current_project_path()
        if not current_path:
            return [], -1
        folder = os.path.dirname(current_path)
        if not os.path.isdir(folder):
            return [], -1
        paths = [
            os.path.abspath(os.path.join(folder, name))
            for name in os.listdir(folder)
            if name.lower().endswith(".scomp") and os.path.isfile(os.path.join(folder, name))
        ]
        paths.sort(key=self._natural_project_sort_key)
        normalized_current = os.path.normcase(current_path)
        current_index = next((i for i, path in enumerate(paths) if os.path.normcase(path) == normalized_current), -1)
        return paths, current_index

    def _release_media_before_project_switch(self):
        if hasattr(self, "play_timer"):
            self.play_timer.stop()
        self.is_playing = False
        if hasattr(self, "btn_play"):
            self.btn_play.setText("▶ 播放")
        for player in (getattr(self, "player", None), getattr(self, "audio_player", None), getattr(self, "music_player", None)):
            if player is None:
                continue
            try:
                player.stop()
                player.setSource(QUrl())
            except Exception:
                pass
        self.last_video_image = None
        self._preview_scaled_pixmap_key = None
        self._preview_scaled_pixmap = None
        self._preview_frame_retry_pending = False
        self._preview_frame_retry_count = 0
        self.v_wave_pixmap = None
        self.a_wave_pixmap = None
        self.video_thumbs = []
        if hasattr(self, "video_label"):
            self.video_label.clear()
            self.video_label.setText("切换中...")
        if hasattr(self, "browser"):
            self.browser.page().runJavaScript("if(typeof syncSubs === 'function') syncSubs('[]');")

    def _finish_switch_sibling_project(self, next_path, next_index, total_count):
        try:
            next_project = load_project(next_path)
            self.project_data = next_project
            parent = self.parent_window()
            if parent and hasattr(parent, "project"):
                parent.project = next_project
                if hasattr(parent, "reload_rooms_from_project"):
                    parent.reload_rooms_from_project()
                elif hasattr(parent, "refresh_room_links"):
                    parent.refresh_room_links()
            else:
                self.load_project_on_boot()
            self.status_lbl.setText(f"已切换到 {next_index + 1}/{total_count}：{os.path.basename(next_path)}")
        except Exception as e:
            QMessageBox.warning(self, "切换失败", f"无法切换工程：\n{next_path}\n\n原因：{e}")
        finally:
            self._switching_sibling_project = False
            for btn_name in ("btn_prev_project", "btn_next_project"):
                btn = getattr(self, btn_name, None)
                if btn is not None:
                    btn.setEnabled(True)

    def switch_sibling_project(self, direction):
        if getattr(self, "_switching_sibling_project", False):
            return
        paths, current_index = self._sibling_project_paths()
        if not paths:
            QMessageBox.information(self, "没有可切换工程", "当前工程文件夹里没有找到 .scomp 工程文件。")
            return
        if current_index < 0:
            QMessageBox.information(self, "无法定位当前工程", "当前工程不在它记录的项目文件夹里，暂时不能切换上/下个视频。")
            return
        if len(paths) <= 1:
            self.status_lbl.setText("当前文件夹只有 1 个工程，暂无上/下个视频可切换。")
            return

        next_index = (current_index + int(direction)) % len(paths)
        next_path = paths[next_index]
        try:
            self._switching_sibling_project = True
            for btn_name in ("btn_prev_project", "btn_next_project"):
                btn = getattr(self, btn_name, None)
                if btn is not None:
                    btn.setEnabled(False)
            current_project = self.save_to_project(silent=True)
            if current_project.get("project_path"):
                save_project(current_project["project_path"], current_project)
            self._release_media_before_project_switch()
            self.status_lbl.setText("正在切换视频，释放旧预览...")
            QTimer.singleShot(120, lambda: self._finish_switch_sibling_project(next_path, next_index, len(paths)))
        except Exception as e:
            self._switching_sibling_project = False
            for btn_name in ("btn_prev_project", "btn_next_project"):
                btn = getattr(self, btn_name, None)
                if btn is not None:
                    btn.setEnabled(True)
            QMessageBox.warning(self, "切换失败", f"无法切换工程：\n{next_path}\n\n原因：{e}")

    def is_cloud_project_active(self):
        parent = self.parent_window()
        room_project = getattr(parent, "room_project", None) if parent else None
        return bool(room_project and hasattr(room_project, "is_cloud_workspace") and room_project.is_cloud_workspace())

    def cloud_import_media_if_needed(self, file_path):
        if not file_path or not self.is_cloud_project_active():
            return file_path

        parent = self.parent_window()
        project_data = getattr(parent, "project", None) or self.project_data
        try:
            cloud_path, copied, _ = copy_media_to_project_assets(project_data, file_path)
            if copied:
                self.status_lbl.setText(f"☁ 素材已复制到云端工程 assets：{os.path.basename(cloud_path)}")
            return cloud_path
        except Exception as e:
            QMessageBox.warning(
                self,
                "素材云端化失败",
                f"素材仍会按原路径导入，但其他成员可能无法打开：\n{file_path}\n\n原因：{e}",
            )
            return file_path

    def save_to_project(self, silent=False):
        self.auto_save_cache()
        parent = self.parent_window()
        project_data = getattr(parent, "project", None) or self.project_data or {"project_type": "edit_room"}
        project_tag = str(self.state.get("project_tag", "") or "").strip()
        if project_tag:
            project_data["project_tag"] = project_tag
            project_data["tags"] = [project_tag]
        project_data = update_room_state(project_data, "edit_room", self.state)
        if self.is_cloud_project_active():
            project_data, report = sync_project_assets_to_project_dir(project_data)
            edit_state = project_data.get("room_state", {}).get("edit_room", {})
            if edit_state:
                self.state.update(edit_state)
            if report.get("copied") and not silent:
                self.status_lbl.setText(f"☁ 已自动上传素材到云端工程：{len(report['copied'])} 个")
            if report.get("missing") and not silent:
                QMessageBox.warning(
                    self,
                    "有素材未找到",
                    "以下素材无法复制到云端工程，其他成员可能无法打开：\n" + "\n".join(report["missing"][:6])
                )
        self.project_data = project_data
        if parent and hasattr(parent, "project"):
            parent.project = project_data
        return project_data

    def generate_cover_async(self):
        def task():
            try:
                v_clips = self.state.get("video_clips", [])
                if not v_clips: return
                v_path = v_clips[0]["path"]
                if not os.path.exists(v_path): return

                p_dir = self.project_data.get("project_dir", "")
                p_name = self.project_data.get("project_name", "untitled")
                if not p_dir: return

                cover_filename = f"{p_name}_cover.jpg"
                cover_path = os.path.join(p_dir, cover_filename)

                cmd = [get_ffmpeg_cmd(), "-y", "-ss", "00:00:01", "-i", v_path, "-vframes", "1", "-q:v", "2", cover_path]
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

                self.project_data["cover_img"] = cover_filename
                from project_io import save_project
                save_project(self.project_data["project_path"], self.project_data)
            except Exception as e:
                pass

        threading.Thread(target=task, daemon=True).start()

    def reset_project(self):
        reply = QMessageBox.warning(self, '⚠️ 清空确认', '确定要清空所有轨道数据吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.player.stop(); self.audio_player.stop()
            if hasattr(self, "music_player"):
                self.music_player.stop()
            self.state["video_clips"] = []; self.state["audio_path"] = ""; self.state["music_path"] = ""; self.state["subs_data"] = []; self.state["duration"] = 10.0
            self.state.pop("audio_source_in", None); self.state.pop("music_dur", None); self.state.pop("music_match_duration", None); self.state.pop("music_loop", None)
            self.state["signature"] = default_signature_config(self.default_style)
            self.current_selected_idx = -1; self.current_v_idx = 0; self.current_play_time = 0.0
            self.v_wave_pixmap = None; self.a_wave_pixmap = None; self.video_thumbs = []; self.last_video_image = None
            self.preview_zoom = 1.0; self.preview_pan_x = 0.0; self.preview_pan_y = 0.0
            self.active_subs_cache.clear(); self.last_render_hash = None
            self.browser.page().runJavaScript("if(typeof syncSubs === 'function') syncSubs('[]');")
            self._sync_preview_overlay_transform()
            self.btn_v.setText("➕ 导入第一段画面 (MP4)"); self.btn_a.setText("🎵 导入独立配音 (可选)")
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            self.text_editor.clear(); self.sync_signature_controls(); self.render_ui_list(); self.switch_inspector("empty")
            self.update_timeline_size(); self.update_floating_subtitle()
            if os.path.exists(CACHE_FILE): os.remove(CACHE_FILE)
            self.push_history()
            self.status_lbl.setText("✅ 工程已清空")

    def delete_current_clip(self):
        if not self._ensure_edit_mode("删除字幕"):
            return
        if self.current_selected_idx != -1:
            del self.state["subs_data"][self.current_selected_idx]; self.current_selected_idx = -1; self.switch_inspector("empty"); self.render_ui_list()
            self.update_timeline_size(); self.update_floating_subtitle(); self.auto_save_cache()
            self.push_history()
            self.status_lbl.setText("🗑️ 字幕已删除")

    def add_manual_text(self):
        if not self._ensure_edit_mode("新建字幕"):
            return
        t = self.current_play_time
        new_sub = {"start": t, "end": t + 3.0, "text": "新建文本", "track": 0, "words": [{"text": "新建文本", "start": t, "end": t + 3.0}]}
        new_sub = self.sanitize_subs_data([new_sub])[0]
        self.state["subs_data"].append(new_sub); self.state["subs_data"] = sorted(self.state["subs_data"], key=lambda x: x['start']); self.state["duration"] = max(self.state["duration"], t + 3.0)
        self.current_selected_idx = self.state["subs_data"].index(new_sub); self.switch_inspector("sub"); self.render_ui_list(); self.update_timeline_size(); self.sync_player_to_time(t); self.auto_save_cache()
        self.push_history()

    def _format_monitor_time(self, seconds):
        seconds = max(0.0, float(seconds or 0.0))
        minutes = int(seconds // 60)
        secs = seconds - minutes * 60
        return f"{minutes:02d}:{secs:04.1f}"

    def _update_time_label(self):
        duration = self._preview_playback_duration()
        if hasattr(self, "lbl_time"):
            self.lbl_time.setText(
                f"{self._format_monitor_time(self.current_play_time)} / {self._format_monitor_time(duration)}"
            )
        if hasattr(self, "preview_seek_slider") and not self.preview_seek_slider.isSliderDown():
            value = int(max(0.0, min(1.0, self.current_play_time / duration)) * self.preview_seek_slider.maximum())
            self.preview_seek_slider.blockSignals(True)
            self.preview_seek_slider.setValue(value)
            self.preview_seek_slider.blockSignals(False)

    def _preview_slider_time(self, value):
        duration = self._preview_playback_duration()
        if duration <= 0:
            return 0.0
        maximum = max(1, self.preview_seek_slider.maximum() if hasattr(self, "preview_seek_slider") else 10000)
        return max(0.0, min(duration, duration * float(value) / float(maximum)))

    def _on_preview_seek_slider_moved(self, value):
        self.current_play_time = self._preview_slider_time(value)
        self._update_time_label()
        self.timeline_widget.update_playhead(self.current_play_time)
        self.update_floating_subtitle()

    def _on_preview_seek_slider_released(self):
        if not hasattr(self, "preview_seek_slider"):
            return
        self.sync_player_to_time(self._preview_slider_time(self.preview_seek_slider.value()))

    def _selected_status_text(self):
        if self.selected_track == "sub" and 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])):
            clip = self.state["subs_data"][self.current_selected_idx]
            start = float(clip.get("start", 0.0) or 0.0)
            end = float(clip.get("end", start) or start)
            text = str(clip.get("text", "") or "").replace("\n", " ").strip()
            if len(text) > 52:
                text = text[:49] + "..."
            return f"字幕 T{3 - int(clip.get('track', 1))} · {self._format_monitor_time(start)} - {self._format_monitor_time(end)} · {text or '空文本'}"
        if self.selected_track == "video" and 0 <= self.current_v_idx < len(self.state.get("video_clips", [])):
            clip = self.state["video_clips"][self.current_v_idx]
            name = os.path.basename(clip.get("path", "")) or "视频片段"
            return f"视频 V1 · {name} · {self._format_monitor_time(clip.get('start', 0))} - {self._format_monitor_time(clip.get('end', 0))}"
        if self.selected_track == "audio" and self.state.get("audio_path"):
            name = os.path.basename(self.state.get("audio_path", "")) or "配音"
            a_trim = self.state.get("a_trim", [0.0, 0.0])
            a_start = a_trim[0] if len(a_trim) > 0 else 0.0
            a_end = a_trim[1] if len(a_trim) > 1 else a_start
            return f"音频 A2 · {name} · {self._format_monitor_time(a_start)} - {self._format_monitor_time(a_end)}"
        if self.selected_track == "music" and self.state.get("music_path"):
            name = os.path.basename(self.state.get("music_path", "")) or "配乐"
            end = float(self.state.get("music_match_duration", 0.0) or self.state.get("music_dur", 0.0) or self.state.get("duration", 0.0) or 0.0)
            return f"配乐 M1 · {name} · {self._format_monitor_time(0.0)} - {self._format_monitor_time(end)}"
        if self.selected_track == "design" and self.selected_design_layer_id:
            layer = self._selected_design_layer()
            if layer:
                start = float(layer.get("start", 0.0) or 0.0)
                end = float(layer.get("end", 0.0) or 0.0)
                if end <= start:
                    end = float(self._design_page().get("duration", self.state.get("duration", 5.0)) or 5.0)
                return f"设计 D{int(layer.get('timelineTrack', 6) or 6) - 5} · {layer.get('name', '图层')} · {self._format_monitor_time(start)} - {self._format_monitor_time(end)}"
        return "未选中片段"

    def _update_workspace_status(self):
        if not hasattr(self, "lbl_workspace_stats"):
            return
        clips = self.state.get("video_clips", []) or []
        subs = self.state.get("subs_data", []) or []
        audio_on = bool(self.state.get("audio_path"))
        design_layers = self.design_timeline_layers() if hasattr(self, "design_timeline_layers") else []
        duration = float(self.state.get("duration", 0.0) or 0.0)
        sig = normalize_signature_config(self.state.get("signature"), self.default_style)
        sig_text = "署名 ON" if sig.get("enabled") and str(sig.get("text", "")).strip() else "署名 OFF"
        self.lbl_workspace_stats.setText(
            f"{len(clips)} 视频 · {len(subs)} 字幕 · {len(design_layers)} 设计 · {'配音 ON' if audio_on else '配音 OFF'} · {self._format_monitor_time(duration)}"
        )
        self.lbl_monitor_meta.setText(f"{self.proj_width}x{self.proj_height} · {self.zoom_factor:.0f} px/s · VIEW {self.preview_zoom * 100:.0f}% · {sig_text}")
        if hasattr(self, "lbl_timeline_summary"):
            self.lbl_timeline_summary.setText(
                f"{len(clips)}V · {len(subs)}T · {len(design_layers)}D · {'A2' if audio_on else 'no A2'} · {self._format_monitor_time(duration)} · {self.zoom_factor:.0f}px/s"
            )
        if hasattr(self, "btn_timeline_snap"):
            self.btn_timeline_snap.blockSignals(True)
            self.btn_timeline_snap.setChecked(bool(self.timeline_snap_enabled))
            self.btn_timeline_snap.setText("SNAP ON" if self.timeline_snap_enabled else "SNAP OFF")
            self.btn_timeline_snap.blockSignals(False)
        if hasattr(self, "lbl_zoom"):
            self.lbl_zoom.setText(f"{self.zoom_factor:.0f} px/s")
        if hasattr(self, "lbl_preview_zoom"):
            self.lbl_preview_zoom.setText(f"{self.preview_zoom * 100:.0f}%")
        if hasattr(self, "lbl_selected_status"):
            self.lbl_selected_status.setText(self._selected_status_text())
        if hasattr(self, "lbl_edit_health"):
            self.lbl_edit_health.setText(f"{self._format_monitor_time(self.current_play_time)} / {self._format_monitor_time(duration)}")

    def seek_relative(self, delta):
        target = max(0.0, min(self._preview_playback_duration(), self.current_play_time + float(delta)))
        self.sync_player_to_time(target)

    def _normalize_shortcut_sequence(self, sequence_text):
        default = "Ctrl+F"
        text = str(sequence_text or "").strip() or default
        sequence = QKeySequence(text)
        normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText).strip()
        return normalized or default

    def apply_preview_fullscreen_shortcut(self, sequence_text=None):
        value = sequence_text if sequence_text is not None else get_preview_fullscreen_shortcut()
        shortcut_text = self._normalize_shortcut_sequence(value)
        self.preview_fullscreen_shortcut_text = shortcut_text
        sequence = QKeySequence(shortcut_text)
        if hasattr(self, "shortcut_preview_fullscreen"):
            self.shortcut_preview_fullscreen.setKey(sequence)
            self.shortcut_preview_fullscreen.setContext(Qt.ShortcutContext.ApplicationShortcut)
        if hasattr(self, "btn_preview_fullscreen"):
            self.btn_preview_fullscreen.setToolTip(f"全屏观看预览 {shortcut_text} / Esc 退出")
        dlg = getattr(self, "preview_fullscreen_dialog", None)
        if dlg is not None:
            dlg.setWindowTitle(f"预览全屏 - Esc / {shortcut_text} 退出")
            exit_shortcut = getattr(dlg, "_preview_exit_shortcut", None)
            if exit_shortcut is not None:
                exit_shortcut.setKey(sequence)
        return shortcut_text

    def _shortcut_editing_guard(self):
        focused = QApplication.focusWidget()
        if focused is not None and focused.__class__.__name__ == "QKeySequenceEdit":
            return False
        return not isinstance(focused, (QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox))

    def _shortcut_compare_text(self, sequence_text):
        return str(sequence_text or "").replace("Control", "Ctrl").replace(" ", "").lower()

    def _key_value(self, key):
        return int(key.value) if hasattr(key, "value") else int(key)

    def _event_shortcut_text(self, event):
        key = int(event.key())
        ignored = {
            self._key_value(Qt.Key.Key_Control),
            self._key_value(Qt.Key.Key_Shift),
            self._key_value(Qt.Key.Key_Alt),
            self._key_value(Qt.Key.Key_Meta),
        }
        if key in ignored:
            return ""
        parts = []
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("Meta")
        key_text = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText).strip()
        if not key_text:
            if self._key_value(Qt.Key.Key_A) <= key <= self._key_value(Qt.Key.Key_Z):
                key_text = chr(key)
            elif self._key_value(Qt.Key.Key_0) <= key <= self._key_value(Qt.Key.Key_9):
                key_text = chr(key)
        if not key_text:
            return ""
        parts.append(key_text)
        return "+".join(parts)

    def _event_matches_preview_fullscreen_shortcut(self, event):
        target = self._shortcut_compare_text(self._normalize_shortcut_sequence(self.preview_fullscreen_shortcut_text))
        pressed = self._shortcut_compare_text(self._event_shortcut_text(event))
        return bool(target and pressed and target == pressed)

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Type.KeyPress and self.isVisible():
                if getattr(self, "_preview_fullscreen_active", False) and event.key() == self._key_value(Qt.Key.Key_Escape):
                    self.exit_preview_fullscreen()
                    return True
                if self._event_matches_preview_fullscreen_shortcut(event) and self._shortcut_editing_guard():
                    self.toggle_preview_fullscreen()
                    return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def toggle_play_from_shortcut(self):
        if self._shortcut_editing_guard():
            self.toggle_play()

    def seek_relative_from_shortcut(self, delta):
        if self._shortcut_editing_guard():
            self.seek_relative(delta)

    def adjust_preview_zoom_from_shortcut(self, direction):
        if self._shortcut_editing_guard():
            self.adjust_preview_zoom(direction)

    def reset_preview_view_from_shortcut(self):
        if self._shortcut_editing_guard():
            self.reset_preview_view()

    def toggle_preview_fullscreen_from_shortcut(self):
        if self.isVisible() and self._shortcut_editing_guard():
            self.toggle_preview_fullscreen()

    def toggle_preview_fullscreen(self):
        if getattr(self, "_preview_fullscreen_active", False):
            self.exit_preview_fullscreen()
        else:
            self.enter_preview_fullscreen()

    def _force_preview_web_transparency(self):
        if not hasattr(self, "browser"):
            return
        try:
            self.browser.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            opaque_attr = getattr(Qt.WidgetAttribute, "WA_OpaquePaintEvent", None)
            if opaque_attr is not None:
                self.browser.setAttribute(opaque_attr, False)
            self.browser.setAutoFillBackground(False)
            self.browser.setStyleSheet("background: transparent; border: none;")
            page = self.browser.page()
            if page is not None:
                page.setBackgroundColor(QColor(0, 0, 0, 0))
                page.runJavaScript("""
                    (() => {
                        document.documentElement.style.setProperty('background', 'transparent', 'important');
                        if (document.body) {
                            document.body.style.setProperty('background', 'transparent', 'important');
                        }
                        const wrapper = document.getElementById('scale-wrapper');
                        if (wrapper) {
                            wrapper.style.setProperty('background', 'transparent', 'important');
                        }
                    })();
                """)
        except Exception:
            pass

    def _set_preview_overlay_visible(self, visible):
        wants_overlay = bool(visible)
        visible = wants_overlay and bool(getattr(self, "preview_overlay_enabled", True))
        previous_wants_overlay = bool(getattr(self, "_preview_overlay_has_content", False))
        self._preview_overlay_has_content = wants_overlay
        try:
            if hasattr(self, "video_label"):
                self.video_label.show()
            if not hasattr(self, "browser"):
                return
            browser_visible = self.browser.isVisible()
            if visible:
                if not browser_visible or not previous_wants_overlay:
                    self._force_preview_web_transparency()
                    self.browser.show()
                    self.browser.raise_()
            else:
                if browser_visible:
                    self.browser.hide()
                if hasattr(self, "video_label") and browser_visible:
                    self.video_label.raise_()
        except Exception:
            pass

    def _request_preview_video_refresh(self):
        try:
            last_image = getattr(self, "last_video_image", None)
            if (last_image is None or last_image.isNull()) and self.state.get("video_clips"):
                self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
        except Exception:
            pass
        self.redraw_video_preview()

    def _sync_preview_web_state(self):
        if not hasattr(self, "browser"):
            return
        self._force_preview_web_transparency()
        self.last_render_hash = None
        self.active_subs_cache = set()
        try:
            self.browser.page().runJavaScript(
                f"if(typeof setResolution === 'function') setResolution({self.proj_width}, {self.proj_height});"
                "if(typeof setEditMode === 'function') setEditMode(true);"
            )
            self._sync_preview_overlay_transform(sync_js=True, update_status=True)
            self.update_floating_subtitle()
        except Exception:
            pass

    def _on_preview_web_reloaded_after_reparent(self, *_):
        if hasattr(self, "browser"):
            try:
                self.browser.loadFinished.disconnect(self._on_preview_web_reloaded_after_reparent)
            except Exception:
                pass
            self._force_preview_web_transparency()
            self._set_preview_overlay_visible(getattr(self, "_preview_overlay_has_content", False))
        QTimer.singleShot(0, self._sync_preview_web_state)
        QTimer.singleShot(120, self._sync_preview_web_state)

    def _refresh_preview_surface_after_reparent(self, reload_web=False):
        if hasattr(self, "preview_workspace"):
            self.preview_workspace.update_stage_geometry()
        if hasattr(self, "video_label"):
            self.video_label.show()
        if hasattr(self, "browser"):
            self._force_preview_web_transparency()
            self._set_preview_overlay_visible(getattr(self, "_preview_overlay_has_content", False))
        self._request_preview_video_refresh()
        if reload_web and hasattr(self, "browser"):
            try:
                self.browser.loadFinished.disconnect(self._on_preview_web_reloaded_after_reparent)
            except Exception:
                pass
            self.browser.loadFinished.connect(self._on_preview_web_reloaded_after_reparent)
            self.init_web_engine_once()
        else:
            self._sync_preview_web_state()
        QTimer.singleShot(0, self._force_preview_web_transparency)
        QTimer.singleShot(80, self._force_preview_web_transparency)
        QTimer.singleShot(80, self._request_preview_video_refresh)

    def _legacy_enter_preview_fullscreen_reparent(self):
        return
        if not hasattr(self, "preview_workspace") or getattr(self, "preview_fullscreen_dialog", None):
            return
        origin_parent = self.preview_workspace.parentWidget()
        origin_layout = origin_parent.layout() if origin_parent is not None else None
        origin_index = origin_layout.indexOf(self.preview_workspace) if origin_layout is not None else -1
        self._preview_fullscreen_origin = {
            "parent": origin_parent,
            "layout": origin_layout,
            "index": origin_index,
        }
        if origin_layout is not None:
            origin_layout.removeWidget(self.preview_workspace)

        shortcut_text = self.apply_preview_fullscreen_shortcut()
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"预览全屏 - Esc / {shortcut_text} 退出")
        dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dlg.setStyleSheet("background-color:#000000;")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.preview_workspace)
        esc_shortcut = QShortcut(QKeySequence("Esc"), dlg)
        esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        esc_shortcut.activated.connect(self.exit_preview_fullscreen)
        exit_shortcut = QShortcut(QKeySequence(shortcut_text), dlg)
        exit_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        exit_shortcut.activated.connect(self.exit_preview_fullscreen)
        dlg._preview_exit_shortcut = exit_shortcut
        dlg._preview_shortcuts = (esc_shortcut, exit_shortcut)
        dlg.finished.connect(lambda *_: self.exit_preview_fullscreen())
        self.preview_fullscreen_dialog = dlg
        if hasattr(self, "btn_preview_fullscreen"):
            self.btn_preview_fullscreen.setText("退出")
        dlg.showFullScreen()
        QTimer.singleShot(0, self._refresh_preview_surface_after_reparent)
        QTimer.singleShot(220, self._refresh_preview_surface_after_reparent)
        QTimer.singleShot(420, lambda: self.sync_player_to_time(self.current_play_time))

    def _legacy_exit_preview_fullscreen_reparent(self):
        return
        dlg = getattr(self, "preview_fullscreen_dialog", None)
        origin = getattr(self, "_preview_fullscreen_origin", {}) or {}
        if not dlg and not origin:
            return
        if getattr(self, "_restoring_preview_fullscreen", False):
            return
        self._restoring_preview_fullscreen = True
        try:
            if dlg is not None:
                dlg.layout().removeWidget(self.preview_workspace)
            origin_parent = origin.get("parent")
            origin_layout = origin.get("layout")
            origin_index = int(origin.get("index", -1))
            if origin_parent is not None:
                self.preview_workspace.setParent(origin_parent)
            if origin_layout is not None:
                if origin_index >= 0 and hasattr(origin_layout, "insertWidget"):
                    origin_layout.insertWidget(origin_index, self.preview_workspace, 1)
                else:
                    origin_layout.addWidget(self.preview_workspace)
            self.preview_workspace.show()
            self.preview_fullscreen_dialog = None
            self._preview_fullscreen_origin = {}
            if hasattr(self, "btn_preview_fullscreen"):
                self.btn_preview_fullscreen.setText("全屏")
            if dlg is not None and dlg.isVisible():
                dlg.close()
            QTimer.singleShot(0, self._refresh_preview_surface_after_reparent)
            QTimer.singleShot(220, self._refresh_preview_surface_after_reparent)
            QTimer.singleShot(420, lambda: self.sync_player_to_time(self.current_play_time))
        finally:
            self._restoring_preview_fullscreen = False

    def _preview_fullscreen_widget_state(self, window):
        widgets = {
            "app_topbar": getattr(window, "topbar", None) if window is not None else None,
            "top_app_bar": getattr(self, "top_app_bar", None),
            "monitor_bar": getattr(self, "monitor_bar", None),
            "edit_status_strip": getattr(self, "edit_status_strip", None),
            "left_shell": getattr(self, "left_shell", None),
            "left_content_panel": getattr(self, "left_content_panel", None),
            "right_panel": getattr(self, "right_panel", None),
            "timeline_outer": getattr(self, "timeline_outer", None),
            "btn_left_float_toggle": getattr(self, "btn_left_float_toggle", None),
            "btn_right_float_toggle": getattr(self, "btn_right_float_toggle", None),
            "canvas_context_toolbar": getattr(self, "canvas_context_toolbar", None),
        }
        return {
            name: (widget, widget.isVisible() if widget is not None else False)
            for name, widget in widgets.items()
        }

    def _set_preview_fullscreen_button(self, active):
        shortcut_text = self.apply_preview_fullscreen_shortcut()
        if hasattr(self, "btn_preview_fullscreen"):
            self.btn_preview_fullscreen.setText("退出" if active else "全屏")
            tip = f"再次按 {shortcut_text} 或 Esc 退出" if active else f"全屏观看预览 {shortcut_text} / Esc 退出"
            self.btn_preview_fullscreen.setToolTip(tip)
        if hasattr(self, "shortcut_preview_fullscreen_escape"):
            self.shortcut_preview_fullscreen_escape.setEnabled(bool(active))
        if hasattr(self, "shortcut_preview_fullscreen"):
            self.shortcut_preview_fullscreen.setEnabled(True)

    def _schedule_preview_fullscreen_refresh(self):
        for delay in (0, 80, 220):
            QTimer.singleShot(delay, self.refresh_preview_layout)
        QTimer.singleShot(320, lambda: self.sync_player_to_time(self.current_play_time))

    def enter_preview_fullscreen(self):
        if getattr(self, "_preview_fullscreen_active", False):
            return
        window = self.parent_window() or self.window()
        self._preview_fullscreen_restore = {
            "window": window,
            "window_was_fullscreen": bool(window.isFullScreen()) if window is not None else False,
            "window_was_maximized": bool(window.isMaximized()) if window is not None else False,
            "top_splitter_sizes": self.top_h_splitter.sizes() if hasattr(self, "top_h_splitter") else [],
            "main_splitter_sizes": self.main_v_splitter.sizes() if hasattr(self, "main_v_splitter") else [],
            "left_shell_min_width": self.left_shell.minimumWidth() if hasattr(self, "left_shell") else None,
            "widgets": self._preview_fullscreen_widget_state(window),
        }
        self._preview_fullscreen_active = True
        self._set_preview_fullscreen_button(True)

        for name in ("app_topbar", "top_app_bar", "monitor_bar", "edit_status_strip",
                     "btn_left_float_toggle", "btn_right_float_toggle", "canvas_context_toolbar"):
            widget, _ = self._preview_fullscreen_restore["widgets"].get(name, (None, False))
            if widget is not None:
                widget.setVisible(False)
        if hasattr(self, "left_shell"):
            self.left_shell.setMinimumWidth(0)
            self.left_shell.setVisible(False)
        if hasattr(self, "right_panel"):
            self.right_panel.setVisible(False)
        if hasattr(self, "timeline_outer"):
            self.timeline_outer.setVisible(False)
        if hasattr(self, "top_h_splitter"):
            total = max(900, sum(self.top_h_splitter.sizes()) or self.width())
            self.top_h_splitter.setSizes([0, total, 0])
        if hasattr(self, "main_v_splitter"):
            total = max(620, sum(self.main_v_splitter.sizes()) or self.height())
            self.main_v_splitter.setSizes([total, 0])
        if window is not None and not window.isFullScreen():
            window.showFullScreen()
        self._schedule_preview_fullscreen_refresh()

    def exit_preview_fullscreen(self):
        if not getattr(self, "_preview_fullscreen_active", False):
            return
        if getattr(self, "_restoring_preview_fullscreen", False):
            return
        self._restoring_preview_fullscreen = True
        restore = getattr(self, "_preview_fullscreen_restore", {}) or {}
        try:
            self._preview_fullscreen_active = False
            widgets = restore.get("widgets", {})
            for name, (widget, was_visible) in widgets.items():
                if widget is not None:
                    widget.setVisible(bool(was_visible))
            if hasattr(self, "left_shell"):
                min_width = restore.get("left_shell_min_width")
                self.left_shell.setMinimumWidth(68 if min_width is None else int(min_width))
            if hasattr(self, "top_h_splitter") and restore.get("top_splitter_sizes"):
                self.top_h_splitter.setSizes(restore.get("top_splitter_sizes"))
            if hasattr(self, "main_v_splitter") and restore.get("main_splitter_sizes"):
                self.main_v_splitter.setSizes(restore.get("main_splitter_sizes"))
            window = restore.get("window")
            if window is not None and not restore.get("window_was_fullscreen") and window.isFullScreen():
                if restore.get("window_was_maximized"):
                    window.showMaximized()
                else:
                    window.showNormal()
            self._set_preview_fullscreen_button(False)
            self._sync_panel_toggle_buttons()
            self._position_floating_panel_toggles()
            self._schedule_preview_fullscreen_refresh()
        finally:
            self._preview_fullscreen_restore = {}
            self._restoring_preview_fullscreen = False

    def adjust_timeline_zoom(self, factor):
        self.zoom_factor = max(10.0, min(300.0, self.zoom_factor * float(factor)))
        self.update_timeline_size()

    def set_timeline_snap_enabled(self, enabled):
        self.timeline_snap_enabled = bool(enabled)
        self.update_timeline_size()

    def _clamp_preview_pan(self):
        if hasattr(self, "preview_workspace"):
            w = max(1, self.preview_workspace.width())
            h = max(1, self.preview_workspace.height())
            stage_w, stage_h = self.preview_workspace.stage_size_for_zoom(self.preview_zoom)
            keep_x = min(140.0, stage_w * 0.35)
            keep_y = min(140.0, stage_h * 0.35)
            max_x = max(0.0, (w + stage_w) * 0.5 - keep_x)
            max_y = max(0.0, (h + stage_h) * 0.5 - keep_y)
        elif hasattr(self, "video_label"):
            w = max(1, self.video_label.width())
            h = max(1, self.video_label.height())
            if self.preview_zoom <= 1.001:
                self.preview_pan_x = 0.0
                self.preview_pan_y = 0.0
                return
            max_x = (self.preview_zoom - 1.0) * w * 0.5
            max_y = (self.preview_zoom - 1.0) * h * 0.5
        else:
            return
        self.preview_pan_x = max(-max_x, min(max_x, self.preview_pan_x))
        self.preview_pan_y = max(-max_y, min(max_y, self.preview_pan_y))

    def _sync_preview_overlay_transform(self, sync_js=True, update_status=True):
        if not hasattr(self, "browser"):
            return
        self._clamp_preview_pan()
        if hasattr(self, "preview_workspace"):
            self.preview_workspace.set_view_transform(self.preview_zoom, self.preview_pan_x, self.preview_pan_y)
        if sync_js:
            js = (
                "if(typeof setMonitorView === 'function') "
                f"setMonitorView({self.preview_zoom:.4f}, {self.preview_pan_x:.3f}, {self.preview_pan_y:.3f});"
            )
            self.browser.page().runJavaScript(js)
        if update_status:
            self._update_workspace_status()

    def adjust_preview_zoom_from_stage(self, direction, stage_x=0.0, stage_y=0.0):
        if hasattr(self, "preview_workspace") and hasattr(self, "browser"):
            stage_pos = self.preview_workspace.child_widget.pos()
            browser_pos = self.browser.pos()
            self.adjust_preview_zoom(
                direction,
                stage_pos.x() + browser_pos.x() + float(stage_x or 0.0),
                stage_pos.y() + browser_pos.y() + float(stage_y or 0.0),
            )
        else:
            self.adjust_preview_zoom(direction, stage_x, stage_y)

    def adjust_preview_zoom(self, direction, anchor_x=None, anchor_y=None):
        old_zoom = max(0.25, float(self.preview_zoom or 1.0))
        factor = 1.18 if float(direction) > 0 else 1.0 / 1.18
        new_zoom = max(0.25, min(8.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 0.001:
            return
        if hasattr(self, "preview_workspace"):
            cx = self.preview_workspace.width() * 0.5
            cy = self.preview_workspace.height() * 0.5
            ax = float(anchor_x) if anchor_x is not None else cx
            ay = float(anchor_y) if anchor_y is not None else cy
            self.preview_pan_x = (self.preview_pan_x - (ax - cx)) * (new_zoom / old_zoom) + (ax - cx)
            self.preview_pan_y = (self.preview_pan_y - (ay - cy)) * (new_zoom / old_zoom) + (ay - cy)
        elif hasattr(self, "video_label"):
            cx = self.video_label.width() * 0.5
            cy = self.video_label.height() * 0.5
            ax = float(anchor_x or cx)
            ay = float(anchor_y or cy)
            self.preview_pan_x = (self.preview_pan_x - (ax - cx)) * (new_zoom / old_zoom) + (ax - cx)
            self.preview_pan_y = (self.preview_pan_y - (ay - cy)) * (new_zoom / old_zoom) + (ay - cy)
        self.preview_zoom = new_zoom
        self._clamp_preview_pan()
        self._sync_preview_overlay_transform()
        self.redraw_video_preview()

    def pan_preview_view(self, dx, dy):
        self.preview_pan_x += float(dx or 0.0)
        self.preview_pan_y += float(dy or 0.0)
        self._clamp_preview_pan()
        self._sync_preview_overlay_transform(sync_js=False, update_status=False)

    def finalize_preview_pan(self):
        self._sync_preview_overlay_transform(sync_js=True, update_status=True)

    def reset_preview_view(self):
        self.preview_zoom = 1.0
        self.preview_pan_x = 0.0
        self.preview_pan_y = 0.0
        self._sync_preview_overlay_transform()
        self.redraw_video_preview()

    def _apply_preview_transform_to_pixmap(self, base_pixmap):
        if not base_pixmap or base_pixmap.isNull():
            return base_pixmap
        self._clamp_preview_pan()
        if self.preview_zoom <= 1.001 and abs(self.preview_pan_x) < 0.5 and abs(self.preview_pan_y) < 0.5:
            return base_pixmap
        w = base_pixmap.width()
        h = base_pixmap.height()
        result_pix = QPixmap(w, h)
        result_pix.fill(Qt.GlobalColor.black)
        painter = QPainter(result_pix)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(w * 0.5 + self.preview_pan_x, h * 0.5 + self.preview_pan_y)
        painter.scale(self.preview_zoom, self.preview_zoom)
        painter.drawPixmap(int(-w * 0.5), int(-h * 0.5), base_pixmap)
        painter.end()
        return result_pix

    def update_timeline_size(self):
        self.timeline_widget.sync_from_controller()
        self._update_workspace_status()

    def switch_inspector(self, track_type):
        self.selected_track = track_type
        if track_type in ("video", "audio", "sub", "design"):
            self.show_canvas_context_toolbar("subtitle" if track_type == "sub" else track_type)
            if hasattr(self, "right_panel_title"):
                self.right_panel_title.setText({"video": "视频编辑", "audio": "音频选项", "sub": "字幕选项", "design": "设计图层"}.get(track_type, "参数"))
            if hasattr(self, "right_panel") and track_type != "design":
                self.set_right_sidebar_visible(True)
        if track_type == "video": self.insp_stack.setCurrentIndex(2); self.sync_inspector_to_video()
        elif track_type == "audio": self.insp_stack.setCurrentIndex(3)
        elif track_type == "sub" and self.current_selected_idx != -1: self.insp_stack.setCurrentIndex(1); self.sync_inspector_to_clip()
        elif track_type == "design": self.sync_design_panel_controls()
        else:
            self.insp_stack.setCurrentIndex(0); self.current_selected_idx = -1
            self.hide_canvas_context_toolbar()
        if track_type != "design":
            self.tabs.setCurrentIndex(1)
        self.timeline_widget.sync_from_controller(); self._update_workspace_status()

    def sync_inspector_to_video(self):
        if not self.state.get("video_clips") or self.current_v_idx < 0 or self.current_v_idx >= len(self.state["video_clips"]): return
        clip = self.state["video_clips"][self.current_v_idx]
        self.v_start_spin.blockSignals(True); self.v_end_spin.blockSignals(True)
        self.v_start_spin.setValue(float(clip.get("start", 0))); self.v_end_spin.setValue(float(clip.get("end", 5)))
        self.v_start_spin.blockSignals(False); self.v_end_spin.blockSignals(False)

    def _on_v_time_change(self):
        if not self.state.get("video_clips") or self.current_v_idx < 0 or self.current_v_idx >= len(self.state["video_clips"]): return
        self.state["video_clips"][self.current_v_idx]["start"] = self.v_start_spin.value()
        self.state["video_clips"][self.current_v_idx]["end"] = self.v_end_spin.value()
        self.update_timeline_size(); self.auto_save_cache()

    def sync_time_from_list(self, idx, new_start, new_end):
        clip = self.state["subs_data"][idx]
        old_start = float(clip.get("start", 0))
        old_end = float(clip.get("end", 1))
        old_dur = max(0.001, old_end - old_start)

        n_start = new_start if new_start is not None else old_start
        n_end = new_end if new_end is not None else old_end

        if n_end <= n_start: return

        new_dur = max(0.001, n_end - n_start)

        words = clip.get("words", [])
        for w in words:
            rel_s = (float(w.get("start", 0)) - old_start) / old_dur
            rel_e = (float(w.get("end", 1)) - old_start) / old_dur
            w["start"] = n_start + rel_s * new_dur
            w["end"] = n_start + rel_e * new_dur

        clip["start"] = n_start
        clip["end"] = n_end

        self.update_timeline_size()
        self.auto_save_cache()
        if new_start is not None: self.sync_player_to_time(n_start)

        if getattr(self, 'current_selected_idx', -1) == idx and getattr(self, 'selected_track', '') == 'sub':
            self.sub_start_spin.blockSignals(True)
            self.sub_end_spin.blockSignals(True)
            self.sub_start_spin.setValue(n_start)
            self.sub_end_spin.setValue(n_end)
            self.sub_start_spin.blockSignals(False)
            self.sub_end_spin.blockSignals(False)

    def _on_sub_time_change(self):
        if self.current_selected_idx == -1 or not self.state["subs_data"]: return
        idx = self.current_selected_idx
        clip = self.state["subs_data"][idx]

        old_start = float(clip.get("start", 0))
        old_end = float(clip.get("end", 1))
        old_dur = max(0.001, old_end - old_start)

        new_start = self.sub_start_spin.value()
        new_end = self.sub_end_spin.value()
        if new_end <= new_start: return
        new_dur = max(0.001, new_end - new_start)

        words = clip.get("words", [])
        for w in words:
            rel_s = (float(w.get("start", 0)) - old_start) / old_dur
            rel_e = (float(w.get("end", 1)) - old_start) / old_dur
            w["start"] = new_start + rel_s * new_dur
            w["end"] = new_start + rel_e * new_dur

        clip["start"] = new_start
        clip["end"] = new_end

        if hasattr(self, 'ui_entries') and 0 <= idx < len(self.ui_entries):
            if "start_spin" in self.ui_entries[idx]:
                self.ui_entries[idx]["start_spin"].blockSignals(True)
                self.ui_entries[idx]["start_spin"].setValue(new_start)
                self.ui_entries[idx]["start_spin"].blockSignals(False)
            if "end_spin" in self.ui_entries[idx]:
                self.ui_entries[idx]["end_spin"].blockSignals(True)
                self.ui_entries[idx]["end_spin"].setValue(new_end)
                self.ui_entries[idx]["end_spin"].blockSignals(False)

        self.update_timeline_size()
        self.auto_save_cache()

    def sync_text_edit(self, idx, text):
        self.state["subs_data"][idx]["text"] = text
        clean_text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\u2029', '\n')
        words_split = []
        for i, line in enumerate(clean_text.split('\n')):
            parts = line.split()
            if not parts:
                if i > 0 and words_split: words_split[-1] += '\n'
                continue
            if i > 0: parts[0] = '\n' + parts[0]
            words_split.extend(parts)

        num_words = len(words_split)
        if num_words > 0:
            st = float(self.state["subs_data"][idx]["start"]); en = float(self.state["subs_data"][idx]["end"]); step = (en - st) / num_words
            self.state["subs_data"][idx]["words"] = [{"text": w, "start": st + i * step, "end": st + (i + 1) * step} for i, w in enumerate(words_split)]
        if float(self.state["subs_data"][idx]["start"]) <= self.current_play_time <= float(self.state["subs_data"][idx]["end"]): self.update_floating_subtitle()
        self.auto_save_cache()

    def _switch_sub_page(self, idx):
        if not hasattr(self, "sub_pages"):
            return
        self.sub_pages.setCurrentIndex(idx)
        for i, btn in enumerate(getattr(self, "sub_page_buttons", [])):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)

    def _on_safe_font_filter_changed(self, *args):
        self.safe_font_only = bool(self.chk_safe_fonts.isChecked()) if hasattr(self, "chk_safe_fonts") else False
        self._apply_font_license_filter()
        self._update_font_preview()

    def _selected_font_family(self):
        if not hasattr(self, "font_var"):
            return self.default_style.get("font", "Noto Sans SC")
        text = self.font_var.currentText().strip()
        if text:
            return text
        return self.font_var.currentFont().family()

    def _on_font_change(self, font):
        family = ""
        try:
            family = font.family().strip()
        except Exception:
            family = ""
        family = self._usable_font_name(family or self._selected_font_family())
        if not family:
            return

        if self.state.get("subs_data"):
            if self.current_selected_idx == -1:
                target_clips = self.state["subs_data"]
            else:
                current_clip = self.state["subs_data"][self.current_selected_idx]
                scope = self.style_scope_combo.currentIndex()
                if scope == 0:
                    target_clips = self.state["subs_data"]
                elif scope == 1:
                    target_clips = [c for c in self.state["subs_data"] if c.get("track") == current_clip.get("track")]
                else:
                    target_clips = [current_clip]

            for clip in target_clips:
                clip.setdefault("style", {})["font"] = family

        self.default_style["font"] = family
        self._apply_font_license_filter(font)
        self._update_font_preview()
        self.update_floating_subtitle()
        self.auto_save_cache()

    def _font_is_blocked_for_design(self, font_name):
        return False

    def _usable_font_name(self, font_name):
        name = str(font_name or "").strip()
        if name:
            return name
        return self._preferred_safe_font()

    def _apply_font_license_filter(self, *args):
        if not hasattr(self, "font_var"):
            return
        if getattr(self, "_font_filtering", False):
            return
        self._font_filtering = True
        view = self.font_var.view()
        visible = 0
        signal_font = args[0] if args and isinstance(args[0], QFont) else None
        current_name = signal_font.family().strip() if signal_font and signal_font.family().strip() else self._selected_font_family()
        current_name_key = current_name.casefold()
        current_record = font_record_for(current_name)
        category_text = self.font_category_combo.currentText() if hasattr(self, "font_category_combo") else ""

        def category_allowed(record):
            status = record.get("status")
            style_class = record.get("style_class", "")
            if "开源打包" in category_text:
                return status == STATUS_OPEN
            if "个人" in category_text or "不可商用" in category_text:
                return status in (STATUS_NONCOMMERCIAL, STATUS_REVIEW, STATUS_SYSTEM, STATUS_APPROVED)
            if "系统" in category_text or "待复核" in category_text:
                return status in (STATUS_SYSTEM, STATUS_REVIEW)
            if "无衬线" in category_text:
                return style_class == "sans"
            if "衬线" in category_text and "无衬线" not in category_text:
                return style_class == "serif"
            if "手写" in category_text or "花体" in category_text:
                return style_class == "script_handwriting"
            if "装饰" in category_text or "标题" in category_text:
                return style_class in ("display", "decorative_seasonal")
            return True

        for row in range(self.font_var.count()):
            name = self.font_var.itemText(row).strip()
            record = font_record_for(name)
            allowed = category_allowed(record)
            if name.casefold() == current_name_key:
                allowed = True
            try:
                view.setRowHidden(row, not allowed)
            except Exception:
                pass
            if allowed:
                visible += 1

        if hasattr(self, "lbl_font_license"):
            status = current_record.get("status")
            if status == STATUS_OPEN:
                label = "开源/已确认，可随打包字体清单使用"
                color = "#a6e3a1"
            elif status in (STATUS_NONCOMMERCIAL, STATUS_APPROVED):
                label = "仅个人使用/不可商用，不随安装包或模板分发"
                color = "#f38ba8"
            elif status == STATUS_SYSTEM:
                label = "系统字体/需确认系统授权，其他人需本机自备"
                color = "#f9e2af"
            else:
                label = "未登记字体，按个人预览处理；商用前需补授权凭据"
                color = "#f9e2af"
            source = current_record.get("source") or ""
            license_name = current_record.get("license") or ""
            proof = current_record.get("proof") or current_record.get("source_url") or current_record.get("license_evidence_url") or current_record.get("license_file") or ""
            detail = f"字体: {current_name}；状态: {label}；可见 {visible} 个"
            if source:
                detail += f"；来源: {source}"
            if license_name:
                detail += f"；协议: {license_name}"
            self.lbl_font_license.setText(detail)
            self.lbl_font_license.setStyleSheet(f"color: {color}; font-size: 12px;")
            proof = current_record.get("proof") or current_record.get("source_url") or current_record.get("license_evidence_url") or ""
            if not proof:
                proof = current_record.get("license_file") or current_record.get("bundled_file") or current_record.get("notes") or ""
            self.lbl_font_license.setToolTip(proof)
        self._font_filtering = False

    def _preferred_safe_font(self):
        preferred = ["Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Noto Sans", "Inter", "Roboto", "Open Sans"]
        installed = {self.font_var.itemText(i).strip().casefold(): self.font_var.itemText(i).strip() for i in range(self.font_var.count())} if hasattr(self, "font_var") else {}
        safe_keys = safe_font_keys()
        for name in preferred:
            match = installed.get(name.casefold())
            if match and match.casefold() in safe_keys:
                return match
        for key, name in installed.items():
            if key in safe_keys:
                return name
        return "Noto Sans SC"

    def apply_open_font_to_targets(self):
        if self.current_selected_idx == -1:
            return QMessageBox.warning(self, "提示", "请先选中一个字幕片段，再按当前样式作用范围替换字体。")
        font_name = self._preferred_safe_font()
        targets = self._get_target_clips()
        for c in targets:
            if "style" not in c:
                c["style"] = {}
            c["style"]["font"] = font_name
        self.default_style["font"] = font_name
        try:
            self.font_var.setCurrentFont(QFont(font_name))
        except Exception:
            pass
        self._apply_font_license_filter()
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText(f"✅ 已把当前作用范围替换为开源字体: {font_name}")

    def _set_font_filter(self, text):
        if not hasattr(self, "font_var"):
            return
        try:
            writing_system = QFontDatabase.WritingSystem.Any
            font_filter = QFontComboBox.FontFilter.AllFonts
            if "中文" in text:
                writing_system = QFontDatabase.WritingSystem.SimplifiedChinese
            elif "拉丁" in text:
                writing_system = QFontDatabase.WritingSystem.Latin
            elif "等宽" in text:
                font_filter = QFontComboBox.FontFilter.MonospacedFonts

            self.font_var.setWritingSystem(writing_system)
            self.font_var.setFontFilters(font_filter)
        except Exception as e:
            try:
                self.font_var.setFontFilters(QFontComboBox.FontFilter.AllFonts)
            except Exception:
                pass
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText(f"字体分类切换失败，已退回全部字体: {e}")
        self._apply_font_license_filter()

    def _update_font_preview(self, *args):
        if not hasattr(self, "font_preview_label"):
            return
        preview_text = "Text"
        if hasattr(self, "font_preview_input"):
            preview_text = self.font_preview_input.text().strip() or "Text"
        font_family = self._selected_font_family() if hasattr(self, "font_var") else "Segoe UI"
        font_size = self.size_spin.value() if hasattr(self, "size_spin") else 72
        line_height_pct = self.lineh_spin.value() if hasattr(self, "lineh_spin") else 110
        letter_spacing = self.spacing_spin.value() if hasattr(self, "spacing_spin") else 0
        word_spacing = self.word_spacing_spin.value() if hasattr(self, "word_spacing_spin") else 0
        font_weight = self.font_weight_combo.currentData() if hasattr(self, "font_weight_combo") else "700"
        font_style = "italic" if hasattr(self, "chk_font_italic") and self.chk_font_italic.isChecked() else "normal"
        self.font_preview_label.setText(preview_text)
        preview_font = QFont(font_family)
        preview_font.setPointSize(max(10, min(int(font_size * 0.72), 54)))
        try:
            preview_font.setWeight(QFont.Weight(int(font_weight)))
        except Exception:
            preview_font.setWeight(QFont.Weight.Bold)
        preview_font.setItalic(font_style == "italic")
        self.font_preview_label.setFont(preview_font)
        self.font_preview_label.setStyleSheet(
            f"background-color: #11111b; border: 1px dashed #45475a; border-radius: 8px; color: #ffffff;"
            f"padding: 12px; letter-spacing: {letter_spacing}px; word-spacing: {word_spacing}px; line-height: {max(90, min(line_height_pct, 180))}%;"
        )

    def sync_inspector_to_clip(self):
        if self.current_selected_idx == -1 or not self.state["subs_data"]: return
        clip = self.state["subs_data"][self.current_selected_idx]
        st = clip.get("style", clip)

        controls = [self.sub_start_spin, self.sub_end_spin, self.pos_x_spin, self.pos_x_slider, self.pos_y_spin, self.pos_y_slider, self.size_slider, self.size_spin, self.box_width_slider, self.box_width_spin, self.box_height_slider, self.box_height_spin, self.max_lines_slider, self.max_lines_spin, self.alpha_slider, self.alpha_spin, self.radius_slider, self.radius_spin, self.padding_slider, self.padding_spin, self.bg_pad_left_slider, self.bg_pad_left_spin, self.bg_pad_right_slider, self.bg_pad_right_spin, self.bg_pad_top_slider, self.bg_pad_top_spin, self.bg_pad_bottom_slider, self.bg_pad_bottom_spin, self.hl_alpha_slider, self.hl_alpha_spin, self.hl_radius_slider, self.hl_radius_spin, self.hl_padding_slider, self.hl_padding_spin, self.hl_pad_left_slider, self.hl_pad_left_spin, self.hl_pad_right_slider, self.hl_pad_right_spin, self.hl_pad_top_slider, self.hl_pad_top_spin, self.hl_pad_bottom_slider, self.spacing_slider, self.spacing_spin, self.word_spacing_slider, self.word_spacing_spin, self.lineh_slider, self.lineh_spin, self.layout_row_gap_slider, self.layout_row_gap_spin, self.stroke_slider, self.stroke_spin, self.stroke_o_slider, self.stroke_o_spin, self.stroke_soft_slider, self.stroke_soft_spin, self.rot_slider, self.rot_spin, self.glow_size_slider, self.glow_size_spin, self.sh_x_slider, self.sh_x_spin, self.sh_y_slider, self.sh_y_spin, self.sh_blur_slider, self.sh_blur_spin, self.sh_a_slider, self.sh_a_spin, self.pop_speed_slider, self.pop_speed_spin, self.pop_bounce_slider, self.pop_bounce_spin, self.inactive_alpha_slider, self.inactive_alpha_spin, self.mask_top_slider, self.mask_top_spin, self.mask_bot_slider, self.mask_bot_spin, self.merge_bridge_width_slider, self.merge_bridge_width_spin, self.merge_bridge_height_slider, self.merge_bridge_height_spin, self.merge_bridge_alpha_slider, self.merge_bridge_alpha_spin, self.hl_skew_slider, self.hl_skew_spin, self.hl_trail_words_slider, self.hl_trail_words_spin, self.hl_trail_alpha_slider, self.hl_trail_alpha_spin, self.global_glow_size_slider, self.global_glow_size_spin, self.global_glow_blur_slider, self.global_glow_blur_spin, self.global_glow_alpha_slider, self.global_glow_alpha_spin, self.global_glow_x_slider, self.global_glow_x_spin, self.global_glow_y_slider, self.global_glow_y_spin, self.global_glow_z_slider, self.global_glow_z_spin, self.chk_text_3d, self.text_3d_depth_slider, self.text_3d_depth_spin, self.text_3d_x_slider, self.text_3d_x_spin, self.text_3d_y_slider, self.text_3d_y_spin, self.chk_bg_auto_resolution, self.chk_bg_enabled, self.chk_global_glow, self.global_glow_mode_combo, self.global_glow_motion_combo, self.transform_combo, self.align_combo, self.anim_combo, self.font_motion_combo, self.hl_motion_combo, self.text_texture_combo, self.bg_mode_combo, self.layout_mode_combo, self.layout_variant_combo, self.box_layout_combo, self.axis_mode_combo, self.layout_pattern_input, self.layout_layer_pattern_input, self.layout_layer_words_input, self.emphasis_slider, self.emphasis_spin, self.contrast_small_slider, self.contrast_small_spin, self.layout_layer_count_slider, self.layout_layer_count_spin, self.axis_spread_slider, self.axis_spread_spin, self.axis_gap_slider, self.axis_gap_spin]
        controls.append(self.hl_style_combo)
        controls.extend(getattr(self, "smart_layout_checks", {}).values())
        if hasattr(self, "font_var"):
            controls.append(self.font_var)
        for c in controls: c.blockSignals(True)

        self.sub_start_spin.setValue(float(clip.get("start", 0)))
        self.sub_end_spin.setValue(float(clip.get("end", 1)))

        vx = float(clip.get("pos_x", 0.0))
        vy = float(clip.get("pos_y", 25.0))
        self.pos_x_spin.setValue(vx); self.pos_x_slider.setValue(int(vx * 100))
        self.pos_y_spin.setValue(vy); self.pos_y_slider.setValue(int(vy * 100))

        self.size_spin.setValue(int(st.get("size", 100))); self.size_slider.setValue(int(st.get("size", 100)))
        if hasattr(self, "font_weight_combo"):
            weight = str(st.get("font_weight", "700"))
            idx = self.font_weight_combo.findData(weight)
            self.font_weight_combo.blockSignals(True)
            self.font_weight_combo.setCurrentIndex(idx if idx >= 0 else 3)
            self.font_weight_combo.blockSignals(False)
        if hasattr(self, "chk_font_italic"):
            self.chk_font_italic.blockSignals(True)
            self.chk_font_italic.setChecked(str(st.get("font_style", "normal")).lower() == "italic")
            self.chk_font_italic.blockSignals(False)
        self.box_width_spin.setValue(float(st.get("box_width", 0))); self.box_width_slider.setValue(int(st.get("box_width", 0) * 100))
        self.box_height_spin.setValue(float(st.get("box_height", 0))); self.box_height_slider.setValue(int(st.get("box_height", 0) * 100))
        self.max_lines_spin.setValue(int(st.get("max_lines", 2))); self.max_lines_slider.setValue(int(st.get("max_lines", 2)))
        self.spacing_spin.setValue(int(st.get("letter_spacing", 0))); self.spacing_slider.setValue(int(st.get("letter_spacing", 0)))
        self.lineh_spin.setValue(int(st.get("line_height", 1.1)*100)); self.lineh_slider.setValue(int(st.get("line_height", 1.1)*100))
        self.layout_row_gap_spin.setValue(int(st.get("layout_row_gap", 100))); self.layout_row_gap_slider.setValue(int(st.get("layout_row_gap", 100)))
        self.word_spacing_spin.setValue(int(st.get("word_spacing", 0))); self.word_spacing_slider.setValue(int(st.get("word_spacing", 0)))
        self.emphasis_spin.setValue(int(st.get("emphasis_scale", 145))); self.emphasis_slider.setValue(int(st.get("emphasis_scale", 145)))
        small_pct = int(float(st.get("contrast_small_scale", 0.74) or 0.74) * 100)
        self.contrast_small_spin.setValue(small_pct); self.contrast_small_slider.setValue(small_pct)
        self.layout_layer_count_spin.setValue(int(st.get("layout_layer_count", 0))); self.layout_layer_count_slider.setValue(int(st.get("layout_layer_count", 0)))
        self.axis_spread_spin.setValue(int(st.get("axis_spread", 100))); self.axis_spread_slider.setValue(int(st.get("axis_spread", 100)))
        self.axis_gap_spin.setValue(int(st.get("axis_gap", 100))); self.axis_gap_slider.setValue(int(st.get("axis_gap", 100)))
        self.layout_pattern_input.setText(str(st.get("layout_pattern", "auto") or "auto"))
        self.layout_layer_pattern_input.setText(str(st.get("layout_layer_pattern", "auto") or "auto"))
        self.layout_layer_words_input.setText(str(st.get("layout_layer_words", "auto") or "auto"))
        self.stroke_spin.setValue(int(st.get("stroke_width", 4))); self.stroke_slider.setValue(int(st.get("stroke_width", 4)))
        self.stroke_o_spin.setValue(int(st.get("stroke_o_width", 0))); self.stroke_o_slider.setValue(int(st.get("stroke_o_width", 0)))
        self.stroke_soft_spin.setValue(int(st.get("stroke_softness", 0))); self.stroke_soft_slider.setValue(int(st.get("stroke_softness", 0)))
        self.hl_skew_spin.setValue(int(st.get("hl_bg_skew", 0))); self.hl_skew_slider.setValue(int(st.get("hl_bg_skew", 0)))
        self.hl_trail_words_spin.setValue(int(st.get("hl_trail_words", 1))); self.hl_trail_words_slider.setValue(int(st.get("hl_trail_words", 1)))
        self.hl_trail_alpha_spin.setValue(int(st.get("hl_trail_min_alpha", 35))); self.hl_trail_alpha_slider.setValue(int(st.get("hl_trail_min_alpha", 35)))
        self.rot_spin.setValue(int(st.get("rotation", 0))); self.rot_slider.setValue(int(st.get("rotation", 0)))
        self.glow_size_spin.setValue(int(st.get("glow_size", 20))); self.glow_size_slider.setValue(int(st.get("glow_size", 20)))
        self.global_glow_size_spin.setValue(int(st.get("global_glow_size", 18))); self.global_glow_size_slider.setValue(int(st.get("global_glow_size", 18)))
        self.global_glow_blur_spin.setValue(int(st.get("global_glow_blur", 24))); self.global_glow_blur_slider.setValue(int(st.get("global_glow_blur", 24)))
        self.global_glow_alpha_spin.setValue(int(st.get("global_glow_alpha", 35))); self.global_glow_alpha_slider.setValue(int(st.get("global_glow_alpha", 35)))
        self.global_glow_x_spin.setValue(int(st.get("global_glow_x", 0))); self.global_glow_x_slider.setValue(int(st.get("global_glow_x", 0)))
        self.global_glow_y_spin.setValue(int(st.get("global_glow_y", 0))); self.global_glow_y_slider.setValue(int(st.get("global_glow_y", 0)))
        self.global_glow_z_spin.setValue(int(st.get("global_glow_z", 0))); self.global_glow_z_slider.setValue(int(st.get("global_glow_z", 0)))
        self.chk_text_3d.setChecked(bool(st.get("text_3d_enable", False)))
        self.text_3d_depth_spin.setValue(int(st.get("text_3d_depth", 0))); self.text_3d_depth_slider.setValue(int(st.get("text_3d_depth", 0)))
        self.text_3d_x_spin.setValue(int(st.get("text_3d_x", 2))); self.text_3d_x_slider.setValue(int(st.get("text_3d_x", 2)))
        self.text_3d_y_spin.setValue(int(st.get("text_3d_y", 3))); self.text_3d_y_slider.setValue(int(st.get("text_3d_y", 3)))
        glow_mode_map = {"soft": "发光模式: 柔光", "neon": "发光模式: 霓虹强光", "sweep": "发光模式: 扫光光线"}
        glow_motion_map = {"stable": "光线动画: 静态", "breath": "光线动画: 呼吸", "sweep": "光线动画: 扫光"}
        self.global_glow_mode_combo.setCurrentText(glow_mode_map.get(st.get("global_glow_mode", "soft"), glow_mode_map["soft"]))
        self.global_glow_motion_combo.setCurrentText(glow_motion_map.get(st.get("global_glow_motion", "stable"), glow_motion_map["stable"]))

        self.sh_x_spin.setValue(int(st.get("shadow_x", 5))); self.sh_x_slider.setValue(int(st.get("shadow_x", 5)))
        self.sh_y_spin.setValue(int(st.get("shadow_y", 5))); self.sh_y_slider.setValue(int(st.get("shadow_y", 5)))
        self.sh_blur_spin.setValue(int(st.get("shadow_blur", 0))); self.sh_blur_slider.setValue(int(st.get("shadow_blur", 0)))
        self.sh_a_spin.setValue(int(st.get("shadow_alpha", 100))); self.sh_a_slider.setValue(int(st.get("shadow_alpha", 100)))

        self.pop_speed_spin.setValue(float(st.get("pop_speed", 0.18))); self.pop_speed_slider.setValue(int(st.get("pop_speed", 0.18)*100))
        self.pop_bounce_spin.setValue(int(st.get("pop_bounce", 128))); self.pop_bounce_slider.setValue(int(st.get("pop_bounce", 128)))
        self.inactive_alpha_spin.setValue(int(st.get("inactive_alpha", 100))); self.inactive_alpha_slider.setValue(int(st.get("inactive_alpha", 100)))

        self.alpha_spin.setValue(int(st.get("bg_alpha", 80))); self.alpha_slider.setValue(int(st.get("bg_alpha", 80)))
        self.radius_spin.setValue(int(st.get("bg_radius", 15))); self.radius_slider.setValue(int(st.get("bg_radius", 15)))
        self.padding_spin.setValue(int(st.get("bg_padding", 20))); self.padding_slider.setValue(int(st.get("bg_padding", 20)))
        self.bg_pad_left_spin.setValue(int(st.get("bg_pad_left", st.get("bg_padding", 20)))); self.bg_pad_left_slider.setValue(int(st.get("bg_pad_left", st.get("bg_padding", 20))))
        self.bg_pad_right_spin.setValue(int(st.get("bg_pad_right", st.get("bg_padding", 20)))); self.bg_pad_right_slider.setValue(int(st.get("bg_pad_right", st.get("bg_padding", 20))))
        self.bg_pad_top_spin.setValue(int(st.get("bg_pad_top", st.get("bg_padding", 20) / 2.5))); self.bg_pad_top_slider.setValue(int(st.get("bg_pad_top", st.get("bg_padding", 20) / 2.5)))
        self.bg_pad_bottom_spin.setValue(int(st.get("bg_pad_bottom", st.get("bg_padding", 20) / 2.5))); self.bg_pad_bottom_slider.setValue(int(st.get("bg_pad_bottom", st.get("bg_padding", 20) / 2.5)))
        self.chk_bg_auto_resolution.setChecked(bool(st.get("bg_auto_resolution", True)))

        self.hl_alpha_spin.setValue(int(st.get("hl_bg_alpha", 100))); self.hl_alpha_slider.setValue(int(st.get("hl_bg_alpha", 100)))
        self.hl_radius_spin.setValue(int(st.get("hl_bg_radius", 8))); self.hl_radius_slider.setValue(int(st.get("hl_bg_radius", 8)))
        self.hl_padding_spin.setValue(int(st.get("hl_bg_padding", 8))); self.hl_padding_slider.setValue(int(st.get("hl_bg_padding", 8)))
        self.hl_pad_left_spin.setValue(int(st.get("hl_pad_left", st.get("hl_bg_padding", 8)))); self.hl_pad_left_slider.setValue(int(st.get("hl_pad_left", st.get("hl_bg_padding", 8))))
        self.hl_pad_right_spin.setValue(int(st.get("hl_pad_right", st.get("hl_bg_padding", 8)))); self.hl_pad_right_slider.setValue(int(st.get("hl_pad_right", st.get("hl_bg_padding", 8))))
        self.hl_pad_top_spin.setValue(int(st.get("hl_pad_top", max(0, st.get("hl_bg_padding", 8) / 3)))); self.hl_pad_top_slider.setValue(int(st.get("hl_pad_top", max(0, st.get("hl_bg_padding", 8) / 3))))
        self.hl_pad_bottom_spin.setValue(int(st.get("hl_pad_bottom", max(0, st.get("hl_bg_padding", 8) / 3)))); self.hl_pad_bottom_slider.setValue(int(st.get("hl_pad_bottom", max(0, st.get("hl_bg_padding", 8) / 3))))

        self.mask_top_spin.setValue(int(st.get("mask_top", 20))); self.mask_top_slider.setValue(int(st.get("mask_top", 20)))
        self.mask_bot_spin.setValue(int(st.get("mask_bottom", 20))); self.mask_bot_slider.setValue(int(st.get("mask_bottom", 20)))
        self.merge_bridge_width_spin.setValue(int(st.get("merge_bridge_width", 160))); self.merge_bridge_width_slider.setValue(int(st.get("merge_bridge_width", 160)))
        self.merge_bridge_height_spin.setValue(int(st.get("merge_bridge_height", 16))); self.merge_bridge_height_slider.setValue(int(st.get("merge_bridge_height", 16)))
        self.merge_bridge_alpha_spin.setValue(int(st.get("merge_bridge_alpha", 100))); self.merge_bridge_alpha_slider.setValue(int(st.get("merge_bridge_alpha", 100)))

        check_widgets = [self.chk_use_hl, self.chk_hl_glow, self.chk_mask_en, self.chk_merge_bridge, self.chk_bg_enabled, self.chk_global_glow, self.chk_text_3d]
        for w in check_widgets:
            w.blockSignals(True)
        self.chk_use_hl.setChecked(bool(st.get("use_hl", True)) and st.get("hl_style", "text") != "none")
        self.chk_hl_glow.setChecked(st.get("hl_glow", False))
        self.chk_mask_en.setChecked(st.get("mask_en", False))
        self.chk_merge_bridge.setChecked(st.get("merge_bridge_enable", False))
        self.chk_bg_enabled.setChecked(st.get("bg_mode", "none") != "none")
        self.chk_global_glow.setChecked(st.get("global_glow_enable", False))
        for w in check_widgets:
            w.blockSignals(False)

        t_map = {"uppercase": "全部大写 (UPPERCASE)", "lowercase": "全部小写 (lowercase)", "capitalize": "首字母大写 (Capitalize)", "none": "正常 (Normal)"}
        self.transform_combo.setCurrentText(t_map.get(st.get("text_transform", "capitalize")))
        a_map = {"center": "居中对齐 (Center)", "center_left": "居中左对齐 (Center Left)", "left": "左对齐 (Left)", "free_mix": "自由混合对齐 (Free Mix)", "left_mix": "左对齐为主混合 (Left Mix)", "right": "右对齐 (Right)", "justify": "两端对齐 (Justify)"}
        self.align_combo.setCurrentText(a_map.get(st.get("text_align", "center")))
        lm_map = {"standard": "标准模式（不启用排版）", "smart_caption": "智能模式（从勾选池自动匹配）", "mixed_reel": "智能模式（从勾选池自动匹配）", "contrast": "手动模式：大小对比排版", "narrative_block": "手动模式：多层叙事排版", "triple": "手动模式：三层模板排版", "reel_stack": "手动模式：首尾大小叙事", "random_focus": "手动模式：随机重点排版", "side_steps": "手动模式：左右错开排版", "axis_stack": "手动模式：中轴对比排版"}
        self.layout_mode_combo.setCurrentText(lm_map.get(st.get("layout_mode", "standard"), "标准模式（不启用排版）"))
        pool = {item.strip() for item in str(st.get("smart_layout_pool", "contrast,narrative_block,reel_stack,random_focus,axis_stack") or "").split(",") if item.strip()}
        for key, chk in getattr(self, "smart_layout_checks", {}).items():
            chk.setChecked(key in pool)
        lv_map = {"auto": "\u81ea\u52a8\u53d8\u5316", "small-big-small": "\u5c0f-\u5927-\u5c0f", "big-small-mix": "\u5927-\u5c0f-\u6df7\u6392", "mix-big-small": "\u6df7\u6392-\u5927-\u5c0f", "head-letter-large": "\u9996\u5b57\u6bcd\u53d8\u5927\u53d9\u4e8b", "head-large": "\u5f00\u5934\u53d8\u5927\u53d9\u4e8b", "head-uppercase": "\u5f00\u5934\u53d8\u5927\u53d9\u4e8b", "tail-large": "\u5c3e\u90e8\u53d8\u5927\u53d9\u4e8b", "tail-uppercase": "\u5c3e\u90e8\u53d8\u5927\u53d9\u4e8b", "axis-split-tail": "\u4e2d\u8f74\u7ed3\u5c3e\u5206\u4e24\u8fb9", "axis-123": "\u4e2d\u8f74 1-2-3 \u6392", "axis-diagonal": "\u5de6\u4e0a\u5c0f-\u4e2d\u95f4\u5927-\u53f3\u4e0b\u5c0f", "axis-random": "\u4e2d\u8f74\u968f\u673a\u53d8\u5316"}
        self.layout_variant_combo.setCurrentText(lv_map.get(st.get("layout_variant", "auto"), "自动变化"))
        axis_map = {"axis-split-tail": "\u4e2d\u8f74\u6a21\u5f0f: \u4e2d\u95f4\u4e3a\u4e3b\u672b\u5c3e\u5206\u4e24\u8fb9", "axis-diagonal": "\u4e2d\u8f74\u6a21\u5f0f: \u5de6\u4e0a\u5c0f-\u4e2d\u95f4\u5927-\u53f3\u4e0b\u5c0f", "axis-random": "\u4e2d\u8f74\u6a21\u5f0f: \u968f\u673a\u53d8\u5316", "axis-123": "\u4e2d\u8f74\u6a21\u5f0f: \u5e38\u89c4\u9519\u5f00", "auto": "\u4e2d\u8f74\u6a21\u5f0f: \u5e38\u89c4\u9519\u5f00"}
        self.axis_mode_combo.setCurrentText(axis_map.get(st.get("layout_variant", "auto"), axis_map["auto"]))
        self.box_layout_combo.setCurrentText("固定窗口自动换行" if st.get("box_layout", "auto") == "fixed" else "自适应文字宽度")
        anim_type_value = st.get("anim_type", "pop")
        font_motion_value = st.get("font_motion", "none")
        if anim_type_value == "typewriter":
            anim_type_value = "none"
            if font_motion_value in ("none", "", None):
                font_motion_value = "typewriter_left"
        anim_map = {"pop": "🎉 逐字弹跳 (Pop-in)", "fade": "☁️ 柔和淡入 (Fade)", "blur_fade": "🌫️ 单词模糊渐入 (Blur Fade)", "word_wipe": "▌单词遮罩右移键入", "wipe_right": "➡️ 平滑遮罩右移", "roll_up": "⬆️ 电影级向上滚动 (Roll Up)", "slam_in": "💥 远处砸入 (Slam In)", "grow_in": "🔎 慢慢放大出字 (Grow In)", "scatter_in": "🧲 词语散开入场 (Scatter In)", "letter_scatter_in": "🔤 字字分散入场 (Letter Scatter)", "camera_push": "🎥 朝镜头推进 (Camera Push)", "depth_push": "🧊 3D远近推进 (Depth Push)", "holy_breath": "🕊️ 圣息慢显 (Holy Breath)", "none": "🚫 无动画 (None)"}
        self.anim_combo.setCurrentText(anim_map.get(anim_type_value, anim_map["pop"]))
        font_motion_map = {"none": "字体动画: 无效果", "typewriter_left": "字体动画: 打字机左移", "wave": "字体动画: 波浪感", "ripple3d": "字体动画: 水波立体流动", "breathe": "字体动画: 慢呼吸放大", "drift": "字体动画: 词语慢慢分散", "pulse": "字体动画: 忽大忽小跳动"}
        self.font_motion_combo.setCurrentText(font_motion_map.get(font_motion_value, font_motion_map["none"]))
        texture_map = {"none": "字体质感: 无", "gold_metal": "字体质感: Gold 金色金属", "grain": "字体质感: Grain 轻微颗粒", "noise": "字体质感: Noise 噪点", "roughen": "字体质感: Roughen 粗糙边", "distressed": "字体质感: Distress texture 破碎磨损", "stacked_distress": "字体质感: 叠加 Grain+Noise+Roughen+Distress"}
        self.text_texture_combo.setCurrentText(texture_map.get(st.get("text_texture", "none"), texture_map["none"]))
        self.hl_motion_combo.setCurrentIndex({"stable": 0, "pop": 1, "push": 2}.get(st.get("hl_motion", "stable"), 0))
        hl_style_value = st.get("hl_style", "text")
        if not st.get("use_hl", True) or hl_style_value == "none":
            hl_label = "高亮样式: 无高亮"
        else:
            hl_style_map = {"text": "高亮样式: 纯字变色", "outline": "高亮样式: 边框模式", "box": "高亮样式: 高亮底盒", "underline": "高亮样式: 下划线", "glow": "高亮样式: 纯字发光", "capsule": "高亮样式: 边框模式", "canva_frame": "高亮样式: 边框模式"}
            hl_label = hl_style_map.get(hl_style_value, hl_style_map["text"])
        self.hl_style_combo.setCurrentText(hl_label)

        try:
            font_name = self._usable_font_name(st.get("font", self.default_style.get("font", "Segoe UI")))
            if font_name != st.get("font"):
                st["font"] = font_name
                if "style" in clip:
                    clip["style"]["font"] = font_name
            self.font_var.blockSignals(True)
            self.font_var.setCurrentFont(QFont(font_name))
            self.font_var.blockSignals(False)
        except Exception:
            self.font_var.blockSignals(False)

        bm = st.get("bg_mode", "none")
        bg_label_map = {"canva_fit": "胶带底框: 贴合文字", "tape": "胶带底框: 逐字单点", "sweep": "胶带底框: 扫光渐变", "block": "胶带底框: 全局底框", "full_frame": "胶带底框: 全屏框架", "cinematic_frame": "胶带底框: 柔光玻璃", "none": "胶带底框: 贴合文字"}
        self.bg_mode_combo.setCurrentText(bg_label_map.get(bm, bg_label_map["block"]))

        for c in controls: c.blockSignals(False)
        self._apply_font_license_filter()
        self._update_font_preview()

        self._update_color_controls_from_style(st)

    def _normalize_hex_color(self, value, fallback="#FFFFFF"):
        fallback = str(fallback or "#FFFFFF").strip().upper()
        raw = str(value or "").strip()
        if not raw:
            return fallback
        if not raw.startswith("#"):
            raw = f"#{raw}"
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
            return raw.upper()
        return fallback

    def _color_key_for_target(self, target):
        return {
            "txt": "color_txt",
            "hl": "color_hl",
            "stroke": "stroke_color",
            "stroke_o": "stroke_o_color",
            "sh": "shadow_color",
            "bg": "bg_color",
            "hl_bg": "hl_bg_color",
            "glow": "global_glow_color",
            "text3d": "text_3d_color",
        }.get(target)

    def _current_style_for_color(self):
        if 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])):
            return self.state["subs_data"][self.current_selected_idx].get("style", self.default_style)
        return self.default_style

    def _set_color_control(self, button_attr, color, enabled=True):
        button = getattr(self, button_attr, None)
        editor = getattr(self, f"{button_attr}_input", None)
        color = self._normalize_hex_color(color)
        try:
            r = int(color[1:3], 16); g = int(color[3:5], 16); b = int(color[5:7], 16)
            fg = "#11111b" if (r * 0.299 + g * 0.587 + b * 0.114) > 150 else "#ffffff"
        except Exception:
            fg = "#ffffff"
        if editor is not None:
            editor.blockSignals(True)
            editor.setText(color)
            editor.setEnabled(enabled)
            editor.blockSignals(False)
        if button is not None:
            button.setEnabled(enabled)
            if enabled:
                button.setText("🎨 点击选色")
                button.setStyleSheet(f"background-color:{color}; color:{fg}; border:1px solid #45475a; border-radius:6px; padding:6px 8px; font-weight:900;")
            else:
                button.setText("🎨 未启用")
                button.setStyleSheet("background-color:#313244; color:#7f849c; border:1px solid #45475a; border-radius:6px; padding:6px 8px; font-weight:800;")

    def _update_color_controls_from_style(self, style=None):
        st = style or self._current_style_for_color()
        highlight_enabled = bool(st.get("use_hl", True)) and st.get("hl_style", "text") != "none"
        bg_enabled = st.get("bg_mode", "none") != "none"
        self._set_color_control("btn_color_txt", st.get("color_txt", "#FFFFFF"), True)
        self._set_color_control("btn_color_hl", st.get("color_hl", "#FFFFFF"), highlight_enabled)
        self._set_color_control("btn_color_hl_bg", st.get("hl_bg_color", "#FF0050"), highlight_enabled)
        self._set_color_control("btn_color_glow", st.get("global_glow_color", "#FFFFFF"), True)
        self._set_color_control("btn_color_stroke", st.get("stroke_color", "#000000"), True)
        self._set_color_control("btn_color_stroke_o", st.get("stroke_o_color", "#000000"), True)
        self._set_color_control("btn_color_sh", st.get("shadow_color", "#000000"), True)
        self._set_color_control("btn_color_text3d", st.get("text_3d_color", "#6F3A05"), True)
        self._set_color_control("btn_color_bg", st.get("bg_color", "#000000"), bg_enabled)

    def _apply_color_input(self, target, editor):
        key = self._color_key_for_target(target)
        if not key:
            return
        current = self._current_style_for_color().get(key, "#FFFFFF")
        color = self._normalize_hex_color(editor.text(), current)
        editor.setText(color)
        self._apply_styles_to_targets(f"{target}_col", color)
        self._update_color_controls_from_style(self._current_style_for_color())
        self.push_history()

    def _on_highlight_style_change(self, *args):
        if hasattr(self, "chk_use_hl") and hasattr(self, "hl_style_combo"):
            no_highlight = "无高亮" in self.hl_style_combo.currentText()
            self.chk_use_hl.blockSignals(True)
            self.chk_use_hl.setChecked(not no_highlight)
            self.chk_use_hl.blockSignals(False)
        self._on_style_change()

    def _apply_styles_to_targets(self, target_type, hex_col=None):
        if not self.state.get("subs_data"):
            return
        if self.current_selected_idx == -1:
            current_clip = self.state["subs_data"][0]
            target_clips = self.state["subs_data"]
        else:
            current_clip = self.state["subs_data"][self.current_selected_idx]
            scope = self.style_scope_combo.currentIndex()
            if scope == 0: target_clips = self.state["subs_data"]
            elif scope == 1: target_clips = [c for c in self.state["subs_data"] if c.get("track") == current_clip.get("track")]
            else: target_clips = [current_clip]

        for c in target_clips:
            if "style" not in c: c["style"] = {}
            if target_type == "txt_col": c["style"]["color_txt"] = hex_col
            elif target_type == "stroke_col": c["style"]["stroke_color"] = hex_col
            elif target_type == "stroke_o_col": c["style"]["stroke_o_color"] = hex_col
            elif target_type == "hl_col": c["style"]["color_hl"] = hex_col
            elif target_type == "sh_col": c["style"]["shadow_color"] = hex_col
            elif target_type == "bg_col": c["style"]["bg_color"] = hex_col
            elif target_type == "hl_bg_col": c["style"]["hl_bg_color"] = hex_col
            elif target_type == "glow_col": c["style"]["global_glow_color"] = hex_col
            elif target_type == "text3d_col": c["style"]["text_3d_color"] = hex_col
            elif target_type == "params":
                c["pos_x"] = float(self.pos_x_spin.value()); c["pos_y"] = float(self.pos_y_spin.value())
                c["style"]["rotation"] = self.rot_slider.value(); c["style"]["font"] = self._usable_font_name(self._selected_font_family())
                c["style"]["font_weight"] = self.font_weight_combo.currentData() if hasattr(self, "font_weight_combo") else "700"
                c["style"]["font_style"] = "italic" if hasattr(self, "chk_font_italic") and self.chk_font_italic.isChecked() else "normal"
                c["style"]["size"] = self.size_slider.value(); c["style"]["letter_spacing"] = self.spacing_slider.value(); c["style"]["word_spacing"] = self.word_spacing_slider.value()
                c["style"]["box_width"] = self.box_width_spin.value()
                c["style"]["box_height"] = self.box_height_spin.value()
                c["style"]["max_lines"] = self.max_lines_slider.value()
                c["style"]["line_height"] = self.lineh_slider.value() / 100.0
                c["style"]["layout_row_gap"] = self.layout_row_gap_slider.value()
                c["style"]["stroke_width"] = self.stroke_slider.value(); c["style"]["stroke_o_width"] = self.stroke_o_slider.value(); c["style"]["stroke_softness"] = self.stroke_soft_slider.value()
                c["style"]["hl_bg_skew"] = self.hl_skew_slider.value()
                c["style"]["use_hl"] = self.chk_use_hl.isChecked(); c["style"]["hl_glow"] = self.chk_hl_glow.isChecked(); c["style"]["glow_size"] = self.glow_size_slider.value()
                c["style"]["global_glow_enable"] = self.chk_global_glow.isChecked()
                c["style"]["global_glow_size"] = self.global_glow_size_slider.value(); c["style"]["global_glow_blur"] = self.global_glow_blur_slider.value()
                c["style"]["global_glow_alpha"] = self.global_glow_alpha_slider.value(); c["style"]["global_glow_x"] = self.global_glow_x_slider.value(); c["style"]["global_glow_y"] = self.global_glow_y_slider.value(); c["style"]["global_glow_z"] = self.global_glow_z_slider.value()
                c["style"]["text_3d_enable"] = self.chk_text_3d.isChecked(); c["style"]["text_3d_depth"] = self.text_3d_depth_slider.value(); c["style"]["text_3d_x"] = self.text_3d_x_slider.value(); c["style"]["text_3d_y"] = self.text_3d_y_slider.value()
                glow_mode_txt = self.global_glow_mode_combo.currentText(); c["style"]["global_glow_mode"] = "neon" if "霓虹" in glow_mode_txt else "sweep" if "扫光" in glow_mode_txt else "soft"
                glow_motion_txt = self.global_glow_motion_combo.currentText(); c["style"]["global_glow_motion"] = "breath" if "呼吸" in glow_motion_txt else "sweep" if "扫光" in glow_motion_txt else "stable"

                c["style"]["shadow_x"] = self.sh_x_slider.value(); c["style"]["shadow_y"] = self.sh_y_slider.value()
                c["style"]["shadow_blur"] = self.sh_blur_slider.value(); c["style"]["shadow_alpha"] = self.sh_a_slider.value()
                c["style"]["pop_speed"] = self.pop_speed_spin.value();
                c["style"]["pop_bounce"] = self.pop_bounce_slider.value()
                c["style"]["inactive_alpha"] = self.inactive_alpha_slider.value()

                c["style"]["bg_alpha"] = self.alpha_slider.value()
                c["style"]["bg_radius"] = self.radius_slider.value()
                c["style"]["bg_padding"] = self.padding_slider.value()
                c["style"]["bg_pad_left"] = self.bg_pad_left_slider.value()
                c["style"]["bg_pad_right"] = self.bg_pad_right_slider.value()
                c["style"]["bg_pad_top"] = self.bg_pad_top_slider.value()
                c["style"]["bg_pad_bottom"] = self.bg_pad_bottom_slider.value()
                c["style"]["bg_auto_resolution"] = self.chk_bg_auto_resolution.isChecked()

                c["style"]["hl_bg_alpha"] = self.hl_alpha_slider.value()
                c["style"]["hl_bg_radius"] = self.hl_radius_slider.value()
                c["style"]["hl_bg_padding"] = self.hl_padding_slider.value()
                c["style"]["hl_pad_left"] = self.hl_pad_left_slider.value()
                c["style"]["hl_pad_right"] = self.hl_pad_right_slider.value()
                c["style"]["hl_pad_top"] = self.hl_pad_top_slider.value()
                c["style"]["hl_pad_bottom"] = self.hl_pad_bottom_slider.value()
                c["style"]["hl_trail_words"] = self.hl_trail_words_slider.value()
                c["style"]["hl_trail_min_alpha"] = self.hl_trail_alpha_slider.value()

                c["style"]["mask_en"] = self.chk_mask_en.isChecked()
                c["style"]["mask_top"] = self.mask_top_slider.value()
                c["style"]["mask_bottom"] = self.mask_bot_slider.value()
                c["style"]["merge_bridge_enable"] = self.chk_merge_bridge.isChecked()
                c["style"]["merge_bridge_width"] = self.merge_bridge_width_slider.value()
                c["style"]["merge_bridge_height"] = self.merge_bridge_height_slider.value()
                c["style"]["merge_bridge_alpha"] = self.merge_bridge_alpha_slider.value()
                mode_txt = self.layout_mode_combo.currentText()
                variant_txt = self.layout_variant_combo.currentText()
                axis_mode_txt = self.axis_mode_combo.currentText()
                head_letter_large_variant = "首字母变大" in variant_txt
                head_large_variant = "开头变大" in variant_txt or "首词变大" in variant_txt
                tail_large_variant = "尾部变大" in variant_txt or "尾词变大" in variant_txt
                if "智能模式" in mode_txt:
                    c["style"]["layout_mode"] = "smart_caption"
                elif "首尾大小" in mode_txt:
                    c["style"]["layout_mode"] = "reel_stack"
                elif "随机重点" in mode_txt:
                    c["style"]["layout_mode"] = "random_focus"
                elif "左右错开" in mode_txt:
                    c["style"]["layout_mode"] = "side_steps"
                elif "中轴" in mode_txt:
                    c["style"]["layout_mode"] = "axis_stack"
                elif "大小对比" in mode_txt:
                    c["style"]["layout_mode"] = "contrast"
                elif "多层" in mode_txt or "累积叙事" in mode_txt:
                    c["style"]["layout_mode"] = "narrative_block"
                elif "三层" in mode_txt:
                    c["style"]["layout_mode"] = "triple"
                else:
                    c["style"]["layout_mode"] = "standard"
                if "\u968f\u673a" in axis_mode_txt:
                    variant_value = "axis-random"
                elif "\u672b\u5c3e\u5206" in axis_mode_txt:
                    variant_value = "axis-split-tail"
                elif "左上小" in axis_mode_txt:
                    variant_value = "axis-diagonal"
                else:
                    variant_value = "head-letter-large" if head_letter_large_variant else "head-large" if head_large_variant else "tail-large" if tail_large_variant else "small-big-small" if "小-大-小" in variant_txt else "big-small-mix" if "大-小-混排" in variant_txt else "mix-big-small" if "混排-大-小" in variant_txt else "axis-random" if "\u968f\u673a" in variant_txt else "axis-split-tail" if "\u7ed3\u5c3e\u5206" in variant_txt else "axis-123" if "1-2-3" in variant_txt else "axis-diagonal" if "左上小" in variant_txt else "auto"
                c["style"]["layout_variant"] = variant_value
                c["style"]["smart_layout_pool"] = ",".join([key for key, chk in getattr(self, "smart_layout_checks", {}).items() if chk.isChecked()]) or "standard"
                c["style"]["layout_pattern"] = self.layout_pattern_input.text().strip() or "auto"
                c["style"]["layout_layer_count"] = self.layout_layer_count_slider.value()
                c["style"]["layout_layer_pattern"] = self.layout_layer_pattern_input.text().strip() or "auto"
                c["style"]["layout_layer_words"] = self.layout_layer_words_input.text().strip() or "auto"
                c["style"]["axis_spread"] = self.axis_spread_slider.value()
                c["style"]["axis_gap"] = self.axis_gap_slider.value()
                c["style"]["box_layout"] = "fixed" if "固定窗口" in self.box_layout_combo.currentText() else "auto"
                c["style"]["emphasis_scale"] = self.emphasis_slider.value()
                c["style"]["contrast_small_scale"] = self.contrast_small_slider.value() / 100.0

                tc = self.transform_combo.currentText()
                c["style"]["text_transform"] = "uppercase" if "UPPERCASE" in tc else "lowercase" if "lowercase" in tc else "capitalize" if "Capitalize" in tc else "none"
                ac = self.align_combo.currentText()
                if "居中左对齐" in ac or "Center Left" in ac:
                    c["style"]["text_align"] = "center_left"
                elif "左对齐为主" in ac or "Left Mix" in ac:
                    c["style"]["text_align"] = "left_mix"
                elif "自由混合" in ac or "Free Mix" in ac:
                    c["style"]["text_align"] = "free_mix"
                elif "Left" in ac:
                    c["style"]["text_align"] = "left"
                elif "Right" in ac:
                    c["style"]["text_align"] = "right"
                elif "Justify" in ac:
                    c["style"]["text_align"] = "justify"
                else:
                    c["style"]["text_align"] = "center"
                anc = self.anim_combo.currentText()
                if "Holy Breath" in anc or "圣息" in anc:
                    c["style"]["anim_type"] = "holy_breath"
                elif "Slam" in anc or "砸入" in anc:
                    c["style"]["anim_type"] = "slam_in"
                elif "Grow" in anc or "慢慢放大" in anc:
                    c["style"]["anim_type"] = "grow_in"
                elif "Letter" in anc or "字字分散" in anc:
                    c["style"]["anim_type"] = "letter_scatter_in"
                elif "Camera" in anc or "镜头推进" in anc:
                    c["style"]["anim_type"] = "camera_push"
                elif "Depth" in anc or "3D" in anc or "远近推进" in anc:
                    c["style"]["anim_type"] = "depth_push"
                elif "Scatter" in anc or "散开入场" in anc:
                    c["style"]["anim_type"] = "scatter_in"
                elif "Blur" in anc or "模糊" in anc:
                    c["style"]["anim_type"] = "blur_fade"
                elif "单词遮罩" in anc:
                    c["style"]["anim_type"] = "word_wipe"
                elif "Wipe" in anc or "遮罩" in anc:
                    c["style"]["anim_type"] = "wipe_right"
                elif "Pop" in anc:
                    c["style"]["anim_type"] = "pop"
                elif "Fade" in anc:
                    c["style"]["anim_type"] = "fade"
                elif "Roll Up" in anc:
                    c["style"]["anim_type"] = "roll_up"
                else:
                    c["style"]["anim_type"] = "none"
                font_motion_txt = self.font_motion_combo.currentText()
                if "打字机" in font_motion_txt:
                    c["style"]["font_motion"] = "typewriter_left"
                elif "波浪" in font_motion_txt:
                    c["style"]["font_motion"] = "wave"
                elif "水波" in font_motion_txt or "立体流动" in font_motion_txt:
                    c["style"]["font_motion"] = "ripple3d"
                elif "慢呼吸" in font_motion_txt:
                    c["style"]["font_motion"] = "breathe"
                elif "慢慢分散" in font_motion_txt:
                    c["style"]["font_motion"] = "drift"
                elif "忽大忽小" in font_motion_txt or "跳动" in font_motion_txt:
                    c["style"]["font_motion"] = "pulse"
                else:
                    c["style"]["font_motion"] = "none"
                texture_txt = self.text_texture_combo.currentText()
                if "Gold" in texture_txt or "金色" in texture_txt:
                    c["style"]["text_texture"] = "gold_metal"
                elif "叠加" in texture_txt or "Grain+Noise" in texture_txt:
                    c["style"]["text_texture"] = "stacked_distress"
                elif "Noise" in texture_txt or "噪点" in texture_txt:
                    c["style"]["text_texture"] = "noise"
                elif "Roughen" in texture_txt or "粗糙" in texture_txt:
                    c["style"]["text_texture"] = "roughen"
                elif "Distress" in texture_txt or "破碎" in texture_txt or "磨损" in texture_txt:
                    c["style"]["text_texture"] = "distressed"
                elif "Grain" in texture_txt or "颗粒" in texture_txt:
                    c["style"]["text_texture"] = "grain"
                else:
                    c["style"]["text_texture"] = "none"
                hl_motion_idx = self.hl_motion_combo.currentIndex()
                c["style"]["hl_motion"] = "push" if hl_motion_idx == 2 else "pop" if hl_motion_idx == 1 else "stable"
                hl_style_txt = self.hl_style_combo.currentText()
                if "无高亮" in hl_style_txt:
                    c["style"]["use_hl"] = False
                    c["style"]["hl_style"] = "none"
                else:
                    c["style"]["use_hl"] = self.chk_use_hl.isChecked()
                    if "边框" in hl_style_txt:
                        c["style"]["hl_style"] = "outline"
                    elif "底盒" in hl_style_txt or "色块" in hl_style_txt:
                        c["style"]["hl_style"] = "box"
                    elif "下划线" in hl_style_txt:
                        c["style"]["hl_style"] = "underline"
                    elif "发光" in hl_style_txt:
                        c["style"]["hl_style"] = "glow"
                    else:
                        c["style"]["hl_style"] = "text"

                b_txt = self.bg_mode_combo.currentText()
                if not self.chk_bg_enabled.isChecked():
                    c["style"]["bg_mode"] = "none"
                elif "逐字" in b_txt or "单点" in b_txt:
                    c["style"]["bg_mode"] = "tape"
                elif "贴合" in b_txt:
                    c["style"]["bg_mode"] = "canva_fit"
                elif "渐变" in b_txt or "扫光" in b_txt:
                    c["style"]["bg_mode"] = "sweep"
                elif "全屏" in b_txt or "框架" in b_txt:
                    c["style"]["bg_mode"] = "full_frame"
                elif "玻璃" in b_txt or "柔光" in b_txt:
                    c["style"]["bg_mode"] = "cinematic_frame"
                else:
                    c["style"]["bg_mode"] = "block"

        for k in self.default_style.keys():
            if "style" in current_clip and k in current_clip["style"]:
                self.default_style[k] = current_clip["style"][k]

        self.default_style["font"] = self._usable_font_name(self._selected_font_family()) if hasattr(self, "font_var") else self.default_style.get("font", "Segoe UI")
        self._update_color_controls_from_style(current_clip.get("style", self.default_style))
        self.update_floating_subtitle(); self.auto_save_cache()

    def _pick_color(self, target):
        key = self._color_key_for_target(target)
        current = self._current_style_for_color().get(key, "#FFFFFF") if key else "#FFFFFF"
        color = QColorDialog.getColor(QColor(current), self, "选择颜色")
        if color.isValid():
            self._apply_styles_to_targets(f"{target}_col", color.name().upper())
            self._update_color_controls_from_style(self._current_style_for_color())
            self.push_history()

    def _on_style_change(self, *args):
        self._apply_styles_to_targets("params");
        self._update_font_preview()

    def audit_and_reflow_subtitles(self):
        if not self.state.get("subs_data"):
            QMessageBox.information(self, "没有字幕", "当前工程还没有字幕片段可以检查。")
            return
        safe_subs = self.sanitize_subs_data(copy.deepcopy(self.state["subs_data"]))
        balanced, stats = rebalance_subtitle_layout(
            safe_subs,
            fallback_style=self.default_style,
            default_pos=(self.state.get("default_pos_x", 0.0), self.state.get("default_pos_y", 25.0)),
            proj_w=self.proj_width,
            force_standard_box=True,
            allow_split=True
        )
        self.state["subs_data"] = balanced
        if balanced:
            self.state["duration"] = max(self.state.get("duration", 0.0), max(float(s.get("end", 0.0)) for s in balanced))
        self.current_selected_idx = -1
        self.render_ui_list()
        self.update_timeline_size()
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.push_history()
        self.status_lbl.setText(f"✅ 已整理字幕: 拆分 {stats['split']} 段，修复 {stats['overlaps_fixed']} 处重叠")
        QMessageBox.information(
            self,
            "字幕检查完成",
            f"检查前 {stats['before']} 段，整理后 {stats['after']} 段。\n"
            f"已拆分过长字幕 {stats['split']} 段，修复同轨重叠 {stats['overlaps_fixed']} 处。"
        )

    def _on_vid_prop_change(self): self.state["v_scale"] = self.v_scale_slider.value(); self.state["v_volume"] = self.v_vol_slider.value(); self.audio_output.setVolume(self.state["v_volume"] / 100.0); self.sync_player_to_time(self.current_play_time); self.auto_save_cache()
    def _on_aud_prop_change(self): self.state["a_volume"] = self.a_vol_slider.value(); self.audio_track_output.setVolume(self.state["a_volume"] / 100.0); self.auto_save_cache()
    def _on_music_prop_change(self):
        self.state["music_volume"] = self.music_vol_slider.value()
        if hasattr(self, "music_output"):
            self.music_output.setVolume(self.state["music_volume"] / 100.0)
        self.auto_save_cache()

    def _signature_state(self):
        sig = normalize_signature_config(self.state.get("signature"), self.default_style)
        self.state["signature"] = sig
        return sig

    def _signature_label_to_value(self, label):
        return {
            "右上角": "top_right",
            "左上角": "top_left",
            "右下角": "bottom_right",
            "左下角": "bottom_left",
            "顶部居中": "top_center",
            "底部居中": "bottom_center",
            "自定义位置": "custom",
        }.get(label, "top_right")

    def _signature_value_to_label(self, value):
        return {
            "top_right": "右上角",
            "top_left": "左上角",
            "bottom_right": "右下角",
            "bottom_left": "左下角",
            "top_center": "顶部居中",
            "bottom_center": "底部居中",
            "custom": "自定义位置",
        }.get(value, "右上角")

    def _signature_bg_label_to_value(self, label):
        if "无背景" in label:
            return "none"
        if "纯色" in label or "底框" in label:
            return "block"
        return "cinematic_frame"

    def _signature_bg_value_to_label(self, value):
        if value == "none":
            return "无背景"
        if value == "block":
            return "纯色底框"
        return "柔光玻璃背景"

    def _update_signature_color_buttons(self, style):
        if not hasattr(self, "btn_signature_text_color"):
            return
        txt = style.get("color_txt", "#FFFFFF")
        bg = style.get("bg_color", "#0B1020")
        self.btn_signature_text_color.setStyleSheet(f"background-color: {txt}; color: #11111b; font-weight: bold; border-radius: 6px; padding: 7px 10px;")
        self.btn_signature_bg_color.setStyleSheet(f"background-color: {bg}; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 7px 10px;")

    def sync_signature_controls(self):
        if not hasattr(self, "signature_text_input"):
            return
        sig = self._signature_state()
        widgets = [
            self.chk_signature_enabled,
            self.signature_text_input,
            self.signature_position_combo,
            self.signature_size_slider,
            self.signature_size_spin,
            self.signature_margin_x_slider,
            self.signature_margin_x_spin,
            self.signature_margin_y_slider,
            self.signature_margin_y_spin,
            self.signature_bg_combo,
            self.signature_bg_alpha_slider,
            self.signature_bg_alpha_spin,
            self.signature_bg_radius_slider,
            self.signature_bg_radius_spin,
            self.signature_bg_padding_slider,
            self.signature_bg_padding_spin,
        ]
        for w in widgets:
            w.blockSignals(True)
        self.chk_signature_enabled.setChecked(bool(sig.get("enabled")))
        self.signature_text_input.setText(str(sig.get("text", "") or ""))
        self.signature_position_combo.setCurrentText(self._signature_value_to_label(sig.get("placement", "top_right")))
        size = int(sig.get("style", {}).get("size", 42) or 42)
        self.signature_size_spin.setValue(size)
        self.signature_size_slider.setValue(size)
        margin_x = float(sig.get("margin_x", 5.0) or 0.0)
        margin_y = float(sig.get("margin_y", 4.0) or 0.0)
        self.signature_margin_x_spin.setValue(margin_x)
        self.signature_margin_x_slider.setValue(int(margin_x * 100))
        self.signature_margin_y_spin.setValue(margin_y)
        self.signature_margin_y_slider.setValue(int(margin_y * 100))
        style = sig.get("style", {})
        self.signature_bg_combo.setCurrentText(self._signature_bg_value_to_label(style.get("bg_mode", "cinematic_frame")))
        bg_alpha = int(style.get("bg_alpha", 45) or 0)
        bg_radius = int(style.get("bg_radius", 26) or 0)
        bg_padding = int(style.get("bg_padding", 10) or 0)
        self.signature_bg_alpha_spin.setValue(bg_alpha)
        self.signature_bg_alpha_slider.setValue(bg_alpha)
        self.signature_bg_radius_spin.setValue(bg_radius)
        self.signature_bg_radius_slider.setValue(bg_radius)
        self.signature_bg_padding_spin.setValue(bg_padding)
        self.signature_bg_padding_slider.setValue(bg_padding)
        self._update_signature_color_buttons(style)
        for w in widgets:
            w.blockSignals(False)

    def _on_signature_change(self, *args):
        if not hasattr(self, "signature_text_input"):
            return
        sig = self._signature_state()
        sig["enabled"] = self.chk_signature_enabled.isChecked()
        sig["text"] = self.signature_text_input.text().strip()
        sig["placement"] = self._signature_label_to_value(self.signature_position_combo.currentText())
        sig["margin_x"] = float(self.signature_margin_x_spin.value())
        sig["margin_y"] = float(self.signature_margin_y_spin.value())
        sig.setdefault("style", default_signature_config(self.default_style)["style"])
        sig["style"]["size"] = int(self.signature_size_spin.value())
        sig["style"]["bg_mode"] = self._signature_bg_label_to_value(self.signature_bg_combo.currentText())
        sig["style"]["bg_alpha"] = int(self.signature_bg_alpha_spin.value())
        sig["style"]["bg_radius"] = int(self.signature_bg_radius_spin.value())
        sig["style"]["bg_padding"] = int(self.signature_bg_padding_spin.value())
        sig["style"]["bg_pad_left"] = int(self.signature_bg_padding_spin.value() * 1.8)
        sig["style"]["bg_pad_right"] = int(self.signature_bg_padding_spin.value() * 1.8)
        sig["style"]["bg_pad_top"] = max(0, int(self.signature_bg_padding_spin.value() * 0.5))
        sig["style"]["bg_pad_bottom"] = max(0, int(self.signature_bg_padding_spin.value() * 0.55))
        if sig["placement"] in ("top_right", "bottom_right"):
            sig["style"]["text_align"] = "right"
        elif sig["placement"] in ("top_left", "bottom_left"):
            sig["style"]["text_align"] = "left"
        else:
            sig["style"]["text_align"] = "center"
        self.state["signature"] = sig
        self._update_signature_color_buttons(sig["style"])
        self.last_render_hash = None
        self.update_floating_subtitle()
        self._update_workspace_status()
        self.auto_save_cache()

    def _pick_signature_color(self, target):
        sig = self._signature_state()
        sig.setdefault("style", default_signature_config(self.default_style)["style"])
        current = sig["style"].get("color_txt" if target == "text" else "bg_color", "#FFFFFF")
        color = QColorDialog.getColor(QColor(current), self, "选择署名颜色")
        if not color.isValid():
            return
        if target == "text":
            sig["style"]["color_txt"] = color.name()
            sig["style"]["color_hl"] = color.name()
        else:
            sig["style"]["bg_color"] = color.name()
        self.state["signature"] = sig
        self.sync_signature_controls()
        self.last_render_hash = None
        self.update_floating_subtitle()
        self.auto_save_cache()

    def save_signature_preset(self):
        sig = copy.deepcopy(self._signature_state())
        name, ok = QInputDialog.getText(self, "存署名模板", "给这个署名模板起个名字:")
        if not ok or not name.strip():
            return
        if name.strip() in self._built_in_signature_presets():
            return QMessageBox.warning(self, "提示", "这个名字是内置模板，请换一个名字保存。")
        presets = self.load_signature_presets()
        presets[name.strip()] = sig
        self.save_signature_presets(presets)
        self.refresh_signature_preset_combo()
        self.notify_batch_presets_changed(signature_name=name.strip())
        idx = self.signature_template_combo.findData(name.strip(), Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self.signature_template_combo.setCurrentIndex(idx)
        self.status_lbl.setText(f"✅ 署名模板 '{name.strip()}' 已保存")

    def apply_signature_preset(self):
        if not hasattr(self, "signature_template_combo"):
            return
        name = self.signature_template_combo.currentData(Qt.ItemDataRole.UserRole)
        presets = self.load_signature_presets()
        if not name or name not in presets:
            return
        current_text = self._signature_state().get("text", "")
        sig = normalize_signature_config(copy.deepcopy(presets[name]), self.default_style)
        if current_text and not sig.get("text"):
            sig["text"] = current_text
        sig["enabled"] = True
        self.state["signature"] = sig
        self.sync_signature_controls()
        self.last_render_hash = None
        self.update_floating_subtitle()
        self.auto_save_cache()
        self.status_lbl.setText(f"✅ 已应用署名模板 '{name}'")

    def delete_signature_preset(self):
        name = self.signature_template_combo.currentData(Qt.ItemDataRole.UserRole) if hasattr(self, "signature_template_combo") else ""
        built_ins = set(self._built_in_signature_presets().keys())
        if not name or name in built_ins:
            return QMessageBox.information(self, "提示", "内置署名模板不能删除。")
        presets = self.load_signature_presets()
        if name not in presets:
            return
        reply = QMessageBox.question(self, "删除署名模板", f'确定要删除署名模板 "{name}" 吗？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        del presets[name]
        self.save_signature_presets(presets)
        self.refresh_signature_preset_combo()
        self.notify_batch_presets_changed()
        self.status_lbl.setText(f"🗑️ 署名模板 '{name}' 已删除")

    def reset_signature_template(self):
        current_text = ""
        current_enabled = False
        if isinstance(self.state.get("signature"), dict):
            current_text = self.state["signature"].get("text", "")
            current_enabled = bool(self.state["signature"].get("enabled"))
        sig = default_signature_config(self.default_style)
        sig["text"] = current_text
        sig["enabled"] = current_enabled
        self.state["signature"] = sig
        self.sync_signature_controls()
        self.last_render_hash = None
        self.update_floating_subtitle()
        self.auto_save_cache()

    def capture_signature_template(self):
        sig = self._signature_state()
        if 0 <= self.current_selected_idx < len(self.state.get("subs_data", [])):
            clip = self.state["subs_data"][self.current_selected_idx]
            sig["style"] = copy.deepcopy(clip.get("style", self.default_style))
            sig["style"]["anim_type"] = "none"
            sig["style"]["font_motion"] = "none"
            sig["style"]["use_hl"] = False
            sig["pos_x"] = float(clip.get("pos_x", 0.0) or 0.0)
            sig["pos_y"] = float(clip.get("pos_y", -42.0) or -42.0)
            sig["placement"] = "custom"
            if not sig.get("text"):
                sig["text"] = str(clip.get("text", "") or "").strip()
        else:
            sig["style"] = default_signature_config(self.default_style)["style"]
            sig["placement"] = "top_right"
        sig["enabled"] = True
        self.state["signature"] = sig
        self.sync_signature_controls()
        self.last_render_hash = None
        self.update_floating_subtitle()
        self.auto_save_cache()

    def generate_waveform(self, path, attr_name, max_seconds=None):
        if not path or not os.path.exists(path): return
        def _task():
            try:
                out = os.path.join(tempfile.gettempdir(), f"sh_wave_{attr_name}.png")
                cmd = [get_ffmpeg_cmd(), "-y", "-i", path, "-map", "0:a:0?"]
                try:
                    limit = float(max_seconds or 0.0)
                except Exception:
                    limit = 0.0
                if limit > 0:
                    cmd.extend(["-t", f"{limit:.3f}"])
                cmd.extend(["-filter_complex", "showwavespic=s=2000x60:colors=#a6e3a1", "-frames:v", "1", out])
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=10)
                if os.path.exists(out): QTimer.singleShot(0, lambda: self._apply_waveform(out, attr_name))
            except: pass
        threading.Thread(target=_task, daemon=True).start()

    def _apply_waveform(self, img_path, attr_name):
        setattr(self, attr_name, QPixmap(img_path)); self.timeline_widget.sync_from_controller()

    def _preview_proxy_settings(self):
        return preview_proxy_settings(getattr(self, "preview_proxy_resolution", None) or get_preview_proxy_resolution())

    def on_preview_proxy_resolution_changed(self, value):
        saved = set_preview_proxy_resolution(value)
        self.preview_proxy_resolution = saved
        combo = getattr(self, "preview_proxy_resolution_combo", None)
        if combo is not None and combo.currentText() != saved:
            combo.blockSignals(True)
            combo.setCurrentText(saved)
            combo.blockSignals(False)
        self.video_thumbs = []
        self.last_video_image = None
        self._preview_scaled_pixmap_key = None
        self._preview_scaled_pixmap = None
        clips = self.state.get("video_clips", []) or []
        active_idx, active_clip = self._video_clip_for_time(self.current_play_time)
        was_playing = bool(getattr(self, "is_playing", False))
        for clip in clips:
            self._queue_preview_proxy_for_clip(clip, announce=False)
        if active_clip:
            if was_playing and not preview_proxy_is_ready(active_clip):
                # Keep the current player source alive while the newly selected proxy is being generated.
                pass
            else:
                self._prime_video_preview_source(active_clip, announce=True)
                self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
        if hasattr(self, "status_lbl"):
            if was_playing and active_clip and not preview_proxy_is_ready(active_clip):
                self.status_lbl.setText(f"预览清晰度已切换为 {saved}，正在后台生成；当前播放先保持旧预览，导出不受影响。")
            else:
                self.status_lbl.setText(f"预览清晰度已切换为 {saved}，导出画质不受影响。")
        self.auto_save_cache()

    def _ensure_clip_import_metadata(self, clip):
        if not isinstance(clip, dict):
            return clip
        path = clip.get("path", "")
        if not path or not os.path.exists(path):
            return clip
        has_size = int(float(clip.get("width", 0) or 0)) > 0 and int(float(clip.get("height", 0) or 0)) > 0
        has_duration = float(clip.get("dur", 0.0) or 0.0) > 0
        if has_size and has_duration:
            return clip
        try:
            meta = get_video_import_metadata(path)
            if not has_duration and float(meta.get("duration", 0.0) or 0.0) > 0:
                clip["dur"] = float(meta.get("duration", 0.0) or 0.0)
            if not has_size:
                clip["width"] = int(meta.get("width", 0) or 0)
                clip["height"] = int(meta.get("height", 0) or 0)
            clip.setdefault("duration_probe", meta.get("duration_info", {}))
        except Exception:
            pass
        return clip

    def _clip_needs_preview_proxy(self, clip):
        clip = self._ensure_clip_import_metadata(clip)
        return bool(
            getattr(self, "preview_proxy_auto_generate", False)
            and isinstance(clip, dict)
            and clip_should_auto_proxy(clip)
        )

    def _should_defer_original_preview(self, clip):
        if not self._clip_needs_preview_proxy(clip):
            return False
        if preview_proxy_is_ready(clip):
            return False
        if clip.get("preview_proxy_status") == PROXY_STATUS_FAILED:
            return False
        return True

    def _clip_dimensions_from_state(self, media_path):
        media_path = os.path.abspath(media_path or "")
        for clip in self.state.get("video_clips", []) or []:
            if os.path.abspath(clip.get("path", "") or "") != media_path:
                continue
            width = int(float(clip.get("width", 0) or 0))
            height = int(float(clip.get("height", 0) or 0))
            if width > 0 and height > 0:
                return width, height
        return get_video_dimensions(media_path)

    def _preview_media_path_for_clip(self, clip):
        if self._should_defer_original_preview(clip):
            return ""
        path = preview_source_for_clip(clip)
        if path and os.path.exists(path):
            return path
        return clip.get("path", "") if isinstance(clip, dict) else ""

    def _prime_video_preview_source(self, clip, announce=False):
        if not isinstance(clip, dict):
            return False
        self.last_video_image = None
        self._preview_scaled_pixmap_key = None
        self._preview_scaled_pixmap = None
        self._preview_frame_retry_count = 0
        path = self._preview_media_path_for_clip(clip)
        if path:
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            return True
        if self._should_defer_original_preview(clip):
            self.player.setSource(QUrl())
            if announce and hasattr(self, "status_lbl"):
                self.status_lbl.setText("长视频/高码率素材已加入；正在后台生成流畅预览代理，期间界面可继续操作。")
        return False

    def _prepare_preview_proxies_for_clips(self, clips, announce=False):
        for idx, clip in enumerate(clips or []):
            self._queue_preview_proxy_for_clip(clip, announce=announce and idx == 0)

    def _queue_preview_proxy_for_clip(self, clip, announce=False):
        if not getattr(self, "preview_proxy_auto_generate", False):
            return
        clip = self._ensure_clip_import_metadata(clip)
        if not clip_should_auto_proxy(clip):
            return
        proxy_settings = self._preview_proxy_settings()
        proxy_path, fingerprint, needs_generation = prepare_clip_for_preview_proxy(
            clip,
            proxy_height=proxy_settings.get("height"),
            proxy_fps=proxy_settings.get("fps"),
            proxy_crf=proxy_settings.get("crf"),
        )
        if not proxy_path or not needs_generation:
            return
        job_key = os.path.abspath(proxy_path)
        if job_key in self._preview_proxy_jobs:
            return
        self._preview_proxy_jobs.add(job_key)
        clip["preview_proxy_status"] = PROXY_STATUS_GENERATING
        clip["preview_proxy_error"] = ""
        if announce and hasattr(self, "status_lbl"):
            self.status_lbl.setText("正在后台生成流畅预览代理，生成后会自动切换预览源...")
        self.auto_save_cache()
        threading.Thread(
            target=self._generate_preview_proxy_task,
            args=(clip.get("path", ""), proxy_path, fingerprint, proxy_settings),
            daemon=True,
        ).start()

    def _generate_preview_proxy_task(self, source_path, proxy_path, fingerprint, proxy_settings=None):
        tmp_path = proxy_path + ".tmp.mp4"
        try:
            if not source_path or not os.path.exists(source_path):
                raise FileNotFoundError(source_path or "empty source video")
            os.makedirs(os.path.dirname(proxy_path), exist_ok=True)
            if not os.path.exists(proxy_path) or os.path.getsize(proxy_path) <= 1024:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                proxy_settings = proxy_settings or self._preview_proxy_settings()
                cmd = build_preview_proxy_command(
                    get_ffmpeg_cmd(),
                    source_path,
                    tmp_path,
                    proxy_height=proxy_settings.get("height"),
                    proxy_fps=proxy_settings.get("fps"),
                    proxy_crf=proxy_settings.get("crf"),
                )
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=True)
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) <= 1024:
                    raise RuntimeError("Preview proxy file was not created.")
                os.replace(tmp_path, proxy_path)
            QTimer.singleShot(0, lambda sp=source_path, pp=proxy_path, fp=fingerprint: self._finish_preview_proxy_job(sp, pp, fp, True, ""))
        except Exception as exc:
            error = str(exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            QTimer.singleShot(0, lambda sp=source_path, pp=proxy_path, fp=fingerprint, err=error: self._finish_preview_proxy_job(sp, pp, fp, False, err))

    def _finish_preview_proxy_job(self, source_path, proxy_path, fingerprint, success, error):
        self._preview_proxy_jobs.discard(os.path.abspath(proxy_path))
        matched_clip = False
        matched_active_clip = False
        for clip in self.state.get("video_clips", []) or []:
            if clip.get("path") != source_path or clip.get("preview_proxy_fingerprint") != fingerprint:
                continue
            matched_clip = True
            if success:
                clip["preview_proxy_path"] = proxy_path
                clip["preview_proxy_status"] = PROXY_STATUS_READY
                clip["preview_proxy_error"] = ""
            else:
                clip["preview_proxy_status"] = PROXY_STATUS_FAILED
                clip["preview_proxy_error"] = error[:300]
            _, active_clip = self._video_clip_for_time(self.current_play_time)
            matched_active_clip = matched_active_clip or active_clip is clip
        if not matched_clip:
            return
        if success:
            if hasattr(self, "status_lbl"):
                self.status_lbl.setText("流畅预览代理已生成，预览已切换到轻量素材。")
            if matched_active_clip:
                self.last_video_image = None
                self._preview_frame_retry_count = 0
                self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
                threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
        elif hasattr(self, "status_lbl"):
            self.status_lbl.setText("流畅预览代理生成失败，已继续使用原素材预览。")
        self.auto_save_cache()

    def set_audio_path_from_file(self, file_path, record_history=True):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        self.state["audio_path"] = file_path
        self.state["audio_source_in"] = 0.0
        self.btn_a.setText("✅ " + os.path.basename(file_path)[:15])
        self.audio_player.setSource(QUrl.fromLocalFile(file_path))
        a_dur = get_exact_duration(file_path)
        if a_dur > 0:
            self.state["a_trim"] = [0.0, a_dur]
        self._recalc_duration()
        self.generate_waveform(file_path, "a_wave_pixmap")
        self.update_timeline_size()
        self.auto_save_cache()
        if self.edit_mode:
            self.switch_inspector("audio")
        self.status_lbl.setText("🎵 音频素材已加入配音轨。")
        self.refresh_media_pool()
        if record_history:
            self.push_history()
        return True

    def add_video_clip_from_path(self, file_path, start_t=None):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        try:
            media_meta = get_video_import_metadata(file_path)
            dur = float(media_meta.get("duration", 0.0) or 0.0)
            duration_info = media_meta.get("duration_info", {})
        except Exception:
            media_meta = {}
            dur, duration_info = 0.0, {}
        if dur <= 0:
            dur = get_video_stream_duration(file_path) or get_exact_duration(file_path)
        if dur <= 0:
            dur = 5.0
        try:
            video_w = int(media_meta.get("width", 0) or 0)
            video_h = int(media_meta.get("height", 0) or 0)
            if video_w <= 0 or video_h <= 0:
                video_w, video_h = get_video_dimensions(file_path)
        except Exception:
            video_w, video_h = 0, 0
        clips = self.state.get("video_clips", [])
        if start_t is None:
            start_t = clips[-1]["end"] if clips else 0.0
        else:
            start_t = max(0.0, float(start_t or 0.0))
        new_clip = {
            "path": file_path,
            "start": start_t,
            "end": start_t + dur,
            "dur": dur,
            "width": int(video_w or 0),
            "height": int(video_h or 0),
            "duration_probe": duration_info,
            "source_in": 0.0,
            "source_out": dur,
            "transition": {"type": "cut", "duration": 0.0}
        }
        clips.append(new_clip)
        clips.sort(key=lambda c: float(c.get("start", 0.0) or 0.0))
        self.state["video_clips"] = clips
        self.btn_v.setText("✅ 已导原素材")
        self.current_v_idx = clips.index(new_clip)
        self.current_selected_idx = -1
        self._queue_preview_proxy_for_clip(new_clip, announce=True)
        if len(clips) == 1 or not self.player.source().isValid():
            self._prime_video_preview_source(new_clip, announce=True)
            self.on_resolution_changed(self.res_combo.currentText())
            self.generate_waveform(file_path, "v_wave_pixmap", max_seconds=90)
            threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
        self._recalc_duration()
        self.auto_save_cache()
        if self.edit_mode:
            self.switch_inspector("video")
            self.sync_player_to_time(start_t)
        QTimer.singleShot(0, self._request_preview_video_refresh)
        QTimer.singleShot(280, self._request_preview_video_refresh)
        self.status_lbl.setText("🎞️ 视频素材已加入时间线。")
        self.refresh_media_pool()
        self.push_history()
        return True

    def load_audio(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择音频", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        if file_path:
            self.set_audio_path_from_file(file_path)
        return

    def set_music_path_from_file(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return False
        file_path = self.cloud_import_media_if_needed(file_path)
        self.state["music_path"] = file_path
        self.state.setdefault("music_volume", 35)
        music_dur = get_exact_duration(file_path)
        if music_dur and music_dur > 0:
            self.state["music_dur"] = float(music_dur)
        if hasattr(self, "btn_music"):
            self.btn_music.setText("✅ " + os.path.basename(file_path)[:15])
        if hasattr(self, "music_player"):
            self.music_player.setSource(QUrl.fromLocalFile(file_path))
            self.music_player.setLoops(QMediaPlayer.Loops.Infinite)
        if hasattr(self, "music_output"):
            self.music_output.setVolume(float(self.state.get("music_volume", 35) or 35) / 100.0)
        self.match_music_to_audio(show_message=False)
        self.update_timeline_size()
        self.auto_save_cache()
        self._update_workspace_status()
        self.status_lbl.setText("🎼 配乐已加入；导出时会自动循环/裁切匹配工程时长。")
        self.refresh_media_pool()
        self.push_history()
        return True

    def load_music(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择配乐", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        if file_path:
            self.set_music_path_from_file(file_path)
        return

    def load_video(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video Files (*.mp4 *.mov *.webm *.mkv *.avi)")
        if file_path:
            self.add_video_clip_from_path(file_path)
        return

    def auto_fill_video(self):
        clips = self.state.get("video_clips", [])
        if not clips: return QMessageBox.warning(self, "提示", "请先导入一段视频作为底料！")
        a_path = self.state.get("audio_path", "")
        if not a_path: return QMessageBox.warning(self, "提示", "请先导入配音才能进行一键对齐！")
        a_dur = get_exact_duration(a_path)
        if a_dur <= 0: return
        compound_clip = clips[0]; compound_clip["start"] = 0.0; compound_clip["end"] = a_dur
        self.state["video_clips"] = [compound_clip]; self._recalc_duration(); self.auto_save_cache(); self.timeline_widget.sync_from_controller()
        self.refresh_media_pool()
        self.push_history()
        QMessageBox.information(self, "铺满成功", f"🚀 已将视频转换为复合片段！\n内部自动循环并紧密匹配音频时长 ({a_dur:.1f}s)。")

    def remove_last_video_clip(self):
        if not self._ensure_edit_mode("删除视频"):
            return
        clips = self.state.get("video_clips", [])
        if clips:
            clips.pop(); self.state["video_clips"] = clips
            if not clips: self.btn_v.setText("➕ 导入第一段画面 (MP4)"); self.player.stop(); self.v_wave_pixmap = None
            self._recalc_duration(); self.auto_save_cache(); self.update_timeline_size(); self.refresh_media_pool(); self.push_history()

    def remove_audio(self):
        if self.state.get("audio_path"):
            self.state["audio_path"] = ""
            self.state.pop("audio_source_in", None)
            self.btn_a.setText("🎵 导入独立配音 (可选)")
            self.audio_player.stop()
            self.a_wave_pixmap = None
            self._recalc_duration()
            self.update_timeline_size()
            self.auto_save_cache()
            self.status_lbl.setText("🗑️ 配音已清除")
            self.refresh_media_pool()
            self.push_history()

    def remove_music(self):
        if self.state.get("music_path"):
            self.state["music_path"] = ""
            self.state.pop("music_dur", None)
            self.state.pop("music_match_duration", None)
            self.state.pop("music_loop", None)
            if hasattr(self, "btn_music"):
                self.btn_music.setText("🎼 导入配乐 (可选)")
            if hasattr(self, "music_player"):
                self.music_player.stop()
                self.music_player.setSource(QUrl())
            self._recalc_duration()
            self.update_timeline_size()
            self.auto_save_cache()
            self._update_workspace_status()
            self.status_lbl.setText("配乐已清除")
            self.refresh_media_pool()
            self.push_history()

    def match_music_to_audio(self, show_message=True):
        music_path = self.state.get("music_path", "")
        if not music_path:
            return QMessageBox.warning(self, "提示", "请先导入配乐。")
        target_dur = 0.0
        a_path = self.state.get("audio_path", "")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    target_dur = max(0.0, float(a_trim[1]) - float(a_trim[0]))
                except Exception:
                    target_dur = 0.0
            if target_dur <= 0:
                target_dur = get_exact_duration(a_path) or 0.0
        if target_dur <= 0:
            self._recalc_duration()
            target_dur = float(self.state.get("duration", 0.0) or 0.0)
        if target_dur <= 1.0:
            target_dur = float(self.state.get("music_dur", 0.0) or 0.0) or get_exact_duration(music_path) or target_dur
        self.state["music_match_duration"] = max(1.0, target_dur)
        self.state["music_loop"] = True
        self.update_timeline_size()
        self.auto_save_cache()
        self.status_lbl.setText(f"配乐已匹配到 {self.state['music_match_duration']:.1f}s，导出会自动循环/裁切。")
        if show_message:
            QMessageBox.information(self, "配乐匹配完成", f"配乐会在导出时自动循环或裁切到 {self.state['music_match_duration']:.1f} 秒。")

    def _recalc_duration(self):
        clips = self.state.get("video_clips", [])
        durations = [float(c.get("end", 0.0) or 0.0) for c in clips]

        a_path = self.state.get("audio_path")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    durations.append(max(0.0, float(a_trim[1])))
                except Exception:
                    pass
            else:
                durations.append(float(get_exact_duration(a_path) or 0.0))

        subs = self.state.get("subs_data", []) or []
        durations.extend(float(s.get("end", 0.0) or 0.0) for s in subs)

        if self.state.get("music_path"):
            music_target = float(self.state.get("music_match_duration", 0.0) or 0.0)
            if music_target <= 0:
                music_target = float(self.state.get("music_dur", 0.0) or 0.0)
            if music_target <= 0:
                music_target = get_exact_duration(self.state.get("music_path")) or 0.0
            if music_target > 0:
                durations.append(music_target)

        content_dur = max(durations) if durations else 0.0
        self.state["content_duration"] = max(0.0, content_dur)
        render_dur = content_dur + render_tail_padding_seconds() if content_dur > 0 else 1.0
        self.state["duration"] = max(1.0, render_dur); self.update_timeline_size()

    def _content_duration(self):
        durations = []
        for clip in self.state.get("video_clips", []) or []:
            durations.append(float(clip.get("end", 0.0) or 0.0))

        a_path = self.state.get("audio_path", "")
        if a_path:
            a_trim = self.state.get("a_trim") or []
            if len(a_trim) >= 2:
                try:
                    durations.append(max(0.0, float(a_trim[1])))
                except Exception:
                    pass
            else:
                durations.append(float(get_exact_duration(a_path) or 0.0))

        durations.extend(float(s.get("end", 0.0) or 0.0) for s in self.state.get("subs_data", []) or [])

        if self.state.get("music_path"):
            music_target = float(self.state.get("music_match_duration", 0.0) or 0.0)
            if music_target <= 0:
                music_target = float(self.state.get("music_dur", 0.0) or 0.0)
            if music_target > 0:
                durations.append(music_target)

        content_dur = max(durations) if durations else float(self.state.get("content_duration", 0.0) or 0.0)
        return max(0.0, content_dur)

    def _preview_playback_duration(self):
        content_dur = self._content_duration()
        if content_dur > 0:
            return max(0.001, content_dur)
        return max(0.001, float(self.state.get("duration", 0.0) or 0.0))

    @pyqtSlot(QVideoFrame)
    def on_video_frame(self, frame):
        if frame.isValid():
            image = frame.toImage()
            if image.isNull():
                return
            self.last_video_image = image
            self._preview_frame_retry_pending = False
            self._preview_frame_retry_count = 0
            self.redraw_video_preview()

    def redraw_video_preview(self):
        if not self.last_video_image or self.last_video_image.isNull():
            if self.state.get("video_clips") and not getattr(self, "_preview_frame_retry_pending", False) and getattr(self, "_preview_frame_retry_count", 0) < 3:
                self._preview_frame_retry_pending = True
                self._preview_frame_retry_count = getattr(self, "_preview_frame_retry_count", 0) + 1
                self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
                QTimer.singleShot(260, lambda: setattr(self, "_preview_frame_retry_pending", False))
            return
        img = self.last_video_image
        w, h = self.video_label.width(), self.video_label.height()
        if w > 0 and h > 0:
            preview_scale = max(0.01, float(self.state.get("v_scale", 100) or 100) / 100.0)
            source_w = max(1, int(img.width() or self.proj_width or 1))
            source_h = max(1, int(img.height() or self.proj_height or 1))
            layer_rect = canvas_layer_rect(
                w, h,
                source_w,
                source_h,
                scale=preview_scale,
                pos_x=self.state.get("v_pos_x", 0.0),
                pos_y=self.state.get("v_pos_y", 0.0),
            )
            target_w = max(1, min(8192, layer_rect.width))
            target_h = max(1, min(8192, layer_rect.height))
            frame_key = int(img.cacheKey()) if hasattr(img, "cacheKey") else id(img)
            scale_key = (frame_key, target_w, target_h)
            if getattr(self, "_preview_scaled_pixmap_key", None) == scale_key and getattr(self, "_preview_scaled_pixmap", None) is not None:
                scaled_pix = self._preview_scaled_pixmap
            else:
                scaled_pix = QPixmap.fromImage(img).scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self._preview_scaled_pixmap_key = scale_key
                self._preview_scaled_pixmap = scaled_pix
            result_pix = QPixmap(w, h)
            result_pix.fill(Qt.GlobalColor.black)
            painter = QPainter(result_pix)
            painter.drawPixmap(layer_rect.x, layer_rect.y, scaled_pix)
            painter.end()
            if hasattr(self, "preview_workspace"):
                self.video_label.setPixmap(result_pix)
            else:
                self.video_label.setPixmap(self._apply_preview_transform_to_pixmap(result_pix))
            if not getattr(self, "_preview_overlay_has_content", False) and hasattr(self, "video_label"):
                self.video_label.raise_()

    def toggle_play(self):
        self.is_playing = not self.is_playing; self.btn_play.setText("⏸️ 暂停" if self.is_playing else "▶️ 播放")
        if self.is_playing:
            if hasattr(self, "timeline_widget"):
                self.timeline_widget.is_scrubbing = False
            duration = self._preview_playback_duration()
            if self.current_play_time >= duration - 0.03:
                self.current_play_time = 0.0
            self._play_clock_ref = time.monotonic()
            self._play_time_ref = self.current_play_time
            self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
            self._sync_audio_playback_to_time(self.current_play_time, force_seek=True)
            self._sync_music_playback_to_time(self.current_play_time, force_seek=True)
            self.player.play()
            if not hasattr(self, "play_timer"):
                self.play_timer = QTimer(self)
                self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
                self.play_timer.timeout.connect(self.play_tick)
            self.play_timer.start(30)
        else:
            self.player.pause(); self.audio_player.pause() if self._has_audio_track() else None
            self.music_player.pause() if self._has_music_track() else None
            if hasattr(self, 'play_timer'): self.play_timer.stop()

    def _on_video_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.is_playing:
            if self._playback_loop_enabled():
                duration = self._preview_playback_duration()
                if self.current_play_time >= duration - 0.12:
                    self._restart_loop_playback()
                else:
                    self._sync_video_playback_to_time(self.current_play_time, force_seek=True)
                self.player.play()
            else:
                self._stop_playback_at_end()

    def _on_audio_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.is_playing:
            duration = self._preview_playback_duration()
            if self.current_play_time < duration - 0.12:
                self.audio_player.pause()
                return
            if self._playback_loop_enabled():
                self._restart_loop_playback()
            else:
                self._stop_playback_at_end()

    def _on_music_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.is_playing:
            duration = self._preview_playback_duration()
            if self.current_play_time < duration - 0.12:
                if bool(self.state.get("music_loop", True)):
                    self._sync_music_playback_to_time(self.current_play_time, force_seek=True)
                    self.music_player.play()
                else:
                    self.music_player.pause()
                return
            if self._playback_loop_enabled():
                self._sync_music_playback_to_time(self.current_play_time, force_seek=True)
                self.music_player.play()
            else:
                self._stop_playback_at_end()

    def _playback_loop_enabled(self):
        return not hasattr(self, "chk_loop_playback") or self.chk_loop_playback.isChecked()

    def _restart_loop_playback(self):
        self.current_play_time = 0.0
        self._play_clock_ref = time.monotonic()
        self._play_time_ref = 0.0
        self._sync_video_playback_to_time(0.0, force_seek=True)
        if self.is_playing:
            self.player.play()
        self._sync_audio_playback_to_time(0.0, force_seek=True)
        self._sync_music_playback_to_time(0.0, force_seek=True)
        self._update_time_label()
        self.timeline_widget.update_playhead(0.0)
        self.update_floating_subtitle()
        self._update_workspace_status()

    def _stop_playback_at_end(self):
        self.is_playing = False
        self.current_play_time = self._preview_playback_duration()
        self.btn_play.setText("â–¶ï¸ æ’­æ”¾")
        self.player.pause()
        if self._has_audio_track():
            self.audio_player.pause()
        if self._has_music_track():
            self.music_player.pause()
        if hasattr(self, "play_timer"):
            self.play_timer.stop()
        self._update_time_label()

    def _video_clip_for_time(self, time_sec):
        clips = self.state.get("video_clips", [])
        if not clips:
            return -1, None
        for idx, clip in enumerate(clips):
            start = float(clip.get("start", 0.0) or 0.0)
            end = float(clip.get("end", start) or start)
            if start <= time_sec <= end:
                return idx, clip
        if time_sec < float(clips[0].get("start", 0.0) or 0.0):
            return 0, clips[0]
        return len(clips) - 1, clips[-1]

    def _video_local_time(self, clip, time_sec):
        if not clip:
            return 0.0
        start = float(clip.get("start", 0.0) or 0.0)
        source_in = float(clip.get("source_in", 0.0) or 0.0)
        source_out = float(clip.get("source_out", clip.get("dur", 0.0)) or clip.get("dur", 0.0) or 0.0)
        source_len = max(0.001, source_out - source_in)
        offset = max(0.0, float(time_sec or 0.0) - start)
        if offset > source_len and float(clip.get("end", start) or start) - start > source_len:
            offset = offset % source_len
        return max(0.0, min(source_out, source_in + offset))

    def _sync_video_playback_to_time(self, time_sec, force_seek=False):
        idx, clip = self._video_clip_for_time(float(time_sec or 0.0))
        if not clip:
            return
        if not self.is_playing:
            self._queue_preview_proxy_for_clip(clip)
        path = self._preview_media_path_for_clip(clip)
        if not path:
            return
        current_path = self.player.source().toLocalFile()
        source_changed = current_path != path
        if source_changed:
            self.last_video_image = None
            self._preview_frame_retry_count = 0
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
        local_time = self._video_local_time(clip, time_sec)
        player_time = self.player.position() / 1000.0
        drift_limit = 0.75 if self.is_playing else 0.28
        if force_seek or source_changed or abs(player_time - local_time) > drift_limit:
            self.player.setPosition(int(local_time * 1000))
        if self.is_playing and source_changed:
            self.player.play()
        self._playback_v_idx = idx

    def _has_music_track(self):
        path = self.state.get("music_path", "")
        return bool(path and os.path.exists(path) and hasattr(self, "music_player"))

    def _has_audio_track(self):
        path = self.state.get("audio_path", "")
        return bool(path and os.path.exists(path) and hasattr(self, "audio_player"))

    def _audio_timeline_range(self):
        a_trim = self.state.get("a_trim") or []
        try:
            start = float(a_trim[0] or 0.0) if len(a_trim) >= 1 else 0.0
        except Exception:
            start = 0.0
        try:
            end = float(a_trim[1] or start) if len(a_trim) >= 2 else start
        except Exception:
            end = start
        if end <= start:
            dur = float(get_exact_duration(self.state.get("audio_path", "")) or 0.0)
            if dur > 0:
                end = start + dur
        return max(0.0, start), max(start, end)

    def _sync_audio_playback_to_time(self, time_sec, force_seek=False):
        if not self._has_audio_track():
            return False
        path = self.state.get("audio_path", "")
        time_sec = max(0.0, float(time_sec or 0.0))
        clip_start, clip_end = self._audio_timeline_range()
        if time_sec < clip_start or time_sec >= clip_end:
            self.audio_player.pause()
            return False
        current_path = self.audio_player.source().toLocalFile()
        source_changed = current_path != path
        if source_changed:
            self.audio_player.setSource(QUrl.fromLocalFile(path))
        try:
            source_in = max(0.0, float(self.state.get("audio_source_in", 0.0) or 0.0))
        except Exception:
            source_in = 0.0
        local_time = source_in + max(0.0, time_sec - clip_start)
        player_time = self.audio_player.position() / 1000.0
        if force_seek or source_changed or abs(player_time - local_time) > 0.25:
            self.audio_player.setPosition(int(local_time * 1000))
        if self.is_playing and self.audio_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.play()
        return True
    def _music_source_duration(self):
        path = self.state.get("music_path", "")
        dur = float(self.state.get("music_dur", 0.0) or 0.0)
        if dur <= 0 and path and os.path.exists(path):
            dur = float(get_exact_duration(path) or 0.0)
            if dur > 0:
                self.state["music_dur"] = dur
        return max(0.0, dur)

    def _music_local_time(self, time_sec):
        time_sec = max(0.0, float(time_sec or 0.0))
        dur = self._music_source_duration()
        if dur <= 0:
            return time_sec
        if bool(self.state.get("music_loop", True)):
            return time_sec % dur
        return min(time_sec, dur)

    def _sync_music_playback_to_time(self, time_sec, force_seek=False):
        if not self._has_music_track():
            return False
        time_sec = max(0.0, float(time_sec or 0.0))
        music_end = float(self.state.get("music_match_duration", 0.0) or 0.0)
        if music_end <= 0:
            music_end = float(self.state.get("music_dur", 0.0) or 0.0)
        if music_end <= 0:
            music_end = float(self.state.get("duration", 0.0) or 0.0)
        if music_end > 0 and time_sec >= music_end:
            self.music_player.pause()
            return False
        path = self.state.get("music_path", "")
        current_path = self.music_player.source().toLocalFile()
        source_changed = current_path != path
        if source_changed:
            self.music_player.setSource(QUrl.fromLocalFile(path))
        self.music_player.setLoops(QMediaPlayer.Loops.Infinite if bool(self.state.get("music_loop", True)) else 1)
        if hasattr(self, "music_output"):
            self.music_output.setVolume(float(self.state.get("music_volume", 35) or 35) / 100.0)
        local_time = self._music_local_time(time_sec)
        player_time = self.music_player.position() / 1000.0
        if force_seek or source_changed or abs(player_time - local_time) > 0.28:
            self.music_player.setPosition(int(local_time * 1000))
        if self.is_playing and self.music_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.music_player.play()
        return True
    def play_tick(self):
        if getattr(self.timeline_widget, "is_scrubbing", False):
            try:
                if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                    return
                self.timeline_widget.is_scrubbing = False
            except Exception:
                return
        ref_clock = getattr(self, "_play_clock_ref", time.monotonic())
        ref_time = getattr(self, "_play_time_ref", self.current_play_time)
        real_time = ref_time + max(0.0, time.monotonic() - ref_clock)
        duration = self._preview_playback_duration()
        if real_time >= duration - 0.02:
            if self._playback_loop_enabled():
                self._restart_loop_playback()
                real_time = 0.0
            else:
                self._stop_playback_at_end()
                return
        self.current_play_time = real_time
        self._sync_video_playback_to_time(real_time, force_seek=False)
        self._sync_audio_playback_to_time(real_time, force_seek=False)
        self._sync_music_playback_to_time(real_time, force_seek=False)
        now = time.monotonic()
        if self.state.get("video_clips") and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            if now - getattr(self, "_last_video_play_retry_at", 0.0) >= 0.5:
                self._last_video_play_retry_at = now
                self.player.play()
        self._update_time_label()
        self.timeline_widget.update_playhead(real_time)
        self.update_floating_subtitle()
        if now - getattr(self, "_last_play_status_update_at", 0.0) >= 0.25:
            self._last_play_status_update_at = now
            self._update_workspace_status()

    def sync_player_to_time(self, time_sec):
        self.current_play_time = time_sec
        self._play_clock_ref = time.monotonic()
        self._play_time_ref = float(time_sec or 0.0)
        clips = self.state.get("video_clips", [])
        if clips:
            self._sync_video_playback_to_time(time_sec, force_seek=True)
        else:
            self.player.setPosition(int(time_sec * 1000))
        self._sync_audio_playback_to_time(time_sec, force_seek=True)
        self._sync_music_playback_to_time(time_sec, force_seek=True)
        self._update_time_label()
        self.timeline_widget.update_playhead(time_sec); self.update_floating_subtitle(); self._update_workspace_status()

    def update_floating_subtitle(self):
        active_subs = []
        for i, s in enumerate(self.state["subs_data"]):
            if float(s.get('start', 0)) <= self.current_play_time <= float(s.get('end', 1)):
                htmlText = render_subtitle_html(s, self.current_play_time, self.proj_width, self.proj_height)
                active_subs.append({
                    "idx": i, "htmlText": htmlText, "isNew": (i not in self.active_subs_cache),
                    "pos_x": s.get("pos_x", 0.0), "pos_y": s.get("pos_y", 25.0),
                    "box_width": s.get("style", {}).get("box_width", 0),
                    "track": s.get("track", 1), "isSelected": (i == self.current_selected_idx)
                })
        parent = self.parent()
        project_data = getattr(parent, "project", None) if parent else None
        if not isinstance(project_data, dict):
            project_data = self.project_data if isinstance(self.project_data, dict) else {}
        design_state = project_data.get("room_state", {}).get("design_room", {}) if isinstance(project_data, dict) else {}
        design_html = render_design_html(design_state, self.current_play_time, self.proj_width, self.proj_height)
        signature_html = render_signature_html(self.state.get("signature"), self.current_play_time, self.proj_width, self.proj_height)
        overlay_has_content = bool(active_subs or design_html.strip() or signature_html.strip())
        current_hash = hash(json.dumps({"subs": active_subs, "signature": signature_html, "design": design_html}, sort_keys=True))
        if current_hash != getattr(self, 'last_render_hash', None):
            json_str = json.dumps(active_subs)
            safe_json = json_str.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
            safe_sig = signature_html.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
            safe_design = design_html.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
            if overlay_has_content:
                self._set_preview_overlay_visible(True)
            self.browser.page().runJavaScript(f"if(typeof syncDesign === 'function') syncDesign(`{safe_design}`); if(typeof syncSubs === 'function') syncSubs(`{safe_json}`); if(typeof syncSignature === 'function') syncSignature(`{safe_sig}`);")
            if not overlay_has_content:
                QTimer.singleShot(0, lambda: self._set_preview_overlay_visible(False))
            self.last_render_hash = current_hash; self.active_subs_cache = set([sub["idx"] for sub in active_subs])
        elif overlay_has_content != getattr(self, "_preview_overlay_has_content", False) or (not overlay_has_content and hasattr(self, "browser") and self.browser.isVisible()):
            self._set_preview_overlay_visible(overlay_has_content)

    def sanitize_subs_data(self, data):
        def_x = self.state.get("default_pos_x", 0.0)
        def_y = self.state.get("default_pos_y", 25.0)
        def_style = copy.deepcopy(self.default_style)
        if isinstance(self.state.get("default_style"), dict):
            def_style.update(self.state.get("default_style", {}))
        self.state["default_style"] = def_style

        for s in data:
            s["track"] = s.get("track", 1)
            if "pos_x" not in s: s["pos_x"] = def_x
            else: s["pos_x"] = float(s["pos_x"])
            if "pos_y" not in s: s["pos_y"] = def_y
            else: s["pos_y"] = float(s["pos_y"])

            if "style" not in s: s["style"] = {}

            for k, v in def_style.items():
                if k in s and k not in ["track", "pos_x", "pos_y", "words", "text", "start", "end", "style"]:
                    s["style"][k] = s.pop(k)
                elif k not in s["style"]:
                    s["style"][k] = v
            s.setdefault("words", [{"text": s.get('text', ''), "start": s.get('start', 0.0), "end": s.get('end', 1.0)}])
            fixed_words = []
            for w in s.get("words", []):
                text = str(w.get("text") or w.get("word") or "").strip()
                if not text:
                    continue
                fixed_words.append({
                    "text": text,
                    "start": float(w.get("start", s.get("start", 0.0))),
                    "end": float(w.get("end", s.get("end", 1.0)))
                })
            if fixed_words:
                s["words"] = fixed_words
                if not str(s.get("text", "")).strip():
                    s["text"] = " ".join(w["text"] for w in fixed_words).replace(" \n", "\n").replace("\n ", "\n")
        return data

    @pyqtSlot(str)
    def _on_ai_progress(self, msg): self.status_lbl.setText(msg)

    @pyqtSlot()
    def _on_ai_success(self):
        self.update_timeline_size(); self.render_ui_list(); self.status_lbl.setText("✅ 打轴完毕！"); self.auto_save_cache()
        self.push_history()
        QMessageBox.information(self, "🎉 成功", "AI 听译打轴完美完成！\n已自动为您生成所有字幕片段！")

    @pyqtSlot(str)
    def _on_ai_error(self, msg):
        self.status_lbl.setText("❌ 打轴失败"); QMessageBox.critical(self, "AI 听译失败", f"提取失败！原因如下：\n\n{msg}")

    @pyqtSlot()
    def _on_ai_finish(self):
        self.btn_extract.setEnabled(True)

    # 👑 NLP 文本清洗引擎强化版：彻底解决标点符号与空格粘连
    # 👑 NLP 文本清洗引擎强化版：彻底解决标点符号与空格粘连
    def _clean_and_format_user_text(self, raw_text):
        text = raw_text
        # 1. 拆开被粘连的驼峰词 (ThankYou -> Thank You)
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)

        # 2. 去掉标点前面的多余空格
        text = re.sub(r'\s+([.,!?;:])', r'\1', text)

        # 3. 👑 核心修复：在标点后面强制加空格（完美兼容带着双引号的标点，如 come."Some -> come." Some）
        text = re.sub(r'([.,!?;:]["”\']?)([A-Za-z0-9])', r'\1 \2', text)

        # 4. 清理连续的多余空格
        text = re.sub(r'[ \t]+', ' ', text)

        # 5. 智能首字母大写规则
        sentences = re.split(r'([.!?]["”\']?\s+)', text)
        cleaned_sentences = []
        for s in sentences:
            if len(s) > 0 and s[0].islower():
                cleaned_sentences.append(s[0].upper() + s[1:])
            else:
                cleaned_sentences.append(s)
        return normalize_scripture_quote_text("".join(cleaned_sentences).strip())

    def _tokenize_user_text_for_alignment(self, raw_text):
        return tokenize_display_text(raw_text)

    # 👑 智能对齐引擎：将 AI 时间戳与手工文案强行绑定
    def _align_user_text_to_ai_words(self, ai_words, raw_text, fallback_start=None, fallback_end=None):
        return align_reference_text_to_timestamps(
            ai_words,
            raw_text,
            fallback_start=fallback_start,
            fallback_end=fallback_end,
        )

    def _selected_ai_transcription_provider_order(self):
        combo = getattr(self, "ai_transcription_provider_combo", None)
        if combo is None:
            return None
        data = combo.currentData()
        return data if data else None

    def start_extract(self):
        try:
            cmd = get_ffmpeg_cmd()
            try:
                flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run([cmd, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            except Exception:
                QMessageBox.warning(self, "引擎缺失", "尚未检测到核心引擎 (FFmpeg)！\n可能是下载被拦截或未完成。\n\n系统将再次尝试呼叫云端部署。您也可以手动将 ffmpeg.exe 放入软件目录。")
                self.check_and_download_ffmpeg()
                return

            v_clips = self.state.get("video_clips", [])
            a_path = self.state.get("audio_path", "")
            target_path = a_path if a_path else (v_clips[0]["path"] if v_clips else "")
            if not target_path: return QMessageBox.warning(self, "提示", "请先导入画面或者配音！")

            c_mode = self.chunk_mode.currentText()
            timing_mode = self.timing_mode.currentText()
            # 读取用户输入的手工文案
            custom_text = self.text_editor.toPlainText().strip()

            self.btn_extract.setEnabled(False)
            self.status_lbl.setText("⏳ 准备听译环境...")
            provider_order = self._selected_ai_transcription_provider_order()
            threading.Thread(target=self.extract_task, args=(target_path, c_mode, timing_mode, custom_text, provider_order), daemon=True).start()
        except Exception as e: QMessageBox.critical(self, "错误", f"启动提取失败: {str(e)}")

    def extract_task(self, target_path, c_mode, timing_mode, custom_text, provider_order=None):
        temp_audio = None
        try:
            # 👑 终极修复：不再自作聪明判断大小，强制把所有素材压制成“极限微缩版 mp3”！
            self.sig_ai_progress.emit("⏳ 正在提取云端专用微缩音频...")
            temp_audio = os.path.join(tempfile.gettempdir(), "sh_ai_temp.mp3")

            # 强制 16kHz 采样率、单声道、极低码率 (16k)。保证 1 小时音频也才几 MB，永不触发 413 报错！
            cmd = [get_ffmpeg_cmd(), "-y", "-i", target_path, "-vn", "-map", "a:0?", "-ar", "16000", "-ac", "1", "-b:a", "16k", temp_audio]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000 if os.name == 'nt' else 0)

            if os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 100:
                target_path = temp_audio
            else:
                raise Exception("音频抽取失败！可能素材无声音或格式不支持。")

            # 读取极限压缩后的文件，此时 30秒的音频绝对只有不到 100KB！
            media_duration = get_exact_duration(target_path)
            fallback_end = media_duration if media_duration > 0 else None

            clean_words = normalize_word_timestamps(
                transcribe_audio_words(
                    target_path,
                    progress=lambda msg, color="#cdd6f4": self.sig_ai_progress.emit(msg),
                    provider_order=provider_order,
                ),
                fallback_start=0.0,
                fallback_end=fallback_end,
            )

            # 👑 如果用户输入了手工文案，触发清洗和强行对齐
            if custom_text:
                self.sig_ai_progress.emit("⏳ 正在用 NLP 算法清洗并对齐您的手工文案...")
                cleaned_text = self._clean_and_format_user_text(custom_text)
                clean_words = self._align_user_text_to_ai_words(
                    clean_words,
                    cleaned_text,
                    fallback_start=0.0,
                    fallback_end=fallback_end,
                )

            generated_subs = self.sanitize_subs_data(self.process_words(clean_words, c_mode, timing_mode))
            self.state["subs_data"], _ = rebalance_subtitle_layout(
                generated_subs,
                fallback_style=self.default_style,
                default_pos=(self.state.get("default_pos_x", 0.0), self.state.get("default_pos_y", 25.0)),
                proj_w=self.proj_width,
                force_standard_box=True
            )

            if self.state["subs_data"]: self.state["duration"] = max(self.state["duration"], self.state["subs_data"][-1]["end"])
            self.sig_ai_success.emit()

        except Exception as e: self.sig_ai_error.emit(str(e))
        finally:
            if temp_audio and os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except: pass
            self.sig_ai_finish.emit()

    # 👑 商业级字幕断句引擎：引入【语义保护胶水】与【静音停顿检测】
    def process_words(self, words, mode, timing_mode=None):
        words = normalize_word_timestamps(words)
        subs = []
        curr = {"words": []}
        # ⚠️ 关键修改：去掉了冒号 ':'，防止 31:25 被错误切断！
        puncts = ['.', '!', '?', ',', '，', '。', '！', '？', ';']
        timing_mode = timing_mode or self.state.get("timing_mode", "J Cut (字幕稍后收尾)")
        sound_aligned = "对齐声音" in timing_mode
        narrative_min_words, narrative_max_words = narrative_chunk_word_bounds(mode)
        narrative_merge_words = narrative_chunk_merge_words(mode)
        fixed_count = fixed_word_count_for_chunk_mode(mode)
        exact_single_word = is_exact_single_word_chunk_mode(mode)
        precise_chunk_mode = exact_single_word or fixed_count > 0

        for i, w in enumerate(words):
            if not curr["words"]:
                curr["start"] = w["start"]

            curr["words"].append({"text": w["word"], "start": w["start"], "end": w["end"]})
            curr["end"] = w["end"]

            has_punct = any(w["word"].endswith(p) for p in puncts)
            w_len = len(curr["words"])
            curr_dur = curr["end"] - curr["start"]

            next_word = words[i+1]["word"] if i + 1 < len(words) else ""
            next_start = words[i+1]["start"] if i + 1 < len(words) else 9999.0

            # 🔇 停顿检测：如果说话停顿超过 0.8 秒，强制断开
            silence_gap = next_start - curr["end"]
            force_break = silence_gap > 0.8

            is_break = False

            narrative_block = narrative_max_words > 0
            tiktok_smart = "智能听译" in mode or "4-7词" in mode or "4-7" in mode
            smart_short = "智能重点" in mode or "3-4词为主" in mode
            natural_short = "自然短句" in mode or "1-4" in mode
            clean_curr = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff']", "", str(w.get("word", ""))).lower()
            weak_words = {
                "i", "you", "he", "she", "we", "they", "a", "an", "the", "to", "of", "in", "on",
                "for", "and", "or", "but", "is", "am", "are", "was", "were", "be", "been", "do",
                "does", "did", "not", "would", "could", "should", "have", "has", "had", "it",
                "my", "your", "his", "her", "their", "our"
            }
            is_key_word = bool(clean_curr) and clean_curr not in weak_words and (
                len(clean_curr) >= 7 or clean_curr in FAITH_WORDS or clean_curr.isupper()
            )

            if exact_single_word:
                is_break = True
            elif fixed_count:
                is_break = w_len >= fixed_count or force_break
            elif narrative_block:
                narrative_hard_gap_min = max(6, narrative_min_words - 2)
                narrative_key_min = max(narrative_min_words + 2, narrative_max_words - 2)
                narrative_key_dur = 3.2 if narrative_max_words >= 18 else 2.6
                if force_break and w_len >= narrative_hard_gap_min:
                    is_break = True
                elif has_punct and w_len >= narrative_min_words:
                    is_break = True
                elif silence_gap > 0.42 and w_len >= narrative_min_words:
                    is_break = True
                elif is_key_word and w_len >= narrative_key_min and (silence_gap > 0.16 or curr_dur > narrative_key_dur):
                    is_break = True
                elif w_len >= narrative_max_words:
                    is_break = True
            elif tiktok_smart:
                if force_break:
                    is_break = True
                elif has_punct and w_len >= 4:
                    is_break = True
                elif silence_gap > 0.46 and w_len >= 3:
                    is_break = True
                elif silence_gap > 0.28 and w_len >= 4:
                    is_break = True
                elif is_key_word and w_len >= 5 and (silence_gap > 0.14 or curr_dur > 1.55):
                    is_break = True
                elif w_len >= 6:
                    is_break = True
                elif w_len >= 5 and curr_dur > 2.05:
                    is_break = True
            elif smart_short:
                long_slot = (len(subs) + int(float(curr.get("start", 0.0)) * 10)) % 5 == 3
                if force_break:
                    is_break = True
                elif has_punct and w_len >= 1:
                    is_break = True
                elif is_key_word and w_len >= 4:
                    is_break = True
                elif silence_gap > 0.42 and w_len >= 1 and is_key_word:
                    is_break = True
                elif silence_gap > 0.28 and w_len >= 2:
                    is_break = True
                elif w_len >= 6:
                    is_break = True
                elif w_len >= 4 and (not long_slot or silence_gap > 0.16 or curr_dur > 1.80):
                    is_break = True
                elif w_len >= 3 and curr_dur > 1.45:
                    is_break = True
            elif natural_short:
                if force_break:
                    is_break = True
                elif has_punct and w_len >= 1:
                    is_break = True
                elif silence_gap > 0.30 and w_len >= 2:
                    is_break = True
                elif w_len >= 4:
                    is_break = True
                elif w_len >= 3 and curr_dur > 1.35:
                    is_break = True
            elif sound_aligned:
                soft_gap = silence_gap > 0.34
                hard_gap = silence_gap > 0.55
                if hard_gap and curr_dur >= 0.25:
                    is_break = True
                elif soft_gap and w_len >= 2:
                    is_break = True
                elif has_punct and silence_gap > 0.18 and curr_dur > 0.75:
                    is_break = True
                elif curr_dur >= 3.8 or w_len >= 13:
                    is_break = True
            elif "双行" in mode:
                if force_break: is_break = True
                elif has_punct and curr_dur > 1.2: is_break = True
                elif w_len >= 12: is_break = True
                elif w_len >= 8 and curr_dur > 2.5: is_break = True
            else: # 短句模式
                if force_break: is_break = True
                elif has_punct and curr_dur > 0.8: is_break = True
                elif w_len >= 6: is_break = True
                elif w_len >= 3 and curr_dur > 1.5: is_break = True

            # 👑 【核心新增】：语义强力保护胶水 (Semantic Glue)
            if next_word:
                # 规则 1：如果下一个词是纯数字或冒号开头 (如 "25", ":25", "31")，绝不断开！
                if re.match(r'^[:\d]', next_word):
                    is_break = False

                # 规则 2：如果当前词是经文书卷名，后面通常跟数字，绝不断开！
                curr_clean = re.sub(r'[^a-zA-Z]', '', w["word"]).lower()
                bible_books = {"proverbs", "psalm", "psalms", "matthew", "mark", "luke", "john", "genesis", "exodus", "romans", "corinthians", "chapter", "verse"}
                if curr_clean in bible_books:
                    is_break = False

                # 规则 3：如果当前词本身以冒号结尾 (例如 "31:")，绝不断开！
                if w["word"].endswith(":"):
                    is_break = False

                if not precise_chunk_mode and is_break and should_defer_subtitle_break_for_readability(
                    w.get("word", ""),
                    next_word,
                    segment_word_count=w_len,
                    silence_gap=silence_gap,
                    has_punct=has_punct,
                    is_last_word=(i == len(words) - 1),
                ):
                    is_break = False

            if is_break:
                if ("双行" in mode or sound_aligned) and w_len >= 6:
                    mid = w_len // 2
                    curr["words"][mid]["text"] = "\n" + curr["words"][mid]["text"].lstrip()

                raw_text = " ".join([x["text"] for x in curr["words"]])
                curr["text"] = format_subtitle_text_spacing(raw_text)

                subs.append(curr)
                curr = {"words": [], "track": 1}

        if curr["words"]:
            if sound_aligned and len(curr["words"]) >= 6:
                mid = len(curr["words"]) // 2
                curr["words"][mid]["text"] = "\n" + curr["words"][mid]["text"].lstrip()
            raw_text = " ".join([x["text"] for x in curr["words"]])
            curr["text"] = format_subtitle_text_spacing(raw_text)
            subs.append(curr)

        if not precise_chunk_mode and (narrative_block or "双行" in mode or "长句" in mode or "约10" in mode):
            subs = merge_single_word_subtitle_segments(subs, max_merged_words=narrative_merge_words if narrative_block else 18)

        subs = self._apply_timing_mode(subs, timing_mode)
        if self.state.get("fill_subtitle_gaps", True):
            subs = self._fill_subtitle_gaps(subs)
        pacing_merge_words = pacing_merge_word_limit_for_chunk_mode(mode)
        subs = protect_fast_subtitle_pacing(
            subs,
            allow_merge=pacing_merge_words > 0,
            max_merged_words=pacing_merge_words or 1,
        )
        return subs

    def _fill_subtitle_gaps(self, subs, max_fill=1.20, min_gap=0.05):
        if not subs:
            return subs
        ordered = sorted(
            [s for s in subs if isinstance(s, dict)],
            key=lambda s: (int(s.get("track", 1)), float(s.get("start", 0.0)), float(s.get("end", 0.0)))
        )
        for idx, sub in enumerate(ordered[:-1]):
            next_sub = ordered[idx + 1]
            if int(sub.get("track", 1)) != int(next_sub.get("track", 1)):
                continue
            end = float(sub.get("end", float(sub.get("start", 0.0)) + 0.05))
            next_start = float(next_sub.get("start", end))
            gap = next_start - end
            if gap <= min_gap:
                continue
            sub["end"] = min(next_start - 0.01, end + max_fill)
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

    def render_ui_list(self):
        for i in reversed(range(self.scroll_layout.count())):
            item = self.scroll_layout.itemAt(i); item.widget().deleteLater() if item.widget() else None
        self.ui_entries.clear()

        self.scroll_layout.setSpacing(10)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)

        for i, s in enumerate(self.state["subs_data"]):
            start_t = float(s['start'])
            end_t = float(s['end'])

            card = QFrame()
            card.setStyleSheet("QFrame { background-color: #1e1e2e; border: 1px solid #313244; border-radius: 6px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(6)

            top_row = QHBoxLayout()
            top_row.setContentsMargins(0, 0, 0, 0)

            btn = QPushButton(f"▶ {start_t:.1f}s")
            btn.setFixedWidth(65)
            btn.setStyleSheet("QPushButton { background-color: #313244; color: #a6e3a1; font-weight: bold; border-radius: 3px; padding: 4px; border: none; } QPushButton:hover { background-color: #45475a; }")
            btn.clicked.connect(lambda _, idx=i: self.sync_player_to_time(float(self.state["subs_data"][idx]["start"])))

            lbl_start = QLabel("起:")
            lbl_start.setStyleSheet("color: #a6adc8; border: none; background: transparent;")
            start_spin = ProScrubDoubleSpinBox()
            start_spin.setRange(0, 36000); start_spin.setSingleStep(0.1); start_spin.setDecimals(1); start_spin.setLocale(self.eng_locale)
            start_spin.setValue(start_t)
            start_spin.setStyleSheet("QDoubleSpinBox { background: #11111b; color: #a6e3a1; font-weight: bold; border: 1px solid #313244; border-radius: 3px; padding: 2px 4px; }")
            start_spin.setFixedWidth(65)

            lbl_end = QLabel("终:")
            lbl_end.setStyleSheet("color: #a6adc8; border: none; background: transparent;")
            end_spin = ProScrubDoubleSpinBox()
            end_spin.setRange(0, 36000); end_spin.setSingleStep(0.1); end_spin.setDecimals(1); end_spin.setLocale(self.eng_locale)
            end_spin.setValue(end_t)
            end_spin.setStyleSheet("QDoubleSpinBox { background: #11111b; color: #f38ba8; font-weight: bold; border: 1px solid #313244; border-radius: 3px; padding: 2px 4px; }")
            end_spin.setFixedWidth(65)

            top_row.addWidget(btn)
            top_row.addSpacing(10)
            top_row.addWidget(lbl_start)
            top_row.addWidget(start_spin)
            top_row.addSpacing(5)
            top_row.addWidget(lbl_end)
            top_row.addWidget(end_spin)
            top_row.addStretch()

            entry = QTextEdit(s["text"])
            entry.setFixedHeight(48)
            entry.setStyleSheet("QTextEdit { background-color: #11111b; color: #cdd6f4; border: 1px solid #313244; border-radius: 4px; font-size: 13px; padding: 4px; }")

            start_spin.valueChanged.connect(lambda val, idx=i: self.sync_time_from_list(idx, val, None))
            end_spin.valueChanged.connect(lambda val, idx=i: self.sync_time_from_list(idx, None, val))
            entry.textChanged.connect(lambda idx=i, w=entry: self.sync_text_edit(idx, w.toPlainText()))
            card_layout.addLayout(top_row)
            card_layout.addWidget(entry)

            self.scroll_layout.addWidget(card)
            self.ui_entries.append({"ui": entry, "start_spin": start_spin, "end_spin": end_spin, "btn": btn})
        if hasattr(self, "_theme_colors"):
            apply_tinted_styles(self.scroll_content, self._theme_colors)

    def auto_save_cache(self):
        try:
            if getattr(self, 'current_selected_idx', -1) != -1 and self.state.get("subs_data"):
                try:
                    curr_clip = self.state["subs_data"][self.current_selected_idx]
                    self.state["default_pos_x"] = curr_clip.get("pos_x", 0.0)
                    self.state["default_pos_y"] = curr_clip.get("pos_y", 25.0)
                    self.state["default_style"] = curr_clip.get("style", self.default_style).copy()
                except IndexError:
                    pass

            parent = self.parent_window()
            if parent and hasattr(parent, "is_auto_save_enabled") and not parent.is_auto_save_enabled():
                return

            write_json_file(CACHE_FILE, self.state, indent=2)
            if hasattr(self, "project_autosave_timer"):
                self.project_autosave_timer.start(1200)
        except Exception as e:
            pass

    def flush_project_autosave(self):
        if self.project_autosave_busy:
            return
        parent = self.parent_window()
        if parent and hasattr(parent, "is_auto_save_enabled") and not parent.is_auto_save_enabled():
            return
        project_data = getattr(parent, "project", None) if parent else None
        project_data = project_data or self.project_data
        if not isinstance(project_data, dict) or not project_data.get("project_path"):
            return
        self.project_autosave_busy = True
        try:
            project_data = update_room_state(project_data, "edit_room", self.state)
            self.project_data = project_data
            if parent and hasattr(parent, "project"):
                parent.project = project_data
                if hasattr(parent, "refresh_room_links"):
                    parent.refresh_room_links()
        except Exception:
            pass
        finally:
            self.project_autosave_busy = False

    def load_project_on_boot(self):
        self.refresh_project_header()
        room_state = {}
        if isinstance(self.project_data, dict):
            room_state = self.project_data.get("room_state", {}).get("edit_room", {})
        self.ensure_design_default_is_blank()
        self.sync_design_panel_controls()
        if room_state:
            try:
                room_state = dict(room_state)
                room_state["subs_data"] = self.sanitize_subs_data(room_state.get("subs_data", []))
                merged_default_style = copy.deepcopy(self.default_style)
                if isinstance(room_state.get("default_style"), dict):
                    merged_default_style.update(room_state.get("default_style", {}))
                room_state["default_style"] = merged_default_style
                self.state.update(room_state)
                self.refresh_project_header()
                self.default_style.update(merged_default_style)
                self.state["signature"] = normalize_signature_config(self.state.get("signature"), self.default_style)
                self.sync_signature_controls()
                self.last_render_hash = None
                self.active_subs_cache = set()
                self.v_scale_spin.setValue(self.state.get("v_scale", 100))
                self.v_vol_spin.setValue(self.state.get("v_volume", 100))
                self.a_vol_spin.setValue(self.state.get("a_volume", 100))
                self.music_vol_spin.setValue(self.state.get("music_volume", 35))
                chunk_value = self.state.get("chunk_mode", "双行大段 (约10字，智能折行)")
                timing_value = self.state.get("timing_mode", "J Cut (字幕稍后收尾)")
                if "对齐声音" in chunk_value:
                    chunk_value = "双行大段 (约10字，智能折行)"
                    timing_value = "对齐声音 (按停顿)"
                self.chunk_mode.blockSignals(True)
                self.chunk_mode.setCurrentText(chunk_value)
                self.chunk_mode.blockSignals(False)
                self.timing_mode.blockSignals(True)
                self.timing_mode.setCurrentText(timing_value)
                self.timing_mode.blockSignals(False)
                self.chk_fill_gaps.blockSignals(True)
                self.chk_fill_gaps.setChecked(bool(self.state.get("fill_subtitle_gaps", True)))
                self.chk_fill_gaps.blockSignals(False)
                self.text_editor.blockSignals(True)
                self.text_editor.setPlainText(self.state.get("custom_text", ""))
                self.text_editor.blockSignals(False)
                clips = self.state.get("video_clips", [])
                if clips:
                    self.btn_v.setText("✅ 已导原素材")
                    self._prepare_preview_proxies_for_clips(clips, announce=True)
                    self._prime_video_preview_source(clips[0], announce=True)
                    self.on_resolution_changed(self.state.get("resolution", get_output_resolution()))
                    self.generate_waveform(clips[0]["path"], "v_wave_pixmap", max_seconds=90)
                    threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
                if self.state.get("audio_path") and os.path.exists(self.state.get("audio_path")):
                    self.btn_a.setText("✅ " + os.path.basename(self.state.get("audio_path"))[:15])
                    self.audio_player.setSource(QUrl.fromLocalFile(self.state.get("audio_path")))
                    self.generate_waveform(self.state.get("audio_path"), "a_wave_pixmap")
                if self.state.get("music_path") and os.path.exists(self.state.get("music_path")) and hasattr(self, "btn_music"):
                    self.btn_music.setText("✅ " + os.path.basename(self.state.get("music_path"))[:15])
                    self.music_player.setSource(QUrl.fromLocalFile(self.state.get("music_path")))
                    self.music_player.setLoops(QMediaPlayer.Loops.Infinite)
                self.render_ui_list()
                self.refresh_media_pool()
                self.switch_inspector("empty")
                self.push_history() # 初始化历史栈
                QTimer.singleShot(500, self._sync_duration_after_cache)
                return
            except Exception:
                pass
        self.load_cache_on_boot()

    def load_cache_on_boot(self):
        if not os.path.exists(CACHE_FILE): return
        try:
            cached = read_json_file(CACHE_FILE, default={})
            if not isinstance(cached, dict):
                return
            cached["subs_data"] = self.sanitize_subs_data(cached.get("subs_data", []))
            merged_default_style = copy.deepcopy(self.default_style)
            if isinstance(cached.get("default_style"), dict):
                merged_default_style.update(cached.get("default_style", {}))
            cached["default_style"] = merged_default_style
            self.state.update(cached); self.default_style.update(merged_default_style); self.state["signature"] = normalize_signature_config(self.state.get("signature"), self.default_style); self.sync_signature_controls(); self.last_render_hash = None; self.active_subs_cache = set(); self.v_scale_spin.setValue(self.state.get("v_scale", 100)); self.v_vol_spin.setValue(self.state.get("v_volume", 100)); self.a_vol_spin.setValue(self.state.get("a_volume", 100)); self.music_vol_spin.setValue(self.state.get("music_volume", 35))
            chunk_value = self.state.get("chunk_mode", "双行大段 (约10字，智能折行)")
            timing_value = self.state.get("timing_mode", "J Cut (字幕稍后收尾)")
            if "对齐声音" in chunk_value:
                chunk_value = "双行大段 (约10字，智能折行)"
                timing_value = "对齐声音 (按停顿)"
            self.chunk_mode.blockSignals(True)
            self.chunk_mode.setCurrentText(chunk_value)
            self.chunk_mode.blockSignals(False)
            self.timing_mode.blockSignals(True)
            self.timing_mode.setCurrentText(timing_value)
            self.timing_mode.blockSignals(False)
            self.chk_fill_gaps.blockSignals(True)
            self.chk_fill_gaps.setChecked(bool(self.state.get("fill_subtitle_gaps", True)))
            self.chk_fill_gaps.blockSignals(False)
            self.text_editor.blockSignals(True)
            self.text_editor.setPlainText(self.state.get("custom_text", ""))
            self.text_editor.blockSignals(False)
            clips = self.state.get("video_clips", [])
            if clips:
                self.btn_v.setText("✅ 已导原素材")
                self._prepare_preview_proxies_for_clips(clips, announce=True)
                self._prime_video_preview_source(clips[0], announce=True)
                self.on_resolution_changed(self.state.get("resolution", get_output_resolution()))
                self.generate_waveform(clips[0]["path"], "v_wave_pixmap", max_seconds=90)
                threading.Thread(target=self._gen_thumbs_cache, daemon=True).start()
            if self.state.get("audio_path") and os.path.exists(self.state.get("audio_path")):
                self.btn_a.setText("✅ " + os.path.basename(self.state.get("audio_path"))[:15]); self.audio_player.setSource(QUrl.fromLocalFile(self.state.get("audio_path"))); self.generate_waveform(self.state.get("audio_path"), "a_wave_pixmap")
            if self.state.get("music_path") and os.path.exists(self.state.get("music_path")) and hasattr(self, "btn_music"):
                self.btn_music.setText("✅ " + os.path.basename(self.state.get("music_path"))[:15])
                self.music_player.setSource(QUrl.fromLocalFile(self.state.get("music_path")))
                self.music_player.setLoops(QMediaPlayer.Loops.Infinite)
            self.render_ui_list(); self.refresh_media_pool(); self.switch_inspector("empty");
            self.push_history() # 初始化历史栈
            QTimer.singleShot(500, self._sync_duration_after_cache)
        except: pass

    def on_resolution_changed(self, text):
        clips = self.state.get("video_clips", [])
        media_path = clips[0]["path"] if clips else ""
        self.proj_width, self.proj_height = resolution_to_size(text or get_output_resolution(), media_path, self._clip_dimensions_from_state)
        self.aspect_container.set_ratio(self.proj_width, self.proj_height); self.state["resolution"] = text
        self.browser.page().runJavaScript(f"if(typeof setResolution === 'function') setResolution({self.proj_width}, {self.proj_height});")
        self._sync_preview_overlay_transform()
        self.redraw_video_preview()
        self._update_workspace_status()
        self.auto_save_cache()

    def _gen_thumbs_cache(self):
        clips = self.state.get("video_clips", [])
        if not clips: return
        try:
            clip = self._ensure_clip_import_metadata(clips[0])
            source_path = clip.get("path", "")
            if not source_path or not os.path.exists(source_path):
                return
            heavy_clip = clip_should_auto_proxy(clip)
            if heavy_clip and not preview_proxy_is_ready(clip):
                return
            clip_path = preview_source_for_clip(clip) if heavy_clip else source_path
            if not clip_path or not os.path.exists(clip_path):
                return
            stat = os.stat(source_path)
            cache_sig = f"{os.path.abspath(source_path)}|{stat.st_mtime_ns}|{stat.st_size}"
            cache_key = hashlib.sha1(cache_sig.encode("utf-8", "replace")).hexdigest()[:18]
            tdir = os.path.join(tempfile.gettempdir(), f"sh_v8_thumbs_{cache_key}")
            os.makedirs(tdir, exist_ok=True)
            if len([f for f in os.listdir(tdir) if f.endswith('.jpg')]) == 0:
                thumb_seconds = "45" if heavy_clip else "180"
                thumb_fps = "0.25" if heavy_clip else "0.5"
                thumb_filter = f"fps={thumb_fps},scale=80:45:force_original_aspect_ratio=decrease,pad=80:45:(ow-iw)/2:(oh-ih)/2"
                subprocess.run(
                    [get_ffmpeg_cmd(), "-y", "-i", clip_path, "-an", "-t", thumb_seconds, "-vf", thumb_filter, "-threads", "1", os.path.join(tdir, "t_%04d.jpg")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000 if os.name == 'nt' else 0,
                )
            files = sorted([os.path.join(tdir, f) for f in os.listdir(tdir) if f.endswith('.jpg')]); QTimer.singleShot(0, lambda: self._load_thumbs_ui(files))
        except: pass

    def _load_thumbs_ui(self, files):
        self.video_thumbs = [QPixmap(f) for f in files]; self.timeline_widget.sync_from_controller()

    def _sync_duration_after_cache(self):
        self.audio_output.setVolume(self.state.get("v_volume", 100) / 100.0); self.audio_track_output.setVolume(self.state.get("a_volume", 100) / 100.0)
        if hasattr(self, "music_output"):
            self.music_output.setVolume(float(self.state.get("music_volume", 35) or 35) / 100.0)
        if self._has_music_track():
            self._sync_music_playback_to_time(self.current_play_time, force_seek=True)
        self._recalc_duration(); self.sync_player_to_time(0.1)

    def _get_target_clips(self):
        if self.current_selected_idx == -1: return []
        current_clip = self.state["subs_data"][self.current_selected_idx]
        scope = self.style_scope_combo.currentIndex()
        if scope == 0: return self.state["subs_data"]
        elif scope == 1: return [c for c in self.state["subs_data"] if c.get("track") == current_clip.get("track")]
        else: return [current_clip]
