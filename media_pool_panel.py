import os
import subprocess

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QApplication,
)


class MediaPoolPanel(QFrame):
    importRequested = pyqtSignal()
    addRequested = pyqtSignal()
    refreshRequested = pyqtSignal()
    selectionChangedPayload = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mediaPoolPanel")
        self.setStyleSheet(self.panel_style(False))

        root = QVBoxLayout(self)
        root.setContentsMargins(9, 8, 9, 9)
        root.setSpacing(6)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(QLabel("MEDIA POOL", styleSheet="color:#89b4fa; font-weight:900; font-size:12px; letter-spacing:0px;"))
        title_box.addWidget(QLabel("素材池", styleSheet="color:#ffffff; font-weight:900; font-size:13px;"))
        header.addLayout(title_box)
        header.addStretch()
        self.count_label = QLabel("0 个")
        self.count_label.setStyleSheet("color:#a6adc8; font-size:11px;")
        header.addWidget(self.count_label)
        root.addLayout(header)

        self.list_widget = QListWidget()
        self.list_widget.setFixedHeight(122)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.currentItemChanged.connect(self._emit_selection)
        self.list_widget.itemDoubleClicked.connect(lambda *_: self.addRequested.emit())
        root.addWidget(self.list_widget)

        actions = QHBoxLayout()
        self.import_button = QPushButton("导入")
        self.import_button.setFixedHeight(26)
        self.import_button.setStyleSheet("background-color:#f9e2af; color:#11111b; border-radius:5px; font-weight:900;")
        self.import_button.clicked.connect(self.importRequested.emit)
        self.add_button = QPushButton("入线")
        self.add_button.setFixedHeight(26)
        self.add_button.setStyleSheet("background-color:#89b4fa; color:#11111b; border-radius:5px; font-weight:800;")
        self.add_button.clicked.connect(self.addRequested.emit)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setFixedHeight(26)
        self.refresh_button.setStyleSheet("background-color:#242b3f; color:#cdd6f4; border:1px solid #3a425a; border-radius:5px; font-weight:800;")
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        actions.addWidget(self.import_button)
        actions.addWidget(self.add_button)
        actions.addWidget(self.refresh_button)
        root.addLayout(actions)

    @staticmethod
    def panel_style(highlight=False):
        border = "#89b4fa" if highlight else "#2d3548"
        return f"""
            QFrame#mediaPoolPanel {{ background-color: #111620; border: 1px solid {border}; border-radius: 8px; }}
            QLabel {{ color: #cdd6f4; border: none; }}
            QListWidget {{ background-color: #0d111a; color: #cdd6f4; border: 1px solid #252c3d; border-radius: 6px; padding: 3px; outline: none; }}
            QListWidget::item {{ min-height: 24px; padding: 3px 6px; border-radius: 4px; }}
            QListWidget::item:selected {{ background-color: #3f6fb5; color: white; }}
        """

    def set_highlighted(self, highlighted):
        self.setStyleSheet(self.panel_style(bool(highlighted)))

    def set_items(self, items):
        current_payload = self.current_payload()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for label, payload in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, payload)
            path = str((payload or {}).get("path", "") or "") if isinstance(payload, dict) else ""
            if path:
                item.setToolTip(path)
            self.list_widget.addItem(item)

        count = len(items)
        if not items:
            item = QListWidgetItem("暂无素材，拖入或点击导入")
            item.setData(Qt.ItemDataRole.UserRole, {"type": "empty"})
            self.list_widget.addItem(item)

        selected = 0
        if current_payload:
            for row in range(self.list_widget.count()):
                payload = self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
                if payload == current_payload:
                    selected = row
                    break
        if self.list_widget.count():
            self.list_widget.setCurrentRow(selected)
        self.count_label.setText(f"{count} 个")
        self.list_widget.blockSignals(False)
        self._emit_selection(self.list_widget.currentItem(), None)

    def current_payload(self):
        item = self.list_widget.currentItem()
        payload = item.data(Qt.ItemDataRole.UserRole) if item else {}
        return payload if isinstance(payload, dict) else {}

    def _emit_selection(self, current, _previous):
        payload = current.data(Qt.ItemDataRole.UserRole) if current else {}
        self.selectionChangedPayload.emit(payload if isinstance(payload, dict) else {})

    def _payload_path(self, payload):
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("path") or payload.get("source_path") or payload.get("original_path") or "").strip()

    def _open_path_folder(self, path):
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

    def _open_file(self, path):
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

    def _show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        payload = item.data(Qt.ItemDataRole.UserRole)
        path = self._payload_path(payload)
        if not path:
            return
        menu = QMenu(self)
        show_action = menu.addAction("显示完整路径")
        copy_action = menu.addAction("复制素材路径")
        folder_action = menu.addAction("打开所在文件夹")
        file_action = menu.addAction("打开素材文件")
        if not os.path.exists(path):
            file_action.setEnabled(False)
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen == show_action:
            QMessageBox.information(self, "素材完整路径", path)
        elif chosen == copy_action:
            QApplication.clipboard().setText(path)
        elif chosen == folder_action:
            self._open_path_folder(path)
        elif chosen == file_action:
            self._open_file(path)
