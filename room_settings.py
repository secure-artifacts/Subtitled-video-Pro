# ==========================================
# 文件名: room_settings.py (账号池与负载均衡全开版)
# ==========================================
import os
import json
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel,
                             QTextEdit, QPushButton, QMessageBox, QFrame,
                             QHBoxLayout, QLineEdit, QScrollArea, QComboBox,
                             QCheckBox, QFileDialog, QProgressDialog, QKeySequenceEdit,
                             QTabWidget)
from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QKeySequence
import requests

from core import DEFAULT_SYNC_URL, CLOUD_SECRET
from app_theme import apply_tinted_styles
from room_theme_bridge import apply_room_theme_bridge
from font_assets import ensure_fonts_dir, ensure_personal_fonts_dir, font_asset_summary, register_bundled_fonts
from render_config import (
    cpu_safe_profile,
    describe_render_profile,
    detect_hardware_profile,
    peek_render_profile,
)
from font_registry import FONT_REGISTRY_FILE, STATUS_NONCOMMERCIAL, load_font_registry, reset_to_open_font_policy, upsert_approved_fonts
from ai_transcription import DEFAULT_GROQ_MODEL, parse_transcription_provider_order
from canva_connect import (
    DEFAULT_CANVA_REDIRECT_URI,
    DEFAULT_CANVA_SCOPES,
    build_authorization_url,
    exchange_authorization_code,
    generate_pkce_pair,
    generate_state,
    store_token_response,
)
from app_config import (
    CONFIG_FILE,
    DEFAULT_SHORTCUTS,
    OUTPUT_RESOLUTION_OPTIONS,
    PREVIEW_PROXY_RESOLUTION_OPTIONS,
    get_output_resolution,
    get_preview_fullscreen_shortcut,
    get_preview_proxy_resolution,
    load_app_config,
    save_app_config,
    set_output_resolution,
    set_preview_fullscreen_shortcut,
    set_preview_proxy_resolution,
)
from app_update import (
    check_latest_release,
    download_asset,
    load_update_config,
    normalize_repo,
    pick_release_asset,
    release_download_dir,
    save_update_config,
)

def load_cloud_secret():
    cloud_secret = CLOUD_SECRET
    config = load_app_config()
    cloud_secret = config.get("cloud_secret") or cloud_secret
    return (cloud_secret or "").strip()


