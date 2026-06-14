from dataclasses import dataclass

from theme_tokens import DEFAULT_THEME, ThemeTokens, room_base_qss


@dataclass(frozen=True)
class UiPalette:
    bg: str = "#0b0f16"
    surface: str = "#111722"
    surface_2: str = "#151c28"
    surface_3: str = "#1b2535"
    border: str = "#263244"
    border_focus: str = "#4c607d"
    text: str = "#e7edf7"
    text_soft: str = "#b8c2d4"
    text_muted: str = "#8792a8"
    text_faint: str = "#5f6b80"
    accent: str = "#69d2c0"
    accent_2: str = "#89b4fa"
    warning: str = "#f4c86a"
    danger: str = "#f38ba8"
    success: str = "#a6e3a1"


@dataclass(frozen=True)
class UiDensity:
    radius: int = 7
    control_h: int = 30
    compact_control_h: int = 26
    icon_button: int = 28
    panel_padding: int = 10
    section_gap: int = 10
    row_gap: int = 6
    font_size: int = 12
    small_font_size: int = 10
    title_font_size: int = 16


def app_font_stack() -> str:
    return '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", Arial, sans-serif'


def professional_app_qss(palette: UiPalette | None = None, density: UiDensity | None = None) -> str:
    p = palette or UiPalette()
    d = density or UiDensity()
    return f"""
    QWidget {{
        background-color: {p.bg};
        color: {p.text};
        font-family: {app_font_stack()};
        font-size: {d.font_size}px;
    }}
    QFrame {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: {d.radius}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {p.text_soft};
    }}
    QLabel[role="title"] {{
        color: {p.text};
        font-size: {d.title_font_size}px;
        font-weight: 900;
    }}
    QLabel[role="meta"] {{
        color: {p.text_muted};
        font-size: {d.small_font_size}px;
    }}
    QPushButton {{
        min-height: {d.compact_control_h}px;
        background-color: {p.surface_3};
        color: {p.text};
        border: 1px solid {p.border_focus};
        border-radius: {d.radius}px;
        padding: 4px 9px;
        font-weight: 800;
    }}
    QPushButton:hover {{
        background-color: #243049;
        border-color: {p.accent_2};
    }}
    QPushButton:checked {{
        background-color: {p.accent_2};
        color: #0b0f16;
        border-color: {p.accent_2};
    }}
    QPushButton:disabled {{
        background-color: #161c27;
        color: {p.text_faint};
        border-color: {p.border};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        min-height: {d.compact_control_h}px;
        background-color: #0d111a;
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: {d.radius}px;
        padding: 4px 8px;
        selection-background-color: {p.accent_2};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {p.accent_2};
    }}
    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: {d.radius}px;
        background-color: {p.surface};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: #141b27;
        color: {p.text_muted};
        border: 1px solid {p.border};
        border-bottom: none;
        padding: 6px 10px;
        font-weight: 800;
    }}
    QTabBar::tab:selected {{
        background-color: {p.surface_3};
        color: {p.text};
        border-color: {p.border_focus};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: #0c1119;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: #344156;
        min-height: 34px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p.accent_2};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QSlider::groove:horizontal {{
        height: 5px;
        background: #263244;
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {p.accent_2};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {p.text};
        width: 12px;
        margin: -4px 0;
        border-radius: 6px;
    }}
    """


def compact_panel_qss(palette: UiPalette | None = None, density: UiDensity | None = None) -> str:
    p = palette or UiPalette()
    d = density or UiDensity()
    return f"""
    QFrame {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: {d.radius}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    """


def apply_professional_ui(root, palette: UiPalette | None = None, density: UiDensity | None = None) -> None:
    if root is None or not hasattr(root, "setStyleSheet"):
        return
    root.setStyleSheet(professional_app_qss(palette, density))


def professional_app_qss_from_tokens(tokens: ThemeTokens | None = None) -> str:
    return room_base_qss(tokens or DEFAULT_THEME)


def apply_professional_tokens(root, tokens: ThemeTokens | None = None) -> None:
    if root is None or not hasattr(root, "setStyleSheet"):
        return
    root.setStyleSheet(professional_app_qss_from_tokens(tokens))
