import unittest

from project_sidebar_state import SidebarState, apply_sidebar_state


class FakeSidebar:
    def __init__(self):
        self.min_width = None
        self.max_width = None

    def setMinimumWidth(self, value):
        self.min_width = value

    def setMaximumWidth(self, value):
        self.max_width = value


class FakeButton:
    def __init__(self):
        self.text = ""
        self.tooltip = ""

    def setText(self, value):
        self.text = value

    def setToolTip(self, value):
        self.tooltip = value


class ProjectSidebarStateTests(unittest.TestCase):
    def test_sidebar_defaults_collapsed(self):
        state = SidebarState()

        self.assertFalse(state.expanded)
        self.assertEqual(state.width, 54)
        self.assertTrue(state.tooltip)

    def test_toggle_updates_width_and_arrow(self):
        state = SidebarState()
        collapsed_arrow = state.arrow
        state.toggle()

        self.assertTrue(state.expanded)
        self.assertEqual(state.width, 260)
        self.assertNotEqual(state.arrow, collapsed_arrow)

    def test_apply_sidebar_state_updates_width_and_button(self):
        sidebar = FakeSidebar()
        button = FakeButton()
        state = SidebarState(expanded=False, collapsed_width=50, expanded_width=280)

        apply_sidebar_state(sidebar, button, state)
        self.assertEqual(sidebar.min_width, 50)
        self.assertEqual(sidebar.max_width, 50)
        self.assertEqual(button.text, state.arrow)

        state.set_expanded(True)
        apply_sidebar_state(sidebar, button, state)
        self.assertEqual(sidebar.min_width, 280)
        self.assertGreater(sidebar.max_width, 100000)


if __name__ == "__main__":
    unittest.main()
