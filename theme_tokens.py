from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ThemeTokens:
    bg: str
    app_bar: str
    panel: str
    panel_alt: str
    panel_soft: str
    border: str
    border_focus: str
    text: str
    text_soft: str
    text_muted: str
    text_faint: str
    accent: str
    accent_2: str
    success: str
    warning: str
    danger: str
    info: str
    input_bg: str
    button_bg: str
    button_hover: str
    selected_bg: str


DEFAULT_THEME = ThemeTokens(
    bg="#0b0f16",
    app_bar="#101620",
    panel="#111722",
    panel_alt="#151c28",
    panel_soft="#1a2230",
    border="#263244",
    border_focus="#3e516d",
    text="#e7edf7",
    text_soft="#c4cedf",
    text_muted="#8d9ab0",
    text_faint="#5f6b80",
    accent="#69d2c0",
    accent_2="#89b4fa",
    success="#a6e3a1",
    warning="#f4c86a",
    danger="#f38ba8",
    info="#b4befe",
    input_bg="#0d111a",
    button_bg="#1b2535",
    button_hover="#243149",
    selected_bg="#22324a",
)


THEME_PRESETS = {
    "graphite": DEFAULT_THEME,
    "midnight": replace(
        DEFAULT_THEME,
        bg="#090d14",
        app_bar="#0f1520",
        panel="#111827",
        panel_alt="#172033",
        accent="#64d8cb",
        accent_2="#8bb8ff",
        warning="#ffd071",
    ),
    "studio": replace(
        DEFAULT_THEME,
        bg="#0e1117",
        app_bar="#131820",
        panel="#171c24",
        panel_alt="#1e2530",
        border="#303746",
        accent="#72d4b8",
        accent_2="#9ab8ff",
        warning="#e9c46a",
    ),
}


def tokens_from_mapping(raw=None, fallback: ThemeTokens = DEFAULT_THEME) -> ThemeTokens:
    if raw is None:
        return fallback
    if isinstance(raw, ThemeTokens):
        return raw
    if isinstance(raw, str):
        return THEME_PRESETS.get(raw, fallback)
    if not isinstance(raw, dict):
        return fallback
    data = fallback.__dict__.copy()
    alias = {
        "background": "bg",
        "surface": "panel",
        "surface2": "panel_alt",
        "primary": "accent",
        "secondary": "accent_2",
        "muted": "text_muted",
        "highlight": "selected_bg",
    }
    for key, value in raw.items():
        target = alias.get(key, key)
        if target in data and isinstance(value, str) and value.strip():
            data[target] = value.strip()
    return ThemeTokens(**data)


def room_base_qss(tokens=None) -> str:
    t = tokens_from_mapping(tokens)
    return f"""
    QWidget {{
        background-color: {t.bg};
        color: {t.text};
        font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", Arial, sans-serif;
        font-size: 12px;
    }}
    QFrame {{
        background-color: {t.panel};
        border: 1px solid {t.border};
        border-radius: 7px;
    }}
    QLabel {{
        background: transparent;
        border: none;
        color: {t.text_soft};
    }}
    QPushButton {{
        min-height: 26px;
        background-color: {t.button_bg};
        color: {t.text};
        border: 1px solid {t.border_focus};
        border-radius: 7px;
        padding: 4px 9px;
        font-weight: 800;
    }}
    QPushButton:hover {{
        background-color: {t.button_hover};
        border-color: {t.accent_2};
    }}
    QPushButton:checked {{
        background-color: {t.accent_2};
        color: {t.bg};
        border-color: {t.accent_2};
    }}
    QPushButton:disabled {{
        background-color: {t.panel_alt};
        color: {t.text_faint};
        border-color: {t.border};
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        min-height: 26px;
        background-color: {t.input_bg};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 4px 8px;
        selection-background-color: {t.accent_2};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {t.accent_2};
    }}
    QTabWidget::pane {{
        border: 1px solid {t.border};
        border-radius: 7px;
        background-color: {t.panel};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {t.panel_alt};
        color: {t.text_muted};
        border: 1px solid {t.border};
        border-bottom: none;
        padding: 6px 10px;
        font-weight: 800;
    }}
    QTabBar::tab:selected {{
        background-color: {t.panel_soft};
        color: {t.text};
        border-color: {t.border_focus};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: {t.input_bg};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: #344156;
        min-height: 34px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.accent_2};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QSlider::groove:horizontal {{
        height: 5px;
        background: {t.border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t.accent_2};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {t.text};
        width: 12px;
        margin: -4px 0;
        border-radius: 6px;
    }}
    """


def role_qss(role: str, tokens=None) -> str:
    t = tokens_from_mapping(tokens)
    palette = {
        "primary": (t.accent, t.bg),
        "secondary": (t.accent_2, t.bg),
        "success": (t.success, t.bg),
        "warning": (t.warning, "#111315"),
        "danger": (t.danger, "#111315"),
        "quiet": (t.button_bg, t.text),
    }
    bg, fg = palette.get(role, palette["quiet"])
    return f"""
    QPushButton {{
        background-color: {bg};
        color: {fg};
        border: 1px solid {bg};
        border-radius: 7px;
        padding: 4px 9px;
        font-weight: 900;
    }}
    QPushButton:hover {{
        border-color: {t.text};
    }}
    """


def apply_room_theme(widget, tokens=None) -> None:
    if widget is None or not hasattr(widget, "setStyleSheet"):
        return
    widget.setStyleSheet(room_base_qss(tokens))
