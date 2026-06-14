# ==========================================
# 文件名: main.py (工程房间完整版)
# ==========================================
import sys
import os
import threading
import re

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

FORCE_SOFTWARE_RENDERING = os.environ.get("SUBTITLE_FORCE_SOFTWARE_RENDERING", "").strip() == "1"

if sys.platform == "win32":
    os.environ["QT_GL_ADAPTER_TYPE"] = "any"

chromium_flags = ["--ignore-gpu-blocklist", "--num-raster-threads=4"]
if FORCE_SOFTWARE_RENDERING:
    os.environ["QT_OPENGL"] = "software"
    chromium_flags.extend([
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-gpu-rasterization",
    ])
else:
    os.environ.setdefault("QT_OPENGL", "angle" if sys.platform == "win32" else "desktop")
    chromium_flags.append("--enable-gpu-rasterization")

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(chromium_flags)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QStackedWidget, QToolButton,
    QMenu, QCheckBox, QLabel, QMessageBox, QTextEdit, QLineEdit, QAbstractSpinBox, QStyle
)
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtGui import QAction, QKeySequence, QShortcut

from core import auto_sync_cloud_data
from font_assets import ensure_fonts_dir, register_bundled_fonts
from project_io import load_or_create_default_project
from workspace_config import get_active_workspace
from room_project import PROJECT_HALL_THEMES, ProjectView
from room_edit import EditView
from room_batch import BatchView
from room_deliver import DeliverView
from room_settings import SettingsView