class SettingsSection(QWidget):
    def __init__(self, title, content_widget, accent="#89b4fa", expanded=True, parent=None):
        super().__init__(parent)
        self.title = title
        self.content_widget = content_widget
        self.accent = accent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(bool(expanded))
        self.toggle_button.clicked.connect(self.sync_state)
        layout.addWidget(self.toggle_button)

        self.content_widget.setVisible(bool(expanded))
        layout.addWidget(self.content_widget)
        self.sync_state()

    def sync_state(self, checked=None):
        expanded = self.toggle_button.isChecked()
        self.toggle_button.setText(f"{'▼' if expanded else '▶'}  {self.title}")
        self.content_widget.setVisible(expanded)

    def apply_section_theme(self, colors):
        self.toggle_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {colors['panel_2']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 10px 14px;
                text-align: left;
                font-size: 14px;
                font-weight: 900;
            }}
            QPushButton:hover {{
                background-color: {colors['card_hover']};
                border-color: {self.accent};
            }}
            QPushButton:checked {{
                color: {self.accent};
            }}
        """)


class SettingsView(QWidget):
    sig_sync_finished = pyqtSignal(bool, str, object)
    sig_hardware_finished = pyqtSignal(bool, str, object)
    sig_update_checked = pyqtSignal(bool, str, object)
    sig_update_progress = pyqtSignal(int, int)
    sig_update_downloaded = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
        self.sig_sync_finished.connect(self.on_sync_finished)
        self.sig_hardware_finished.connect(self.on_hardware_finished)
        self.sig_update_checked.connect(self.on_update_checked)
        self.sig_update_progress.connect(self.on_update_progress)
        self.sig_update_downloaded.connect(self.on_update_downloaded)
        self._latest_release = None
        self._update_progress_dialog = None
        self._auto_update_check_started = False
        self.load_config()

    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(10)
        self.setting_sections = []
        self.settings_scrolls = []
        self.settings_content = QWidget(self)

        # 👑 顶部标题
        title = QLabel("⚙️ 全局设置")
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #cdd6f4;")
        root_layout.addWidget(title)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setDocumentMode(True)
        root_layout.addWidget(self.settings_tabs, stretch=1)

        def make_tab(name):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(14, 14, 14, 14)
            page_layout.setSpacing(12)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.settings_scrolls.append(scroll)
            self.settings_tabs.addTab(scroll, name)
            return page_layout

        render_tab_layout = make_tab("渲染与性能")
        cloud_tab_layout = make_tab("云端与 AI")
        workflow_tab_layout = make_tab("操作")
        update_tab_layout = make_tab("更新")
        font_tab_layout = make_tab("字体与合规")

        cloud_frame = QFrame()
        cloud_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #89b4fa;")
        cloud_layout = QVBoxLayout(cloud_frame)
        cloud_layout.setContentsMargins(25, 20, 25, 20)
        cloud_layout.setSpacing(10)

        lbl_cloud_title = QLabel("Cloudflare Workers 云端同步链接")
        lbl_cloud_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa; border: none;")
        cloud_layout.addWidget(lbl_cloud_title)

        lbl_cloud_desc = QLabel("填写 Workers 链接后，点击“获取/识别 API”，软件会从云端读取 cf_accounts 并同步到下方账号池。")
        lbl_cloud_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        cloud_layout.addWidget(lbl_cloud_desc)

        url_row = QHBoxLayout()
        self.txt_sync_url = QLineEdit()
        self.txt_sync_url.setPlaceholderText("例如: https://你的-worker.workers.dev/")
        self.txt_sync_url.setStyleSheet("""
            QLineEdit {
                background-color: #11111b;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
        """)
        btn_sync = QPushButton("获取/识别 API")
        btn_sync.setFixedHeight(40)
        btn_sync.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #11111b;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 16px;
                border: none;
            }
            QPushButton:hover { background-color: #74c7ec; }
        """)
        btn_sync.clicked.connect(self.sync_from_worker)
        url_row.addWidget(self.txt_sync_url, stretch=1)
        url_row.addWidget(btn_sync)
        cloud_layout.addLayout(url_row)

        self.lbl_sync_status = QLabel("就绪")
        self.lbl_sync_status.setStyleSheet("color: #f9e2af; font-size: 12px; border: none;")
        cloud_layout.addWidget(self.lbl_sync_status)

        self.cloud_section = self._add_section(cloud_tab_layout, "云端同步链接", cloud_frame, "#89b4fa", expanded=True)

        hardware_frame = QFrame()
        hardware_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #a6e3a1;")
        hardware_layout = QVBoxLayout(hardware_frame)
        hardware_layout.setContentsMargins(25, 18, 25, 18)
        hardware_layout.setSpacing(10)

        lbl_hardware_title = QLabel("⚙️ 硬件扫描与渲染优化")
        lbl_hardware_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #a6e3a1; border: none;")
        hardware_layout.addWidget(lbl_hardware_title)

        lbl_hardware_desc = QLabel("自动识别 CPU、内存、显卡和 FFmpeg 编码器，优先使用可用硬件加速；不稳定时可一键切回 CPU 安全模式。")
        lbl_hardware_desc.setWordWrap(True)
        lbl_hardware_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        hardware_layout.addWidget(lbl_hardware_desc)

        self.lbl_hardware_profile = QLabel("尚未扫描。点击下方按钮后会写入 settings.json，导出和批量流水线都会使用这份配置。")
        self.lbl_hardware_profile.setWordWrap(True)
        self.lbl_hardware_profile.setStyleSheet("""
            QLabel {
                background-color: #11111b;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 10px;
                font-size: 12px;
                line-height: 1.4;
            }
        """)
        hardware_layout.addWidget(self.lbl_hardware_profile)

        hardware_btn_row = QHBoxLayout()
        self.btn_scan_hardware = QPushButton("🔍 扫描显卡/CPU并自动配置")
        self.btn_scan_hardware.setFixedHeight(38)
        self.btn_scan_hardware.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1;
                color: #11111b;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 16px;
                border: none;
            }
            QPushButton:hover { background-color: #94d38f; }
            QPushButton:disabled { background-color: #45475a; color: #a6adc8; }
        """)
        self.btn_scan_hardware.clicked.connect(self.scan_hardware_profile)

        self.btn_cpu_safe = QPushButton("🧯 使用 CPU 安全模式")
        self.btn_cpu_safe.setFixedHeight(38)
        self.btn_cpu_safe.setStyleSheet("""
            QPushButton {
                background-color: #f9e2af;
                color: #11111b;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 16px;
                border: none;
            }
            QPushButton:hover { background-color: #f5d58b; }
            QPushButton:disabled { background-color: #45475a; color: #a6adc8; }
        """)
        self.btn_cpu_safe.clicked.connect(self.use_cpu_render_profile)

        hardware_btn_row.addWidget(self.btn_scan_hardware)
        hardware_btn_row.addWidget(self.btn_cpu_safe)
        hardware_layout.addLayout(hardware_btn_row)

        self.hardware_section = self._add_section(render_tab_layout, "硬件扫描与渲染优化", hardware_frame, "#a6e3a1", expanded=True)

        resolution_frame = QFrame()
        resolution_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #cba6f7;")
        resolution_layout = QVBoxLayout(resolution_frame)
        resolution_layout.setContentsMargins(25, 18, 25, 18)
        resolution_layout.setSpacing(10)

        lbl_resolution_title = QLabel("画面分辨率")
        lbl_resolution_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #cba6f7; border: none;")
        resolution_layout.addWidget(lbl_resolution_title)

        lbl_resolution_desc = QLabel("固定工程与批量渲染画布，默认 1080x1920。字幕字号、设计组件和素材缩放都会按这份画布统一计算。")
        lbl_resolution_desc.setWordWrap(True)
        lbl_resolution_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        resolution_layout.addWidget(lbl_resolution_desc)

        self.output_resolution_combo = QComboBox()
        self.output_resolution_combo.addItems(OUTPUT_RESOLUTION_OPTIONS)
        self.output_resolution_combo.setCurrentText(get_output_resolution())
        self.output_resolution_combo.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-weight: bold;")
        self.output_resolution_combo.currentTextChanged.connect(self.save_output_resolution_ui)
        resolution_layout.addWidget(self.output_resolution_combo)

        self.lbl_output_resolution = QLabel("")
        self.lbl_output_resolution.setStyleSheet("color: #a6e3a1; font-size: 12px; border: none;")
        resolution_layout.addWidget(self.lbl_output_resolution)

        self.resolution_section = self._add_section(render_tab_layout, "画面分辨率", resolution_frame, "#cba6f7", expanded=True)

        preview_frame = QFrame()
        preview_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #89b4fa;")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(25, 18, 25, 18)
        preview_layout.setSpacing(10)
        lbl_preview_title = QLabel("预览流畅度")
        lbl_preview_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa; border: none;")
        preview_layout.addWidget(lbl_preview_title)
        lbl_preview_desc = QLabel("只降低精修预览代理分辨率，适合长视频或电脑卡顿时使用；正式导出仍保持原始素材与工程画布。")
        lbl_preview_desc.setWordWrap(True)
        lbl_preview_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        preview_layout.addWidget(lbl_preview_desc)
        self.preview_proxy_resolution_combo = QComboBox()
        self.preview_proxy_resolution_combo.addItems(PREVIEW_PROXY_RESOLUTION_OPTIONS)
        self.preview_proxy_resolution_combo.setCurrentText(get_preview_proxy_resolution())
        self.preview_proxy_resolution_combo.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-weight: bold;")
        self.preview_proxy_resolution_combo.currentTextChanged.connect(self.save_preview_proxy_resolution_ui)
        preview_layout.addWidget(self.preview_proxy_resolution_combo)
        self.lbl_preview_proxy_resolution = QLabel("")
        self.lbl_preview_proxy_resolution.setStyleSheet("color: #a6e3a1; font-size: 12px; border: none;")
        preview_layout.addWidget(self.lbl_preview_proxy_resolution)
        self.preview_proxy_section = self._add_section(render_tab_layout, "预览流畅度", preview_frame, "#89b4fa", expanded=True)

        shortcuts_frame = QFrame()
        shortcuts_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #89b4fa;")
        shortcuts_layout = QVBoxLayout(shortcuts_frame)
        shortcuts_layout.setContentsMargins(25, 18, 25, 18)
        shortcuts_layout.setSpacing(10)

        lbl_shortcuts_title = QLabel("快捷键操作")
        lbl_shortcuts_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa; border: none;")
        shortcuts_layout.addWidget(lbl_shortcuts_title)

        lbl_shortcuts_desc = QLabel("设置软件内部快捷键。预览全屏默认 Ctrl+F，只在软件窗口内响应，不会注册为系统全局热键。")
        lbl_shortcuts_desc.setWordWrap(True)
        lbl_shortcuts_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        shortcuts_layout.addWidget(lbl_shortcuts_desc)

        fullscreen_shortcut_row = QHBoxLayout()
        fullscreen_shortcut_row.addWidget(QLabel("预览全屏:", styleSheet="color: #cdd6f4; border: none;"))
        self.preview_fullscreen_shortcut_edit = QKeySequenceEdit()
        self.preview_fullscreen_shortcut_edit.setKeySequence(QKeySequence(get_preview_fullscreen_shortcut()))
        self.preview_fullscreen_shortcut_edit.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px;")
        fullscreen_shortcut_row.addWidget(self.preview_fullscreen_shortcut_edit, stretch=1)
        self.btn_save_shortcuts = QPushButton("保存")
        self.btn_reset_shortcuts = QPushButton("恢复默认")
        for btn in (self.btn_save_shortcuts, self.btn_reset_shortcuts):
            btn.setStyleSheet("background-color: #313244; color: #89b4fa; font-weight: bold; border-radius: 6px; padding: 8px 12px;")
        self.btn_save_shortcuts.clicked.connect(self.save_shortcuts_ui)
        self.btn_reset_shortcuts.clicked.connect(self.reset_shortcuts_ui)
        self.preview_fullscreen_shortcut_edit.editingFinished.connect(self.save_shortcuts_ui)
        fullscreen_shortcut_row.addWidget(self.btn_save_shortcuts)
        fullscreen_shortcut_row.addWidget(self.btn_reset_shortcuts)
        shortcuts_layout.addLayout(fullscreen_shortcut_row)

        self.lbl_shortcuts_status = QLabel("")
        self.lbl_shortcuts_status.setWordWrap(True)
        self.lbl_shortcuts_status.setStyleSheet("color: #a6e3a1; background-color: #11111b; border: 1px solid #313244; border-radius: 6px; padding: 8px; font-size: 12px;")
        shortcuts_layout.addWidget(self.lbl_shortcuts_status)

        self.shortcuts_section = self._add_section(workflow_tab_layout, "快捷键操作", shortcuts_frame, "#89b4fa", expanded=True)

        update_frame = QFrame()
        update_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #74c7ec;")
        update_layout = QVBoxLayout(update_frame)
        update_layout.setContentsMargins(25, 18, 25, 18)
        update_layout.setSpacing(10)

        lbl_update_title = QLabel("软件更新与下载")
        lbl_update_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #74c7ec; border: none;")
        update_layout.addWidget(lbl_update_title)

        lbl_update_desc = QLabel("配置 GitHub Release 仓库后，可以在软件内检查最新版本并下载安装包。")
        lbl_update_desc.setWordWrap(True)
        lbl_update_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        update_layout.addWidget(lbl_update_desc)

        update_repo_row = QHBoxLayout()
        update_repo_row.addWidget(QLabel("Release 仓库:", styleSheet="color: #cdd6f4; border: none;"))
        self.txt_update_repo = QLineEdit()
        self.txt_update_repo.setPlaceholderText("owner/repo 或 https://github.com/owner/repo")
        self.txt_update_repo.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px;")
        self.txt_update_version = QLineEdit()
        self.txt_update_version.setPlaceholderText("当前版本")
        self.txt_update_version.setFixedWidth(130)
        self.txt_update_version.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px;")
        update_repo_row.addWidget(self.txt_update_repo, stretch=1)
        update_repo_row.addWidget(self.txt_update_version)
        update_layout.addLayout(update_repo_row)

        update_dir_row = QHBoxLayout()
        update_dir_row.addWidget(QLabel("下载位置:", styleSheet="color: #cdd6f4; border: none;"))
        self.txt_update_dir = QLineEdit()
        self.txt_update_dir.setPlaceholderText("默认保存到当前目录/updates")
        self.txt_update_dir.setStyleSheet("background-color: #11111b; color: #cdd6f4; border: 1px solid #45475a; border-radius: 6px; padding: 8px;")
        self.btn_update_dir = QPushButton("选择目录")
        self.btn_update_dir.setStyleSheet("background-color: #313244; color: #cdd6f4; font-weight: bold; border-radius: 6px; padding: 8px 12px;")
        self.btn_update_dir.clicked.connect(self.select_update_download_dir)
        update_dir_row.addWidget(self.txt_update_dir, stretch=1)
        update_dir_row.addWidget(self.btn_update_dir)
        update_layout.addLayout(update_dir_row)

        update_btn_row = QHBoxLayout()
        self.chk_update_auto = QCheckBox("启动后自动检查")
        self.chk_update_auto.setStyleSheet("color: #a6e3a1; border: none; font-weight: bold;")
        self.btn_update_check = QPushButton("检查更新")
        self.btn_update_download = QPushButton("下载最新包")
        self.btn_update_download.setEnabled(False)
        for btn in (self.btn_update_check, self.btn_update_download):
            btn.setStyleSheet("background-color: #74c7ec; color: #11111b; font-weight: bold; border-radius: 6px; padding: 8px 14px;")
        self.btn_update_check.clicked.connect(self.check_updates_ui)
        self.btn_update_download.clicked.connect(self.download_update_ui)
        update_btn_row.addWidget(self.chk_update_auto)
        update_btn_row.addStretch()
        update_btn_row.addWidget(self.btn_update_check)
        update_btn_row.addWidget(self.btn_update_download)
        update_layout.addLayout(update_btn_row)

        self.lbl_update_status = QLabel("就绪")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setStyleSheet("color: #f9e2af; background-color: #11111b; border: 1px solid #313244; border-radius: 6px; padding: 8px; font-size: 12px;")
        update_layout.addWidget(self.lbl_update_status)
        self.update_section = self._add_section(update_tab_layout, "软件更新与下载", update_frame, "#74c7ec", expanded=True)

        font_frame = QFrame()
        font_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #f9e2af;")
        font_layout = QVBoxLayout(font_frame)
        font_layout.setContentsMargins(25, 18, 25, 18)
        font_layout.setSpacing(10)

        lbl_font_title = QLabel("字体版权登记")
        lbl_font_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #f9e2af; border: none;")
        lbl_font_title.setText("字体个人授权登记")
        font_layout.addWidget(lbl_font_title)

        lbl_font_desc = QLabel("把已经由团队确认可商用的字体写在这里，每行一个字体名。工程体检会用这份登记表标记字体风险。系统字体和未登记字体仍会提示复核。")
        lbl_font_desc.setWordWrap(True)
        lbl_font_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        lbl_font_desc.setText("这里登记的是你本机可个人使用、但不随软件或模板分发的字体。真正可打包的字体只来自 fonts/open 开源字体清单；其他用户需要自己安装或提供授权。")
        font_layout.addWidget(lbl_font_desc)

        self.lbl_font_registry = QLabel("")
        self.lbl_font_registry.setStyleSheet("color: #cdd6f4; background-color: #11111b; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-size: 12px;")
        font_layout.addWidget(self.lbl_font_registry)

        self.lbl_font_assets = QLabel("")
        self.lbl_font_assets.setWordWrap(True)
        self.lbl_font_assets.setStyleSheet("color: #a6adc8; background-color: #11111b; border: 1px solid #313244; border-radius: 6px; padding: 8px; font-size: 12px;")
        font_layout.addWidget(self.lbl_font_assets)

        self.txt_approved_fonts = QTextEdit()
        self.txt_approved_fonts.setPlaceholderText("例如:\nNoto Sans SC\nSource Han Sans SC\n你的品牌授权字体")
        self.txt_approved_fonts.setPlaceholderText("例如:\n你本机安装的个人授权字体\n品牌字体本机预览名\n不可随包分发的字体")
        self.txt_approved_fonts.setMaximumHeight(92)
        self.txt_approved_fonts.setStyleSheet("background-color: #11111b; color: #f9e2af; border: 1px solid #45475a; border-radius: 6px; padding: 8px; font-family: Consolas, 'Microsoft YaHei';")
        font_layout.addWidget(self.txt_approved_fonts)

        font_btn_row = QHBoxLayout()
        self.btn_save_fonts = QPushButton("保存已确认字体")
        self.btn_save_fonts.setFixedHeight(36)
        self.btn_save_fonts.setStyleSheet("background-color: #f9e2af; color: #11111b; font-weight: bold; border-radius: 6px; padding: 0 16px; border: none;")
        self.btn_save_fonts.clicked.connect(self.save_font_registry_ui)
        self.btn_open_only_fonts = QPushButton("整理为开源字体库")
        self.btn_open_only_fonts.setFixedHeight(36)
        self.btn_open_only_fonts.setStyleSheet("background-color: #313244; color: #f9e2af; font-weight: bold; border-radius: 6px; padding: 0 16px; border: none;")
        self.btn_open_only_fonts.clicked.connect(self.reset_open_font_policy_ui)
        self.btn_refresh_font_assets = QPushButton("刷新内置字体包")
        self.btn_refresh_font_assets.setFixedHeight(36)
        self.btn_refresh_font_assets.setStyleSheet("background-color: #313244; color: #a6e3a1; font-weight: bold; border-radius: 6px; padding: 0 16px; border: none;")
        self.btn_refresh_font_assets.clicked.connect(self.refresh_font_assets_ui)
        self.btn_open_fonts_dir = QPushButton("打开字体文件夹")
        self.btn_open_fonts_dir.setFixedHeight(36)
        self.btn_open_fonts_dir.setStyleSheet("background-color: #313244; color: #89b4fa; font-weight: bold; border-radius: 6px; padding: 0 16px; border: none;")
        self.btn_open_fonts_dir.clicked.connect(self.open_fonts_dir_ui)
        font_btn_row.addWidget(self.btn_save_fonts)
        font_btn_row.addWidget(self.btn_open_only_fonts)
        font_btn_row.addWidget(self.btn_refresh_font_assets)
        font_btn_row.addWidget(self.btn_open_fonts_dir)
        font_btn_row.addStretch()
        font_layout.addLayout(font_btn_row)

        self.font_section = self._add_section(font_tab_layout, "字体版权登记", font_frame, "#f9e2af", expanded=True)

        # 👑 账号池大框架
        ai_transcription_frame = QFrame()
        ai_transcription_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #a6e3a1;")
        ai_transcription_layout = QVBoxLayout(ai_transcription_frame)
        ai_transcription_layout.setContentsMargins(25, 18, 25, 18)
        ai_transcription_layout.setSpacing(10)

        lbl_ai_transcription_title = QLabel("AI \u542c\u8bd1\u670d\u52a1\u4e0e\u4f18\u5148\u7ea7")
        lbl_ai_transcription_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #a6e3a1; border: none;")
        ai_transcription_layout.addWidget(lbl_ai_transcription_title)

        lbl_ai_transcription_desc = QLabel("\u9ed8\u8ba4 Groq \u4f18\u5148\uff0cGroq \u5931\u8d25\u3001\u8d85\u989d\u6216\u8fd4\u56de\u5f02\u5e38\u65f6\u81ea\u52a8\u5207\u5230 Cloudflare\u3002\u540e\u7eed\u65b0\u589e API \u53ea\u9700\u6269\u5c55\u8fd9\u4e2a\u961f\u5217\u3002")
        lbl_ai_transcription_desc.setWordWrap(True)
        lbl_ai_transcription_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        ai_transcription_layout.addWidget(lbl_ai_transcription_desc)

        priority_row = QHBoxLayout()
        priority_row.addWidget(QLabel("\u542c\u8bd1\u4f18\u5148\u7ea7:", styleSheet="color:#cdd6f4; font-weight: bold; border:none;"))
        self.ai_transcription_priority_combo = QComboBox()
        self.ai_transcription_priority_combo.addItem("\u81ea\u52a8\uff1aGroq \u2192 Cloudflare", ["groq", "cloudflare"])
        self.ai_transcription_priority_combo.addItem("\u81ea\u52a8\uff1aCloudflare \u2192 Groq", ["cloudflare", "groq"])
        self.ai_transcription_priority_combo.addItem("\u4ec5 Groq", ["groq"])
        self.ai_transcription_priority_combo.addItem("\u4ec5 Cloudflare", ["cloudflare"])
        self.ai_transcription_priority_combo.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px; font-weight:bold;")
        priority_row.addWidget(self.ai_transcription_priority_combo, stretch=1)
        ai_transcription_layout.addLayout(priority_row)

        groq_key_label = QLabel("Groq API Key \u6c60\uff08\u6bcf\u884c\u4e00\u4e2a\uff09")
        groq_key_label.setStyleSheet("color:#f9e2af; font-weight:bold; border:none;")
        ai_transcription_layout.addWidget(groq_key_label)
        self.txt_groq_keys = QTextEdit()
        self.txt_groq_keys.setFixedHeight(94)
        self.txt_groq_keys.setPlaceholderText("gsk_...\n\u53ef\u4ee5\u586b\u591a\u4e2a\uff0c\u5931\u8d25\u65f6\u4f1a\u81ea\u52a8\u6362\u4e0b\u4e00\u4e2a")
        self.txt_groq_keys.setStyleSheet("QTextEdit { background-color:#11111b; color:#a6e3a1; font-family:Consolas; font-size:13px; border:1px solid #45475a; border-radius:6px; padding:10px; }")
        ai_transcription_layout.addWidget(self.txt_groq_keys)

        groq_option_row = QHBoxLayout()
        groq_option_row.addWidget(QLabel("Groq \u6a21\u578b:", styleSheet="color:#cdd6f4; border:none;"))
        self.groq_model_combo = QComboBox()
        self.groq_model_combo.addItems(["whisper-large-v3-turbo", "whisper-large-v3"])
        self.groq_model_combo.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        groq_option_row.addWidget(self.groq_model_combo, stretch=1)
        groq_option_row.addWidget(QLabel("\u8bed\u8a00:", styleSheet="color:#cdd6f4; border:none;"))
        self.txt_groq_language = QLineEdit()
        self.txt_groq_language.setPlaceholderText("en / auto")
        self.txt_groq_language.setFixedWidth(110)
        self.txt_groq_language.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        groq_option_row.addWidget(self.txt_groq_language)
        ai_transcription_layout.addLayout(groq_option_row)

        btn_save_ai_transcription = QPushButton("\u4fdd\u5b58 AI \u542c\u8bd1\u914d\u7f6e")
        btn_save_ai_transcription.setFixedHeight(38)
        btn_save_ai_transcription.setStyleSheet("background-color:#a6e3a1; color:#11111b; font-weight:bold; border-radius:6px; padding:0 16px; border:none;")
        btn_save_ai_transcription.clicked.connect(self.save_config)
        ai_transcription_layout.addWidget(btn_save_ai_transcription)

        self.ai_transcription_section = self._add_section(cloud_tab_layout, "AI \u542c\u8bd1\u670d\u52a1\u4f18\u5148\u7ea7", ai_transcription_frame, "#a6e3a1", expanded=True)


        canva_frame = QFrame()
        canva_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #f9e2af;")
        canva_layout = QVBoxLayout(canva_frame)
        canva_layout.setContentsMargins(25, 18, 25, 18)
        canva_layout.setSpacing(10)

        lbl_canva_title = QLabel("Canva 联动授权")
        lbl_canva_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #f9e2af; border: none;")
        canva_layout.addWidget(lbl_canva_title)

        lbl_canva_desc = QLabel("用于透明 WebM 字幕层导出后的 Canva 素材库上传。官方 Connect API 需要 OAuth 2.0 + PKCE；回调地址必须和 Canva 开发者后台一致。")
        lbl_canva_desc.setWordWrap(True)
        lbl_canva_desc.setStyleSheet("color: #a6adc8; font-size: 13px; border: none;")
        canva_layout.addWidget(lbl_canva_desc)

        canva_client_row = QHBoxLayout()
        canva_client_row.addWidget(QLabel("Client ID:", styleSheet="color:#cdd6f4; font-weight:bold; border:none;"))
        self.txt_canva_client_id = QLineEdit()
        self.txt_canva_client_id.setPlaceholderText("Canva OAuth Client ID")
        self.txt_canva_client_id.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        canva_client_row.addWidget(self.txt_canva_client_id, stretch=1)
        canva_layout.addLayout(canva_client_row)

        canva_secret_row = QHBoxLayout()
        canva_secret_row.addWidget(QLabel("Client Secret:", styleSheet="color:#cdd6f4; font-weight:bold; border:none;"))
        self.txt_canva_client_secret = QLineEdit()
        self.txt_canva_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_canva_client_secret.setPlaceholderText("Canva OAuth Client Secret")
        self.txt_canva_client_secret.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        canva_secret_row.addWidget(self.txt_canva_client_secret, stretch=1)
        canva_layout.addLayout(canva_secret_row)

        canva_redirect_row = QHBoxLayout()
        canva_redirect_row.addWidget(QLabel("回调地址:", styleSheet="color:#cdd6f4; font-weight:bold; border:none;"))
        self.txt_canva_redirect_uri = QLineEdit()
        self.txt_canva_redirect_uri.setPlaceholderText(DEFAULT_CANVA_REDIRECT_URI)
        self.txt_canva_redirect_uri.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        canva_redirect_row.addWidget(self.txt_canva_redirect_uri, stretch=1)
        canva_layout.addLayout(canva_redirect_row)

        canva_scope_row = QHBoxLayout()
        canva_scope_row.addWidget(QLabel("授权范围:", styleSheet="color:#cdd6f4; font-weight:bold; border:none;"))
        self.txt_canva_scopes = QLineEdit()
        self.txt_canva_scopes.setPlaceholderText(DEFAULT_CANVA_SCOPES)
        self.txt_canva_scopes.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        canva_scope_row.addWidget(self.txt_canva_scopes, stretch=1)
        canva_layout.addLayout(canva_scope_row)

        self.chk_canva_auto_upload = QCheckBox("导出 Canva 透明 WebM 后自动上传到 Canva 素材库")
        self.chk_canva_auto_upload.setStyleSheet("color:#cdd6f4; font-weight:700; border:none;")
        canva_layout.addWidget(self.chk_canva_auto_upload)

        canva_code_row = QHBoxLayout()
        canva_code_row.addWidget(QLabel("授权回调 code:", styleSheet="color:#cdd6f4; font-weight:bold; border:none;"))
        self.txt_canva_auth_code = QLineEdit()
        self.txt_canva_auth_code.setPlaceholderText("授权后粘贴完整回调地址，或只粘贴 code= 后面的值")
        self.txt_canva_auth_code.setStyleSheet("background-color:#11111b; color:#cdd6f4; border:1px solid #45475a; border-radius:6px; padding:8px;")
        canva_code_row.addWidget(self.txt_canva_auth_code, stretch=1)
        canva_layout.addLayout(canva_code_row)

        canva_button_row = QHBoxLayout()
        self.btn_canva_open_dev = QPushButton("打开 Canva 开发者后台")
        self.btn_canva_start_auth = QPushButton("开始授权")
        self.btn_canva_exchange_code = QPushButton("兑换授权")
        self.btn_save_canva = QPushButton("保存 Canva 配置")
        for btn in (self.btn_canva_open_dev, self.btn_canva_start_auth, self.btn_canva_exchange_code, self.btn_save_canva):
            btn.setFixedHeight(36)
            btn.setStyleSheet("background-color:#313244; color:#cdd6f4; font-weight:bold; border-radius:6px; padding:0 12px; border:none;")
        self.btn_canva_start_auth.setStyleSheet("background-color:#f9e2af; color:#11111b; font-weight:bold; border-radius:6px; padding:0 12px; border:none;")
        self.btn_canva_exchange_code.setStyleSheet("background-color:#a6e3a1; color:#11111b; font-weight:bold; border-radius:6px; padding:0 12px; border:none;")
        self.btn_canva_open_dev.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.canva.dev/developers/")))
        self.btn_canva_start_auth.clicked.connect(self.start_canva_authorization_ui)
        self.btn_canva_exchange_code.clicked.connect(self.exchange_canva_authorization_code_ui)
        self.btn_save_canva.clicked.connect(self.save_config)
        canva_button_row.addWidget(self.btn_canva_open_dev)
        canva_button_row.addWidget(self.btn_canva_start_auth)
        canva_button_row.addWidget(self.btn_canva_exchange_code)
        canva_button_row.addWidget(self.btn_save_canva)
        canva_button_row.addStretch()
        canva_layout.addLayout(canva_button_row)

        self.lbl_canva_status = QLabel("未授权：先在 Canva 开发者后台创建 App，并填写 Client ID / Secret。")
        self.lbl_canva_status.setWordWrap(True)
        self.lbl_canva_status.setStyleSheet("color:#f9e2af; font-size:12px; border:none;")
        canva_layout.addWidget(self.lbl_canva_status)

        self.canva_section = self._add_section(cloud_tab_layout, "Canva 联动授权", canva_frame, "#f9e2af", expanded=False)

        pool_frame = QFrame()
        pool_frame.setStyleSheet("background-color: #181825; border-radius: 10px; border: 1px solid #313244;")
        pool_layout = QVBoxLayout(pool_frame)
        pool_layout.setContentsMargins(25, 25, 25, 25)
        pool_layout.setSpacing(15)

        # 提示信息
        lbl_pool_title = QLabel("🤖 Cloudflare Whisper AI 账号池 (支持自动负载均衡与故障轮询)")
        lbl_pool_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa; border: none;")
        pool_layout.addWidget(lbl_pool_title)

        lbl_desc = QLabel("为了突破单账号免费额度与并发限制，请在下方【批量填入】您的云端账号矩阵。\n"
                          "👉 格式要求：每行填写一个账号，Account ID 和 API Token 之间用【英文逗号】或【空格】隔开。\n"
                          "👉 底层引擎在打轴时，遇到请求上限或报错会瞬间无缝切换下一个账号！完全不卡顿！")
        lbl_desc.setStyleSheet("color: #a6adc8; line-height: 1.5; font-size: 13px; border: none;")
        pool_layout.addWidget(lbl_desc)

        # 多行输入文本框
        self.txt_accounts = QTextEdit()
        self.txt_accounts.setPlaceholderText("粘贴您的账号阵列，例如:\nf48b2db71fc565c2abfc..., abcdefg1234567890...\n1234567890abcdef..., xyz0987654321...")
        self.txt_accounts.setStyleSheet("""
            QTextEdit {
                background-color: #11111b;
                color: #a6e3a1;
                font-family: Consolas;
                font-size: 14px;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        pool_layout.addWidget(self.txt_accounts, stretch=1)

        # 保存按钮
        btn_save = QPushButton("💾 保存全局账号阵列")
        btn_save.setFixedHeight(45)
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #f59e0b;
                color: #11111b;
                font-size: 16px;
                font-weight: bold;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #d97706;
            }
        """)
        btn_save.clicked.connect(self.save_config)
        pool_layout.addWidget(btn_save)

        self.pool_section = self._add_section(cloud_tab_layout, "Cloudflare Whisper AI 账号池", pool_frame, "#89b4fa", expanded=True)
        for tab_layout in (render_tab_layout, cloud_tab_layout, workflow_tab_layout, update_tab_layout, font_tab_layout):
            tab_layout.addStretch(1)

    def _add_section(self, layout, title, frame, accent, expanded=True):
        section = SettingsSection(title, frame, accent=accent, expanded=expanded, parent=self.settings_content)
        self.setting_sections.append(section)
        layout.addWidget(section)
        return section

    def apply_theme(self, colors, theme_key=None):
        self._theme_colors = colors
        self._theme_key = theme_key or ""
        apply_tinted_styles(self, colors)
        apply_room_theme_bridge(self, colors)
        if hasattr(self, "settings_tabs"):
            self.settings_tabs.setStyleSheet(f"""
                QTabWidget::pane {{
                    background-color: {colors['panel']};
                    border: 1px solid {colors['border']};
                    border-radius: 8px;
                    top: -1px;
                }}
                QTabBar::tab {{
                    background-color: {colors['panel']};
                    color: {colors['muted']};
                    padding: 8px 16px;
                    margin-right: 4px;
                    border: 1px solid {colors['border']};
                    border-bottom: none;
                    border-top-left-radius: 7px;
                    border-top-right-radius: 7px;
                    font-weight: 800;
                }}
                QTabBar::tab:selected {{
                    background-color: {colors['panel_2']};
                    color: {colors['accent_2']};
                    border-color: {colors['accent']};
                }}
                QTabBar::tab:hover {{
                    background-color: {colors['card_hover']};
                    color: {colors['text']};
                }}
            """)
        scroll_style = f"""
            QScrollArea {{
                background-color: {colors['bg']};
                border: none;
            }}
            QScrollBar:vertical {{
                background: {colors['panel']};
                width: 10px;
                margin: 2px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: {colors['border']};
                border-radius: 5px;
                min-height: 38px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {colors['accent']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """
        for scroll in getattr(self, "settings_scrolls", []):
            scroll.setStyleSheet(scroll_style)
        for section in getattr(self, "setting_sections", []):
            section.apply_section_theme(colors)

    def save_output_resolution_ui(self, value):
        saved = set_output_resolution(value)
        if hasattr(self, "lbl_output_resolution"):
            self.lbl_output_resolution.setText(f"当前固定画布：{saved}")
        parent = self.parent()
        while parent is not None and not hasattr(parent, "room_edit"):
            parent = parent.parent()
        if parent and hasattr(parent, "room_edit"):
            try:
                parent.room_edit.on_resolution_changed(saved)
            except Exception:
                pass

    def save_preview_proxy_resolution_ui(self, value):
        saved = set_preview_proxy_resolution(value)
        if hasattr(self, "lbl_preview_proxy_resolution"):
            self.lbl_preview_proxy_resolution.setText(f"当前预览代理：{saved}（不影响导出）")
        parent = self.parent()
        while parent is not None and not hasattr(parent, "room_edit"):
            parent = parent.parent()
        if parent and hasattr(parent, "room_edit"):
            try:
                parent.room_edit.on_preview_proxy_resolution_changed(saved)
            except Exception:
                pass

    def _main_window(self):
        parent = self.parent()
        while parent is not None and not hasattr(parent, "room_edit"):
            parent = parent.parent()
        return parent

    def _shortcut_edit_text(self, sequence):
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText).strip()
        return text or DEFAULT_SHORTCUTS["preview_fullscreen"]

    def save_shortcuts_ui(self):
        if not hasattr(self, "preview_fullscreen_shortcut_edit"):
            return
        value = self._shortcut_edit_text(self.preview_fullscreen_shortcut_edit.keySequence())
        saved = set_preview_fullscreen_shortcut(value)
        self.preview_fullscreen_shortcut_edit.blockSignals(True)
        self.preview_fullscreen_shortcut_edit.setKeySequence(QKeySequence(saved))
        self.preview_fullscreen_shortcut_edit.blockSignals(False)
        main_window = self._main_window()
        if main_window and hasattr(main_window, "room_edit"):
            try:
                saved = main_window.room_edit.apply_preview_fullscreen_shortcut(saved)
            except Exception:
                pass
        if hasattr(self, "lbl_shortcuts_status"):
            self.lbl_shortcuts_status.setText(f"预览全屏快捷键已保存：{saved}")

    def reset_shortcuts_ui(self):
        default = DEFAULT_SHORTCUTS["preview_fullscreen"]
        self.preview_fullscreen_shortcut_edit.setKeySequence(QKeySequence(default))
        self.save_shortcuts_ui()

    def _parse_groq_keys_from_ui(self):
        if not hasattr(self, "txt_groq_keys"):
            return []
        raw = self.txt_groq_keys.toPlainText().replace(",", "\n")
        keys = []
        for line in raw.splitlines():
            key = line.strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    def _current_transcription_priority_order(self):
        combo = getattr(self, "ai_transcription_priority_combo", None)
        if combo is None:
            return ["groq", "cloudflare"]
        return parse_transcription_provider_order(combo.currentData())

    def _set_transcription_priority_order(self, order):
        combo = getattr(self, "ai_transcription_priority_combo", None)
        if combo is None:
            return
        parsed = parse_transcription_provider_order(order)
        for index in range(combo.count()):
            if parse_transcription_provider_order(combo.itemData(index)) == parsed:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)


    def _current_canva_config_from_ui(self):
        return {
            "canva_client_id": self.txt_canva_client_id.text().strip() if hasattr(self, "txt_canva_client_id") else "",
            "canva_client_secret": self.txt_canva_client_secret.text().strip() if hasattr(self, "txt_canva_client_secret") else "",
            "canva_redirect_uri": self.txt_canva_redirect_uri.text().strip() if hasattr(self, "txt_canva_redirect_uri") else DEFAULT_CANVA_REDIRECT_URI,
            "canva_scopes": self.txt_canva_scopes.text().strip() if hasattr(self, "txt_canva_scopes") else DEFAULT_CANVA_SCOPES,
            "canva_auto_upload": bool(self.chk_canva_auto_upload.isChecked()) if hasattr(self, "chk_canva_auto_upload") else False,
        }

    def _merge_canva_config(self, base=None):
        cfg = dict(base if isinstance(base, dict) else load_app_config())
        cfg.update(self._current_canva_config_from_ui())
        save_app_config(cfg)
        return cfg

    def load_canva_ui(self, config=None):
        if not hasattr(self, "txt_canva_client_id"):
            return
        config = config if isinstance(config, dict) else load_app_config()
        self.txt_canva_client_id.setText(str(config.get("canva_client_id") or ""))
        self.txt_canva_client_secret.setText(str(config.get("canva_client_secret") or ""))
        self.txt_canva_redirect_uri.setText(str(config.get("canva_redirect_uri") or DEFAULT_CANVA_REDIRECT_URI))
        self.txt_canva_scopes.setText(str(config.get("canva_scopes") or DEFAULT_CANVA_SCOPES))
        self.chk_canva_auto_upload.setChecked(bool(config.get("canva_auto_upload", False)))
        token_ok = bool(config.get("canva_access_token") or config.get("canva_refresh_token"))
        if token_ok:
            self.lbl_canva_status.setText("已保存 Canva 授权 Token。透明 WebM 导出后可按开关自动上传到 Canva 素材库。")
            self.lbl_canva_status.setStyleSheet("color:#a6e3a1; font-size:12px; border:none;")
        else:
            self.lbl_canva_status.setText("未授权：填写 Canva App 的 Client ID / Secret 后，点击开始授权，再粘贴回调 code 兑换。")
            self.lbl_canva_status.setStyleSheet("color:#f9e2af; font-size:12px; border:none;")

    def start_canva_authorization_ui(self):
        cfg = self._merge_canva_config()
        client_id = str(cfg.get("canva_client_id") or "").strip()
        if not client_id:
            QMessageBox.warning(self, "Canva 授权", "请先填写 Canva OAuth Client ID。")
            return
        verifier, challenge = generate_pkce_pair()
        state = generate_state()
        cfg["canva_oauth_code_verifier"] = verifier
        cfg["canva_oauth_state"] = state
        save_app_config(cfg)
        url = build_authorization_url(client_id, cfg.get("canva_redirect_uri"), cfg.get("canva_scopes"), challenge, state)
        QDesktopServices.openUrl(QUrl(url))
        self.lbl_canva_status.setText("已打开 Canva 授权页。完成授权后，把浏览器地址栏里的完整回调地址或 code 粘到下方再点兑换授权。")
        self.lbl_canva_status.setStyleSheet("color:#89b4fa; font-size:12px; border:none;")

    def _extract_canva_code(self, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "code=" in raw:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(raw)
                code = parse_qs(parsed.query).get("code", [""])[0]
                return code.strip()
            except Exception:
                pass
            return raw.split("code=", 1)[1].split("&", 1)[0].strip()
        return raw

    def exchange_canva_authorization_code_ui(self):
        cfg = self._merge_canva_config()
        code = self._extract_canva_code(self.txt_canva_auth_code.text() if hasattr(self, "txt_canva_auth_code") else "")
        if not code:
            QMessageBox.warning(self, "Canva 授权", "请先粘贴 Canva 回调 code 或完整回调地址。")
            return
        verifier = str(cfg.get("canva_oauth_code_verifier") or "").strip()
        if not verifier:
            QMessageBox.warning(self, "Canva 授权", "缺少 PKCE verifier，请重新点击“开始授权”。")
            return
        try:
            token_data = exchange_authorization_code(
                cfg.get("canva_client_id"),
                cfg.get("canva_client_secret"),
                code,
                verifier,
                cfg.get("canva_redirect_uri"),
            )
            cfg = store_token_response(cfg, token_data)
            cfg.pop("canva_oauth_code_verifier", None)
            cfg.pop("canva_oauth_state", None)
            save_app_config(cfg)
            self.txt_canva_auth_code.clear()
            self.load_canva_ui(cfg)
            QMessageBox.information(self, "Canva 授权", "Canva 授权成功，Token 已保存。")
        except Exception as e:
            QMessageBox.critical(self, "Canva 授权失败", str(e))

    def load_ai_transcription_ui(self, config=None):
        if not hasattr(self, "txt_groq_keys"):
            return
        config = config if isinstance(config, dict) else load_app_config()
        keys = config.get("groq_api_keys", [])
        if isinstance(keys, str):
            keys = [item.strip() for item in keys.replace(",", "\n").splitlines() if item.strip()]
        elif not isinstance(keys, list):
            keys = []
        legacy_key = str(config.get("groq_api_key") or "").strip()
        if legacy_key and legacy_key not in keys:
            keys.insert(0, legacy_key)
        self.txt_groq_keys.setPlainText("\n".join(str(item).strip() for item in keys if str(item).strip()))
        model = str(config.get("groq_whisper_model") or DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
        if hasattr(self, "groq_model_combo"):
            if self.groq_model_combo.findText(model) < 0:
                self.groq_model_combo.addItem(model)
            self.groq_model_combo.setCurrentText(model)
        if hasattr(self, "txt_groq_language"):
            self.txt_groq_language.setText(str(config.get("groq_transcription_language") or "en").strip() or "en")
        self._set_transcription_priority_order(config.get("ai_transcription_provider_order", ["groq", "cloudflare"]))

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.txt_sync_url.setText(config.get("sync_url", DEFAULT_SYNC_URL))
                    accounts = config.get("cf_accounts", [])
                    if accounts:
                        # 将 JSON 里的账号还原为多行文本展示
                        lines = [f"{acc.get('id', '')},{acc.get('token', '')}" for acc in accounts]
                        self.txt_accounts.setPlainText("\n".join(lines))
            except: pass
        else:
            self.txt_sync_url.setText(DEFAULT_SYNC_URL)
        self.load_font_registry_ui()
        self.refresh_font_asset_label()
        self.refresh_hardware_profile_label()
        if hasattr(self, "output_resolution_combo"):
            current_resolution = get_output_resolution()
            self.output_resolution_combo.blockSignals(True)
            self.output_resolution_combo.setCurrentText(current_resolution)
            self.output_resolution_combo.blockSignals(False)
            self.lbl_output_resolution.setText(f"当前固定画布：{current_resolution}")
        if hasattr(self, "preview_proxy_resolution_combo"):
            current_preview_resolution = get_preview_proxy_resolution()
            self.preview_proxy_resolution_combo.blockSignals(True)
            self.preview_proxy_resolution_combo.setCurrentText(current_preview_resolution)
            self.preview_proxy_resolution_combo.blockSignals(False)
            self.lbl_preview_proxy_resolution.setText(f"当前预览代理：{current_preview_resolution}（不影响导出）")
        if hasattr(self, "preview_fullscreen_shortcut_edit"):
            shortcut = get_preview_fullscreen_shortcut()
            self.preview_fullscreen_shortcut_edit.blockSignals(True)
            self.preview_fullscreen_shortcut_edit.setKeySequence(QKeySequence(shortcut))
            self.preview_fullscreen_shortcut_edit.blockSignals(False)
            self.lbl_shortcuts_status.setText(f"当前预览全屏快捷键：{shortcut}")
        app_cfg = load_app_config()
        self.load_ai_transcription_ui(app_cfg)
        self.load_canva_ui(app_cfg)
        self.load_update_ui()

    def load_update_ui(self):
        if not hasattr(self, "txt_update_repo"):
            return
        cfg = load_update_config()
        self.txt_update_repo.setText(cfg.get("repo", ""))
        self.txt_update_version.setText(cfg.get("current_version", "0.1.0"))
        self.txt_update_dir.setText(cfg.get("download_dir", ""))
        self.chk_update_auto.setChecked(bool(cfg.get("auto_check", True)))
        self.lbl_update_status.setText(f"下载目录：{release_download_dir(cfg)}")
        if cfg.get("auto_check", True) and cfg.get("repo") and not self._auto_update_check_started:
            self._auto_update_check_started = True
            QTimer.singleShot(1800, self.check_updates_ui)

    def _current_update_config_from_ui(self):
        return {
            "repo": normalize_repo(self.txt_update_repo.text()),
            "current_version": self.txt_update_version.text().strip() or "0.1.0",
            "download_dir": self.txt_update_dir.text().strip(),
            "auto_check": self.chk_update_auto.isChecked(),
        }

    def save_update_ui(self):
        cfg = save_update_config(self._current_update_config_from_ui())
        self.txt_update_repo.setText(cfg.get("repo", ""))
        self.lbl_update_status.setText(f"更新设置已保存。下载目录：{release_download_dir(cfg)}")
        return cfg

    def select_update_download_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择更新包下载目录", self.txt_update_dir.text().strip() or os.getcwd())
        if folder:
            self.txt_update_dir.setText(folder)
            self.save_update_ui()

    def check_updates_ui(self):
        cfg = self.save_update_ui()
        self.btn_update_check.setEnabled(False)
        self.btn_update_download.setEnabled(False)
        self.lbl_update_status.setText("正在检查 GitHub Release...")
        threading.Thread(target=self._check_updates_thread, args=(cfg,), daemon=True).start()

    def _check_updates_thread(self, cfg):
        try:
            release = check_latest_release(cfg.get("repo", ""), cfg.get("current_version", ""))
            self.sig_update_checked.emit(True, "", release)
        except Exception as e:
            self.sig_update_checked.emit(False, str(e), {})

    def on_update_checked(self, ok, message, release):
        self.btn_update_check.setEnabled(True)
        if not ok:
            self._latest_release = None
            self.lbl_update_status.setText(f"检查失败：{message}")
            return
        self._latest_release = release
        asset = pick_release_asset(release)
        asset_text = f"\n可下载：{asset.get('name')}" if asset else "\n没有找到可下载资源。"
        newer = "发现新版本" if release.get("is_newer") else "当前已是最新或版本号未递增"
        self.lbl_update_status.setText(
            f"{newer}：{release.get('tag_name')}\n"
            f"发布时间：{release.get('published_at', '')}\n"
            f"Release：{release.get('html_url', '')}{asset_text}"
        )
        self.btn_update_download.setEnabled(bool(asset))

    def download_update_ui(self):
        if not self._latest_release:
            return QMessageBox.information(self, "请先检查", "请先检查更新，再下载最新安装包。")
        asset = pick_release_asset(self._latest_release)
        if not asset:
            return QMessageBox.warning(self, "没有资源", "这个 Release 没有可下载的安装包资源。")
        cfg = self.save_update_ui()
        target_dir = release_download_dir(cfg)
        self.btn_update_download.setEnabled(False)
        self._update_progress_dialog = QProgressDialog("正在下载更新包...", "取消", 0, 100, self)
        self._update_progress_dialog.setWindowTitle("下载更新")
        self._update_progress_dialog.setAutoClose(False)
        self._update_progress_dialog.setValue(0)
        threading.Thread(target=self._download_update_thread, args=(asset, target_dir), daemon=True).start()

    def _download_update_thread(self, asset, target_dir):
        try:
            def progress(done, total):
                self.sig_update_progress.emit(int(done), int(total))
            path = download_asset(asset, target_dir, progress_callback=progress)
            self.sig_update_downloaded.emit(True, path)
        except Exception as e:
            self.sig_update_downloaded.emit(False, str(e))

    def on_update_progress(self, done, total):
        if not self._update_progress_dialog:
            return
        if total > 0:
            self._update_progress_dialog.setValue(max(0, min(100, int(done * 100 / total))))
        else:
            self._update_progress_dialog.setValue(0)

    def on_update_downloaded(self, ok, message):
        self.btn_update_download.setEnabled(bool(self._latest_release))
        if self._update_progress_dialog:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        if ok:
            self.lbl_update_status.setText(f"更新包已下载：\n{message}")
            QMessageBox.information(self, "下载完成", f"更新包已保存到：\n{message}")
        else:
            self.lbl_update_status.setText(f"下载失败：{message}")
            QMessageBox.critical(self, "下载失败", message)

    def load_font_registry_ui(self):
        if not hasattr(self, "txt_approved_fonts"):
            return
        try:
            data = load_font_registry()
            fonts = data.get("fonts", {})
            registered = [
                name for name, record in sorted(fonts.items(), key=lambda item: item[0].casefold())
                if isinstance(record, dict)
                and record.get("status") == STATUS_NONCOMMERCIAL
                and record.get("commercial_use") in ("personal_only_registered", "approved_by_user")
            ]
            self.txt_approved_fonts.setPlainText("\n".join(registered))
            approved = registered
            open_count = sum(1 for record in fonts.values() if isinstance(record, dict) and record.get("status") == "open")
            restricted_count = sum(1 for record in fonts.values() if isinstance(record, dict) and record.get("status") == STATUS_NONCOMMERCIAL)
            review_count = sum(1 for record in fonts.values() if isinstance(record, dict) and record.get("status") == "review")
            self.lbl_font_registry.setText(f"登记文件: {FONT_REGISTRY_FILE}\n开源白名单 {open_count} 个；手动确认 {len(approved)} 个；待确认 {review_count} 个。")
            self.lbl_font_registry.setText(f"登记文件: {FONT_REGISTRY_FILE}\n开源打包 {open_count} 个；个人/不可商用登记 {len(registered)} 个；待确认 {review_count} 个。")
            if restricted_count:
                self.lbl_font_registry.setText(self.lbl_font_registry.text() + f"\nRestricted/non-commercial {restricted_count} fonts.")
        except Exception as e:
            self.lbl_font_registry.setText(f"字体登记读取失败: {e}")

    def refresh_font_asset_label(self):
        if not hasattr(self, "lbl_font_assets"):
            return
        try:
            summary = font_asset_summary()
            families = summary.get("families", [])
            family_text = ", ".join(families[:10])
            if len(families) > 10:
                family_text += f" ... +{len(families) - 10}"
            if not family_text:
                family_text = "尚未放入字体文件"
            self.lbl_font_assets.setText(
                f"开源字体包: {summary.get('font_file_count', 0)} 个文件 / "
                f"个人字体: {summary.get('personal_font_file_count', 0)} 个文件 / "
                f"{summary.get('family_count', 0)} 个字体族\n"
                f"开源目录: {summary.get('fonts_dir')}\n"
                f"个人目录: {summary.get('personal_fonts_dir')}\n"
                f"已识别: {family_text}"
            )
        except Exception as e:
            self.lbl_font_assets.setText(f"读取内置字体包失败: {e}")

    def refresh_font_assets_ui(self):
        try:
            loaded = register_bundled_fonts()
            self.load_font_registry_ui()
            self.refresh_font_asset_label()
            family_count = sum(len(item.get("families", [])) for item in loaded)
            QMessageBox.information(self, "内置字体包已刷新", f"已扫描并注册 {len(loaded)} 个字体文件，识别 {family_count} 个字体族。")
        except Exception as e:
            QMessageBox.warning(self, "刷新失败", str(e))

    def open_fonts_dir_ui(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(ensure_personal_fonts_dir()))

    def save_font_registry_ui(self):
        names = [line.strip() for line in self.txt_approved_fonts.toPlainText().splitlines() if line.strip()]
        try:
            upsert_approved_fonts(names)
            self.load_font_registry_ui()
            QMessageBox.information(self, "已保存", "已更新字体版权登记。工程大厅体检会按这份清单标记字体状态。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def reset_open_font_policy_ui(self):
        reply = QMessageBox.question(
            self,
            "整理为开源字体库",
            "这会保留内置开源字体白名单，并移除你手动标记的“已确认字体”。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            reset_to_open_font_policy()
            self.load_font_registry_ui()
            QMessageBox.information(self, "已整理", "已切换为开源字体白名单策略。系统字体和未登记字体会继续在体检中提示复核。")
        except Exception as e:
            QMessageBox.critical(self, "整理失败", str(e))

    def refresh_hardware_profile_label(self, profile=None):
        profile = profile if profile is not None else peek_render_profile()
        if not profile or not profile.get("encoder"):
            self.lbl_hardware_profile.setText("尚未扫描。点击“扫描显卡/CPU并自动配置”后会写入 settings.json；如果没有手动扫描，首次渲染也会自动生成配置。")
            return
        self.lbl_hardware_profile.setText(describe_render_profile(profile))

    def scan_hardware_profile(self):
        self.btn_scan_hardware.setEnabled(False)
        self.btn_cpu_safe.setEnabled(False)
        self.lbl_hardware_profile.setText("正在扫描显卡、CPU、内存和 FFmpeg 编码器，请稍候...")
        threading.Thread(target=self._scan_hardware_thread, daemon=True).start()

    def _scan_hardware_thread(self):
        try:
            profile = detect_hardware_profile(save=True)
            message = describe_render_profile(profile)
            self.sig_hardware_finished.emit(True, message, profile)
        except Exception as e:
            self.sig_hardware_finished.emit(False, str(e), {})

    def use_cpu_render_profile(self):
        try:
            profile = cpu_safe_profile(save=True)
            self.refresh_hardware_profile_label(profile)
            QMessageBox.information(self, "CPU 安全模式已启用", "已切换为 CPU x264 渲染。导出和批量流水线都会使用这个安全配置。")
        except Exception as e:
            QMessageBox.critical(self, "切换失败", f"无法保存 CPU 安全模式：\n{str(e)}")

    def on_hardware_finished(self, ok, message, profile):
        self.btn_scan_hardware.setEnabled(True)
        self.btn_cpu_safe.setEnabled(True)
        if ok:
            self.refresh_hardware_profile_label(profile)
            QMessageBox.information(self, "硬件配置完成", f"已根据当前设备自动选择渲染配置：\n\n{message}")
        else:
            self.refresh_hardware_profile_label()
            QMessageBox.critical(self, "硬件扫描失败", message)

    def save_config(self):
        raw_text = self.txt_accounts.toPlainText().strip()
        lines = raw_text.split('\n')

        valid_accounts = []
        for line in lines:
            line = line.strip()
            if not line: continue

            # 智能兼容：把中文逗号替换成英文逗号
            line = line.replace('，', ',')

            # 智能拆分：逗号或空格隔开的都能识别
            if ',' in line:
                parts = line.split(',', 1)
            else:
                parts = line.split(maxsplit=1)

            if len(parts) == 2:
                acc_id = parts[0].strip()
                acc_token = parts[1].strip()
                if acc_id and acc_token:
                    valid_accounts.append({"id": acc_id, "token": acc_token})

        if not valid_accounts and raw_text:
            QMessageBox.warning(self, "格式错误", "没有解析到有效的账号！\n请确保 Account ID 和 Token 之间有逗号或空格分隔。")
            return

        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except: pass

        # 写入 cf_accounts 数组，完美对接房间 1 和 2 的负载均衡
        config["cf_accounts"] = valid_accounts
        config["sync_url"] = self.txt_sync_url.text().strip() or DEFAULT_SYNC_URL
        config["groq_api_keys"] = self._parse_groq_keys_from_ui()
        config["groq_whisper_model"] = self.groq_model_combo.currentText().strip() if hasattr(self, "groq_model_combo") else DEFAULT_GROQ_MODEL
        config["groq_transcription_language"] = self.txt_groq_language.text().strip() if hasattr(self, "txt_groq_language") else "en"
        config["ai_transcription_provider_order"] = self._current_transcription_priority_order()
        if hasattr(self, "txt_canva_client_id"):
            config.update(self._current_canva_config_from_ui())

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            canva_state = "，Canva 已授权" if config.get("canva_access_token") or config.get("canva_refresh_token") else "，Canva 未授权"
            QMessageBox.information(self, "\u4fdd\u5b58\u6210\u529f", f"\u2705 Cloudflare {len(valid_accounts)} \u4e2a\uff0cGroq {len(config.get('groq_api_keys', []))} \u4e2a{canva_state}\u3002\nAI \u542c\u8bd1\u4f1a\u6309\u4f60\u8bbe\u7f6e\u7684\u4f18\u5148\u7ea7\u81ea\u52a8\u5c1d\u8bd5\u3002")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存配置文件：\n{str(e)}")

    def sync_from_worker(self):
        url = self.txt_sync_url.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先填写 Cloudflare Workers 链接。")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
            self.txt_sync_url.setText(url)

        self.lbl_sync_status.setText("正在连接 Workers 并识别 API...")
        self.save_sync_url_only(url)
        threading.Thread(target=self._sync_worker_thread, args=(url,), daemon=True).start()

    def save_sync_url_only(self, url):
        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}
        config["sync_url"] = url
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def _sync_worker_thread(self, url):
        try:
            cloud_secret = load_cloud_secret()
            if not cloud_secret:
                raise Exception("Cloud sync secret is not configured. Set SUBTITLE_COMPOSER_CLOUD_SECRET or add cloud_secret in local settings.json.")
            headers = {"X-App-Auth": cloud_secret}
            res = requests.get(url, headers=headers, timeout=12)
            if res.status_code == 401:
                raise Exception("云端拒绝访问：密钥错误或 Workers 鉴权不通过。")
            res.raise_for_status()
            data = res.json()
            accounts = data.get("cf_accounts", [])
            if not isinstance(accounts, list):
                raise Exception("Workers 返回格式不正确：cf_accounts 不是数组。")

            valid_accounts = []
            for item in accounts:
                if isinstance(item, dict) and item.get("id") and item.get("token"):
                    valid_accounts.append({"id": item.get("id"), "token": item.get("token")})
            if not valid_accounts:
                raise Exception("没有识别到有效 Cloudflare API 账号。Workers 需要返回 cf_accounts。")

            config = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        config = json.load(f)
                except Exception:
                    config = {}
            config["sync_url"] = url
            config["cf_accounts"] = valid_accounts
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            self.sig_sync_finished.emit(True, f"成功识别并同步 {len(valid_accounts)} 个 Cloudflare API 账号。", valid_accounts)
        except Exception as e:
            self.sig_sync_finished.emit(False, str(e), [])

    def on_sync_finished(self, ok, message, accounts):
        if ok:
            lines = [f"{acc.get('id', '')},{acc.get('token', '')}" for acc in accounts]
            self.txt_accounts.setPlainText("\n".join(lines))
            self.lbl_sync_status.setText(message)
            QMessageBox.information(self, "同步成功", message)
        else:
            self.lbl_sync_status.setText("同步失败")
            QMessageBox.critical(self, "同步失败", message)
