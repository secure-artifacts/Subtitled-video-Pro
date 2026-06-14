from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectPalette:
    bg: str = "#0b0f16"
    panel: str = "#111722"
    panel_alt: str = "#151c28"
    panel_hover: str = "#1b2636"
    border: str = "#263244"
    border_strong: str = "#3a4962"
    text: str = "#e7edf7"
    muted: str = "#8d9ab0"
    faint: str = "#5d687b"
    accent: str = "#69d2c0"
    accent_2: str = "#89b4fa"
    warm: str = "#f4c86a"
    danger: str = "#f38ba8"
    selected: str = "#233249"


@dataclass(frozen=True)
class ProjectMetrics:
    rail_collapsed_width: int = 54
    rail_expanded_width: int = 260
    card_radius: int = 7
    card_min_width: int = 168
    card_max_width: int = 212
    card_height: int = 148
    grid_gap: int = 14
    section_gap: int = 12
    toolbar_height: int = 40


def project_room_stylesheet(palette: ProjectPalette | None = None, metrics: ProjectMetrics | None = None) -> str:
    p = palette or ProjectPalette()
    m = metrics or ProjectMetrics()
    return f"""
    QWidget {{
        background-color: {p.bg};
        color: {p.text};
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 12px;
    }}
    QFrame#projectTopBar, QFrame#projectToolbar {{
        background-color: {p.panel};
        border: 1px solid {p.border};
        border-radius: {m.card_radius}px;
    }}
    QFrame#projectSideRail {{
        background-color: #0f141d;
        border-right: 1px solid {p.border};
    }}
    QFrame#projectSidePanel {{
        background-color: {p.panel};
        border-right: 1px solid {p.border};
    }}
    QFrame#projectCard {{
        background-color: {p.panel_alt};
        border: 1px solid {p.border};
        border-radius: {m.card_radius}px;
    }}
    QFrame#projectCard:hover {{
        background-color: {p.panel_hover};
        border-color: {p.border_strong};
    }}
    QFrame#projectCard[selected="true"] {{
        background-color: {p.selected};
        border-color: {p.accent_2};
    }}
    QFrame#projectDragGhost {{
        background-color: rgba(137, 180, 250, 92);
        border: 1px solid rgba(137, 180, 250, 170);
        border-radius: {m.card_radius}px;
    }}
    QLabel#projectTitle {{
        color: {p.text};
        font-size: 18px;
        font-weight: 900;
    }}
    QLabel#projectMeta, QLabel#projectHint {{
        color: {p.muted};
        font-size: 11px;
    }}
    QLineEdit {{
        background-color: #0c1119;
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 7px 10px;
        selection-background-color: {p.accent_2};
    }}
    QPushButton {{
        background-color: #1b2433;
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 7px;
        padding: 7px 10px;
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
    QPushButton#primaryAction {{
        background-color: {p.accent};
        color: #0b0f16;
        border-color: {p.accent};
    }}
    QPushButton#warningAction {{
        background-color: {p.warm};
        color: #111315;
        border-color: {p.warm};
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
        min-height: 36px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p.accent_2};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    """


def project_selection_overlay_stylesheet(palette: ProjectPalette | None = None) -> str:
    p = palette or ProjectPalette()
    return f"""
    QFrame {{
        background-color: rgba(105, 210, 192, 36);
        border: 1px solid {p.accent};
        border-radius: 4px;
    }}
    """


def compact_project_grid_columns(available_width: int, metrics: ProjectMetrics | None = None) -> int:
    m = metrics or ProjectMetrics()
    width = max(1, int(available_width or 1))
    return max(1, width // (m.card_min_width + m.grid_gap))


def project_card_width(available_width: int, metrics: ProjectMetrics | None = None) -> int:
    m = metrics or ProjectMetrics()
    columns = compact_project_grid_columns(available_width, m)
    total_gap = max(0, columns - 1) * m.grid_gap
    raw = (max(1, int(available_width or 1)) - total_gap) // columns
    return max(m.card_min_width, min(m.card_max_width, raw))


def project_card_qss(selected: bool = False, palette: ProjectPalette | None = None) -> str:
    p = palette or ProjectPalette()
    bg = p.selected if selected else p.panel_alt
    border = p.accent_2 if selected else p.border
    return f"""
    QFrame {{
        background-color: {bg};
        border: 1px solid {border};
        border-radius: 7px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    """


def apply_project_room_polish(root, palette: ProjectPalette | None = None, metrics: ProjectMetrics | None = None) -> None:
    if root is None:
        return
    if hasattr(root, "setStyleSheet"):
        root.setStyleSheet(project_room_stylesheet(palette, metrics))
    for name in ("left_panel", "side_panel", "project_side_panel"):
        widget = getattr(root, name, None)
        if widget is not None and hasattr(widget, "setObjectName"):
            widget.setObjectName("projectSidePanel")
    for name in ("top_bar", "toolbar", "project_toolbar"):
        widget = getattr(root, name, None)
        if widget is not None and hasattr(widget, "setObjectName"):
            widget.setObjectName("projectToolbar")