class SubtitledvideoPro(QMainWindow):
    def __init__(self, project_data):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self._chrome_drag_pos = None
        self.setWindowTitle("Subtitle Video Pro - 工程房间版")
        self.fit_initial_window_to_screen()
        self.setStyleSheet("background-color: #11111b; color: #cdd6f4;")

        self.project = project_data or {}
        self.rooms = []
        self.current_room_index = 0
        self.current_workspace_key = "project"
        self.room_history = []
        self.room_history_pos = -1
        self.app_settings = QSettings("SubtitleComposer", "SubtitleVideoPro")
        self.auto_save_enabled = self.app_settings.value("auto_save_enabled", True, type=bool)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.create_topbar()
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack, stretch=1)
        self.create_sidebar()
        self.create_rooms()
        self.open_default_room()

    def fit_initial_window_to_screen(self):
        screen = QApplication.primaryScreen()
        if not screen:
            self.resize(1360, 860)
            return

        available = screen.availableGeometry()
        max_w = max(640, available.width() - 32)
        max_h = max(480, available.height() - 40)
        width = min(1600, max(960, int(available.width() * 0.94)), max_w)
        height = min(980, max(660, int(available.height() * 0.90)), max_h)
        self.setMinimumSize(min(900, max_w), min(600, max_h))
        self.resize(width, height)
        self.move(
            available.x() + max(0, (available.width() - width) // 2),
            available.y() + max(0, (available.height() - height) // 2),
        )

    def create_topbar(self):
        self.topbar = QWidget()
        self.topbar.mousePressEvent = self._chrome_mouse_press
        self.topbar.mouseMoveEvent = self._chrome_mouse_move
        self.topbar.mouseReleaseEvent = self._chrome_mouse_release
        self.topbar.mouseDoubleClickEvent = self._chrome_mouse_double_click
        self.topbar.setStyleSheet("""
            QWidget { background-color: #181825; border-bottom: 1px solid #313244; }
            QToolButton, QPushButton {
                background-color: transparent; color: #a6adc8; border: none;
                padding: 7px 9px; border-radius: 6px; font-weight: bold;
            }
            QToolButton:hover, QPushButton:hover { background-color: #313244; color: #cdd6f4; }
            QToolButton:disabled { color: #45475a; }
            QMenu { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244; padding: 6px; }
            QMenu::item { padding: 7px 28px 7px 18px; border-radius: 5px; }
            QMenu::item:selected { background-color: #313244; color: #a6e3a1; }
            QCheckBox { color: #a6e3a1; font-weight: bold; padding: 4px 8px; }
        """)
        layout = QHBoxLayout(self.topbar)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(4)

        self.chrome_title = QLabel("Subtitle Composer")
        self.chrome_title.setStyleSheet("color: #cdd6f4; font-weight: 900; padding: 0 10px 0 4px; border: none;")
        layout.addWidget(self.chrome_title)

        self.btn_toggle_nav = QToolButton()
        self.btn_toggle_nav.setText("☰")
        self.btn_toggle_nav.setToolTip("显示/隐藏底部房间导航")
        self.btn_toggle_nav.clicked.connect(self.toggle_bottom_nav)
        layout.addWidget(self.btn_toggle_nav)

        self.btn_back = QToolButton()
        self.btn_back.setText("‹")
        self.btn_back.setToolTip("返回上一个房间")
        self.btn_back.clicked.connect(self.go_back)
        layout.addWidget(self.btn_back)

        self.btn_forward = QToolButton()
        self.btn_forward.setText("›")
        self.btn_forward.setToolTip("前进到下一个房间")
        self.btn_forward.clicked.connect(self.go_forward)
        layout.addWidget(self.btn_forward)

        layout.addSpacing(6)
        layout.addWidget(self._make_menu_button("文件", self._build_file_menu()))
        layout.addWidget(self._make_menu_button("编辑", self._build_edit_menu()))
        layout.addWidget(self._make_menu_button("检视", self._build_view_menu()))
        layout.addWidget(self._make_menu_button("窗口", self._build_window_menu()))
        layout.addWidget(self._make_menu_button("说明", self._build_help_menu()))

        layout.addSpacing(10)
        self.project_label = QLabel("")
        self.project_label.setStyleSheet("color: #7f849c; border: none; padding-left: 6px;")
        layout.addWidget(self.project_label, stretch=1)

        self.auto_save_checkbox = QCheckBox("自动保存")
        self.auto_save_checkbox.setChecked(self.auto_save_enabled)
        self.auto_save_checkbox.stateChanged.connect(self.set_auto_save_enabled)
        layout.addWidget(self.auto_save_checkbox)

        self.btn_window_min = QToolButton()
        self.btn_window_min.setText("─")
        self.btn_window_min.setToolTip("最小化")
        self.btn_window_min.clicked.connect(self.showMinimized)
        layout.addWidget(self.btn_window_min)

        self.btn_window_max = QToolButton()
        self.btn_window_max.setText("□")
        self.btn_window_max.setToolTip("最大化/还原")
        self.btn_window_max.clicked.connect(self.toggle_max_restore)
        layout.addWidget(self.btn_window_max)

        self.btn_window_close = QToolButton()
        self.btn_window_close.setText("×")
        self.btn_window_close.setToolTip("关闭")
        self.btn_window_close.clicked.connect(self.close)
        layout.addWidget(self.btn_window_close)

        self.main_layout.addWidget(self.topbar)
        self.update_history_buttons()

    def _make_menu_button(self, text, menu):
        btn = QToolButton()
        btn.setText(text)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setMenu(menu)
        return btn

    def _chrome_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._chrome_drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _chrome_mouse_move(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._chrome_drag_pos is not None:
            if self.isMaximized():
                self.showNormal()
            self.move(event.globalPosition().toPoint() - self._chrome_drag_pos)
            event.accept()

    def _chrome_mouse_release(self, event):
        self._chrome_drag_pos = None
        event.accept()

    def _chrome_mouse_double_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_max_restore()
            event.accept()

    def _action(self, text, callback, shortcut=None, checkable=False, checked=False):
        action = QAction(text, self)
        global_shortcuts = {"Ctrl+S", "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z"}
        if shortcut and shortcut not in global_shortcuts:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.setCheckable(checkable)
        if checkable:
            action.setChecked(checked)
            action.triggered.connect(callback)
        else:
            action.triggered.connect(lambda checked=False: callback())
        return action

    def create_global_shortcuts(self):
        shortcuts = [
            ("Ctrl+S", self.shortcut_save_current_stage),
            ("Ctrl+Z", self.shortcut_undo_current_stage),
            ("Ctrl+Y", self.shortcut_redo_current_stage),
            ("Ctrl+Shift+Z", self.shortcut_redo_current_stage),
        ]
        self.global_shortcuts = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self.global_shortcuts.append(shortcut)

    def _focus_is_text_editor(self):
        focused = QApplication.focusWidget()
        parent = focused.parent() if focused is not None else None
        while parent is not None:
            if isinstance(parent, QAbstractSpinBox):
                return False
            parent = parent.parent()
        return isinstance(focused, (QTextEdit, QLineEdit))

    def shortcut_save_current_stage(self):
        if self.current_room_index == 1 and hasattr(self.room_edit, "save_to_project"):
            self.project = self.room_edit.save_to_project(silent=True)
            if hasattr(self.room_edit, "generate_cover_async"):
                self.room_edit.generate_cover_async()
            if hasattr(self.room_edit, "status_lbl"):
                self.room_edit.status_lbl.setText("✅ 工程已保存")
            self.refresh_room_links()
            self.statusBar().showMessage("工程已保存", 2500)
            return
        self.save_current_project(silent=True)
        self.statusBar().showMessage("工程已保存", 2500)

    def shortcut_undo_current_stage(self):
        if self.current_room_index == 1 and hasattr(self.room_edit, "undo") and not self._focus_is_text_editor():
            self.room_edit.undo()
            return
        focused = QApplication.focusWidget()
        if self._focus_is_text_editor() and hasattr(focused, "undo"):
            focused.undo()

    def shortcut_redo_current_stage(self):
        if self.current_room_index == 1 and hasattr(self.room_edit, "redo") and not self._focus_is_text_editor():
            self.room_edit.redo()
            return
        focused = QApplication.focusWidget()
        if self._focus_is_text_editor() and hasattr(focused, "redo"):
            focused.redo()

    def _build_file_menu(self):
        menu = QMenu(self)
        menu.addAction(self._action("新建项目文件夹", self.create_project_folder))
        menu.addAction(self._action("新建 Reel", self.create_reel_in_project))
        menu.addAction(self._action("导入工程文件夹", self.import_project_folder))
        menu.addSeparator()
        menu.addAction(self._action("保存当前工程", lambda: self.save_current_project(False), "Ctrl+S"))
        menu.addAction(self._action("进入工程大厅", lambda: self.switch_room(0)))
        menu.addAction(self._action("打开导出中心", lambda: self.switch_room(3)))
        menu.addSeparator()
        menu.addAction(self._action("退出软件", self.close, "Alt+F4"))
        return menu

    def _build_edit_menu(self):
        menu = QMenu(self)
        menu.addAction(self._action("撤销", self.edit_undo, "Ctrl+Z"))
        menu.addAction(self._action("重做", self.edit_redo, "Ctrl+Y"))
        menu.addSeparator()
        menu.addAction(self._action("检查重叠并整理排版", self.reflow_subtitles))
        return menu

    def _build_view_menu(self):
        menu = QMenu(self)
        menu.addAction(self._action("工程大厅", lambda: self.switch_room(0)))
        menu.addAction(self._action("精修", lambda: self.switch_room(1, workspace_key="edit")))
        menu.addAction(self._action("批量", lambda: self.switch_room(2)))
        menu.addAction(self._action("导出", lambda: self.switch_room(3)))
        menu.addAction(self._action("设置", lambda: self.switch_room(4)))
        menu.addSeparator()
        self.action_show_nav = self._action("显示底部房间导航", self.toggle_bottom_nav_from_menu, checkable=True, checked=True)
        menu.addAction(self.action_show_nav)
        return menu

    def _build_window_menu(self):
        menu = QMenu(self)
        menu.addAction(self._action("最大化/还原", self.toggle_max_restore))
        menu.addAction(self._action("全屏/退出全屏", self.toggle_fullscreen, "F11"))
        return menu

    def _build_help_menu(self):
        menu = QMenu(self)
        menu.addAction(self._action("了解软件架构", self.show_architecture_help))
        menu.addAction(self._action("云端协作说明", self.show_cloud_help))
        return menu

    def create_sidebar(self):
        self.nav_widget = QWidget()
        self.nav_widget.setStyleSheet("background-color: #181825; border-top: 1px solid #313244;")
        nav_layout = QHBoxLayout(self.nav_widget)
        nav_layout.setContentsMargins(10, 4, 10, 4)
        nav_layout.setSpacing(8)

        nav_btn_style = """
            QToolButton { background-color: transparent; color: #a6adc8; border: none; padding: 5px 10px; border-radius: 4px; min-width: 46px; max-width: 60px; }
            QToolButton:hover { background-color: #313244; color: #cdd6f4; }
            QToolButton:checked { background-color: #11111b; color: #a6e3a1; border-bottom: 2px solid #a6e3a1; }
        """

        self.btn_project = QToolButton()
        self.btn_edit = QToolButton()
        self.btn_batch = QToolButton()
        self.btn_deliver = QToolButton()
        self.btn_settings = QToolButton()

        self.nav_buttons = [
            self.btn_project,
            self.btn_edit,
            self.btn_batch,
            self.btn_deliver,
            self.btn_settings,
        ]
        self.nav_button_keys = {
            self.btn_project: "project",
            self.btn_edit: "edit",
            self.btn_batch: "batch",
            self.btn_deliver: "deliver",
            self.btn_settings: "settings",
        }

        for btn in self.nav_buttons:
            btn.setStyleSheet(nav_btn_style)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIconSize(QSize(18, 18))
            btn.setFixedSize(54, 34)
            btn.setAutoRaise(True)

        self.btn_project.setToolTip("工程大厅")
        self.btn_edit.setToolTip("精修时间线")
        self.btn_batch.setToolTip("批量创建")
        self.btn_deliver.setToolTip("导出中心")
        self.btn_settings.setToolTip("设置")
        self.btn_project.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon))
        self.btn_edit.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.btn_batch.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        self.btn_deliver.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.btn_settings.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView))

        nav_layout.addStretch()
        for btn in self.nav_buttons:
            nav_layout.addWidget(btn)
        nav_layout.addStretch()
        self.main_layout.addWidget(self.nav_widget)

        self.btn_project.clicked.connect(lambda: self.switch_room(0))
        self.btn_edit.clicked.connect(lambda: self.switch_room(1, workspace_key="edit"))
        self.btn_batch.clicked.connect(lambda: self.switch_room(2))
        self.btn_deliver.clicked.connect(lambda: self.switch_room(3))
        self.btn_settings.clicked.connect(lambda: self.switch_room(4))

    def apply_chrome_theme(self, theme_key):
        colors = PROJECT_HALL_THEMES.get(theme_key, PROJECT_HALL_THEMES["graphite_cut"])
        self.current_theme_key = theme_key if theme_key in PROJECT_HALL_THEMES else "graphite_cut"
        self.setStyleSheet(f"background-color: {colors['bg']}; color: {colors['text']};")
        if hasattr(self, "topbar"):
            self.topbar.setStyleSheet(f"""
                QWidget {{ background-color: {colors['panel']}; border-bottom: 1px solid {colors['border']}; }}
                QToolButton, QPushButton {{
                    background-color: transparent; color: {colors['muted']}; border: none;
                    padding: 6px 9px; border-radius: 4px; font-weight: bold;
                }}
                QToolButton:hover, QPushButton:hover {{ background-color: {colors['panel_2']}; color: {colors['text']}; }}
                QToolButton:disabled {{ color: {colors['border']}; }}
                QMenu {{ background-color: {colors['panel']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 6px; }}
                QMenu::item {{ padding: 7px 28px 7px 18px; border-radius: 5px; }}
                QMenu::item:selected {{ background-color: {colors['selected']}; color: {colors['selected_text']}; }}
                QCheckBox {{ color: {colors['accent_2']}; font-weight: bold; padding: 4px 8px; }}
            """)
        if hasattr(self, "chrome_title"):
            self.chrome_title.setStyleSheet(f"color: {colors['text']}; font-weight: 900; padding: 0 10px 0 4px; border: none;")
        if hasattr(self, "btn_window_close"):
            self.btn_window_close.setStyleSheet(
                f"QToolButton {{ background-color: transparent; color: {colors['muted']}; border: none; padding: 6px 10px; border-radius: 4px; font-weight: 900; }}"
                f"QToolButton:hover {{ background-color: {colors['danger']}; color: {colors['selected_text']}; }}"
            )
        if hasattr(self, "project_label"):
            self.project_label.setStyleSheet(f"color: {colors['muted']}; border: none; padding-left: 6px;")
        if hasattr(self, "nav_widget"):
            self.nav_widget.setVisible(True)
            self.nav_widget.setStyleSheet(f"background-color: {colors['panel']}; border-top: 1px solid {colors['border']};")
            nav_btn_style = f"""
                QToolButton {{
                    background-color: transparent;
                    color: {colors['muted']};
                    border: none;
                    padding: 5px 10px;
                    border-radius: 4px;
                    min-width: 46px;
                    max-width: 60px;
                }}
                QToolButton:hover {{ background-color: {colors['panel_2']}; color: {colors['text']}; }}
                QToolButton:checked {{ background-color: {colors['hint']}; color: {colors['accent_2']}; border-bottom: 2px solid {colors['accent_2']}; }}
            """
            for btn in getattr(self, "nav_buttons", []):
                btn.setStyleSheet(nav_btn_style)
            if hasattr(self, "action_show_nav"):
                self.action_show_nav.setChecked(True)
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ background-color: {colors['panel']}; color: {colors['muted']}; border-top: 1px solid {colors['border']}; }}"
        )
        for room in getattr(self, "rooms", []):
            if hasattr(room, "apply_theme"):
                room.apply_theme(colors, self.current_theme_key)

    def is_auto_save_enabled(self):
        return bool(self.auto_save_enabled)

    def set_auto_save_enabled(self, state):
        self.auto_save_enabled = state == Qt.CheckState.Checked.value
        self.app_settings.setValue("auto_save_enabled", self.auto_save_enabled)
        if self.auto_save_enabled:
            self.save_current_project(silent=True)
            self.statusBar().showMessage("自动保存已开启", 3000)
        else:
            self.statusBar().showMessage("自动保存已关闭，记得手动保存工程", 4000)

    def toggle_bottom_nav(self):
        visible = not self.nav_widget.isVisible()
        self.nav_widget.setVisible(visible)
        if hasattr(self, "action_show_nav"):
            self.action_show_nav.setChecked(visible)

    def toggle_bottom_nav_from_menu(self, checked):
        self.nav_widget.setVisible(bool(checked))

    def _workspace_key_for_room(self, index, workspace_key=None):
        if workspace_key:
            return workspace_key
        return {
            0: "project",
            1: "edit",
            2: "batch",
            3: "deliver",
            4: "settings",
        }.get(index, "project")

    def _room_history_entry(self, index, workspace_key=None):
        return (int(index), self._workspace_key_for_room(index, workspace_key))

    def _normalize_history_entry(self, entry):
        if isinstance(entry, tuple) and len(entry) == 2:
            return int(entry[0]), str(entry[1])
        return int(entry), self._workspace_key_for_room(int(entry))

    def _update_nav_selection(self):
        active_key = self._workspace_key_for_room(self.current_room_index, self.current_workspace_key)
        for btn in getattr(self, "nav_buttons", []):
            btn.setChecked(getattr(self, "nav_button_keys", {}).get(btn) == active_key)

    def open_design_workspace(self):
        self.switch_room(1, workspace_key="edit")

    def go_back(self):
        if self.room_history_pos > 0:
            self.room_history_pos -= 1
            index, key = self._normalize_history_entry(self.room_history[self.room_history_pos])
            self.switch_room(index, workspace_key=key, record_history=False)

    def go_forward(self):
        if self.room_history_pos < len(self.room_history) - 1:
            self.room_history_pos += 1
            index, key = self._normalize_history_entry(self.room_history[self.room_history_pos])
            self.switch_room(index, workspace_key=key, record_history=False)

    def update_history_buttons(self):
        if hasattr(self, "btn_back"):
            self.btn_back.setEnabled(self.room_history_pos > 0)
        if hasattr(self, "btn_forward"):
            self.btn_forward.setEnabled(self.room_history_pos < len(self.room_history) - 1)

    def save_current_project(self, silent=False):
        try:
            if self.current_room_index == 1 and hasattr(self.room_edit, "save_to_project"):
                self.project = self.room_edit.save_to_project(silent=True)
            elif hasattr(self.room_edit, "save_to_project"):
                self.project = self.room_edit.save_to_project(silent=True)
            self.refresh_room_links()
            if not silent:
                self.statusBar().showMessage("工程已保存", 3000)
                QMessageBox.information(self, "保存成功", "当前工程已经保存。")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"工程保存失败：\n{e}")

    def create_project_folder(self):
        self.switch_room(0)
        if hasattr(self.room_project, "create_new_folder"):
            self.room_project.create_new_folder()

    def create_reel_in_project(self):
        self.switch_room(0)
        if hasattr(self.room_project, "create_new_reel"):
            self.room_project.create_new_reel()

    def import_project_folder(self):
        self.switch_room(0)
        if hasattr(self.room_project, "import_project_folder_dialog"):
            self.room_project.import_project_folder_dialog()

    def edit_undo(self):
        self.switch_room(1)
        if hasattr(self.room_edit, "undo"):
            self.room_edit.undo()

    def edit_redo(self):
        self.switch_room(1)
        if hasattr(self.room_edit, "redo"):
            self.room_edit.redo()

    def reflow_subtitles(self):
        self.switch_room(1)
        if hasattr(self.room_edit, "audit_and_reflow_subtitles"):
            self.room_edit.audit_and_reflow_subtitles()

    def toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_window_control_state()

    def _update_window_control_state(self):
        if hasattr(self, "btn_window_max"):
            self.btn_window_max.setText("❐" if self.isMaximized() else "□")

    def changeEvent(self, event):
        super().changeEvent(event)
        self._update_window_control_state()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def show_architecture_help(self):
        QMessageBox.information(
            self,
            "软件架构",
            "软件分成 5 个工作区：工程大厅、精修、批量、导出、设置。\n\n"
            "工程文件是 .scomp，素材会放入工程 assets；云端模式下 Google Drive 会同步这些工程文件和素材。\n\n"
            "精修负责字幕、音频、样式和逐句调整；批量负责多队列字幕工程与视频生产；导出房间读取当前工程并渲染成视频。"
        )

    def show_cloud_help(self):
        QMessageBox.information(
            self,
            "云端协作说明",
            "推荐每个成员使用自己的 Gmail 登录 Google Drive 桌面版。\n\n"
            "团队共享同一个 Google Drive 文件夹，软件在云端工程大厅里打开 Reel，并用成员 Gmail 写入编辑锁，避免多人同时覆盖。\n\n"
            "云端模式导入素材时会自动复制到当前工程 assets，Google Drive 会继续同步上传。"
        )

    def create_rooms(self):
        self.room_project = ProjectView(self.project, self)
        self.room_edit = EditView(self.project, self)
        self.room_batch = BatchView(self)
        self.room_deliver = DeliverView(self.project, self)
        self.room_settings = SettingsView(self)

        self.rooms = [
            self.room_project,
            self.room_edit,
            self.room_batch,
            self.room_deliver,
            self.room_settings,
        ]
        for room in self.rooms:
            self.stack.addWidget(room)
        self.apply_chrome_theme(getattr(self.room_project, "project_theme", "graphite_cut"))
        self.create_global_shortcuts()

    def open_default_room(self):
        self.switch_room(0, initial=True)

    def _natural_project_sort_key(self, path):
        name = os.path.basename(path or "").lower()
        return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]

    def current_project_folder_progress(self):
        project_path = self.project.get("project_path", "") if isinstance(self.project, dict) else ""
        if not project_path:
            return None
        project_path = os.path.abspath(project_path)
        folder = os.path.dirname(project_path)
        if not os.path.isdir(folder):
            return None
        paths = [
            os.path.abspath(os.path.join(folder, name))
            for name in os.listdir(folder)
            if name.lower().endswith(".scomp") and os.path.isfile(os.path.join(folder, name))
        ]
        if not paths:
            return None
        paths.sort(key=self._natural_project_sort_key)
        normalized_current = os.path.normcase(project_path)
        index = next((i for i, path in enumerate(paths) if os.path.normcase(path) == normalized_current), -1)
        if index < 0:
            return None
        return index + 1, len(paths)

    def refresh_room_links(self):
        if hasattr(self, "project_label"):
            project_name = self.project.get("project_name") or os.path.basename(self.project.get("project_path", "")) or "未命名工程"
            progress = self.current_project_folder_progress()
            progress_text = f"  |  文件夹进度：{progress[0]}/{progress[1]}" if progress else ""
            self.project_label.setText(f"当前工程：{project_name}{progress_text}")

        if hasattr(self, "room_project"):
            self.room_project.project_data = self.project
            self.room_project.sync_current_project_label()

        if hasattr(self.room_edit, "project_data"):
            self.room_edit.project_data = self.project
        if hasattr(self.room_edit, "sync_design_panel_controls"):
            self.room_edit.sync_design_panel_controls()

        if hasattr(self.room_deliver, "project_data"):
            self.room_deliver.project_data = self.project
        if hasattr(self.room_deliver, "load_project_data"):
            self.room_deliver.load_project_data()

    def reload_rooms_from_project(self):
        if hasattr(self.room_edit, "project_data"):
            self.room_edit.project_data = self.project
        if hasattr(self.room_edit, "load_project_on_boot"):
            self.room_edit.load_project_on_boot()

        if hasattr(self.room_deliver, "project_data"):
            self.room_deliver.project_data = self.project
        if hasattr(self.room_deliver, "load_project_data"):
            self.room_deliver.load_project_data()

        self.refresh_room_links()

    def switch_room(self, index, initial=False, record_history=True, workspace_key=None):
        if not initial and self.current_room_index == 1 and hasattr(self.room_edit, "save_to_project"):
            self.project = self.room_edit.save_to_project(silent=True)

        workspace_key = self._workspace_key_for_room(index, workspace_key)
        self.current_room_index = index
        self.current_workspace_key = workspace_key
        if record_history:
            entry = self._room_history_entry(index, workspace_key)
            if self.room_history_pos < len(self.room_history) - 1:
                self.room_history = self.room_history[:self.room_history_pos + 1]
            if not self.room_history or self.room_history[-1] != entry:
                self.room_history.append(entry)
                self.room_history_pos = len(self.room_history) - 1
            elif self.room_history_pos == -1:
                self.room_history_pos = 0
        self.refresh_room_links()
        self.stack.setCurrentIndex(index)

        self._update_nav_selection()

        if index == 1 and hasattr(self.room_edit, "update_floating_subtitle"):
            if hasattr(self.room_edit, "set_workspace_mode"):
                self.room_edit.set_workspace_mode("edit")
            self.room_edit.last_render_hash = None
            self.room_edit.update_floating_subtitle()
        if index == 3 and hasattr(self.room_deliver, "load_project_data"):
            self.room_deliver.load_project_data()
        self.update_history_buttons()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ensure_fonts_dir()
    register_bundled_fonts()
    threading.Thread(target=auto_sync_cloud_data, daemon=True).start()

    workspace = get_active_workspace()
    project_data = load_or_create_default_project(workspace)

    window = SubtitledvideoPro(project_data)
    window.showMaximized()
    sys.exit(app.exec())
