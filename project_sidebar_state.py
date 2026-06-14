from dataclasses import dataclass


@dataclass
class SidebarState:
    expanded: bool = False
    collapsed_width: int = 54
    expanded_width: int = 260

    @property
    def width(self) -> int:
        return self.expanded_width if self.expanded else self.collapsed_width

    @property
    def arrow(self) -> str:
        return "‹" if self.expanded else "›"

    @property
    def tooltip(self) -> str:
        return "收起工程侧栏" if self.expanded else "展开工程侧栏"

    def toggle(self) -> "SidebarState":
        self.expanded = not self.expanded
        return self

    def set_expanded(self, expanded: bool) -> "SidebarState":
        self.expanded = bool(expanded)
        return self


def apply_sidebar_state(sidebar, toggle_button, state: SidebarState) -> None:
    if sidebar is not None:
        if hasattr(sidebar, "setMinimumWidth"):
            sidebar.setMinimumWidth(state.width)
        if hasattr(sidebar, "setMaximumWidth"):
            sidebar.setMaximumWidth(state.width if not state.expanded else 16777215)
    if toggle_button is not None:
        if hasattr(toggle_button, "setText"):
            toggle_button.setText(state.arrow)
        if hasattr(toggle_button, "setToolTip"):
            toggle_button.setToolTip(state.tooltip)
