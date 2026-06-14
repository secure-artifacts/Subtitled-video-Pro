import unittest

from room_theme_bridge import apply_room_theme_bridge, themed_panel_qss
from theme_tokens import DEFAULT_THEME


class FakeWidget:
    def __init__(self):
        self.qss = ""

    def setStyleSheet(self, qss):
        self.qss = qss


class FakeRoom(FakeWidget):
    def __init__(self):
        super().__init__()
        self.btn_save = FakeWidget()
        self.btn_cancel = FakeWidget()
        self.btn_batch_pause = FakeWidget()
        self.btn_export_cancel = FakeWidget()
        self._theme_tokens = None


class RoomThemeBridgeTests(unittest.TestCase):
    def test_apply_bridge_styles_room_and_known_buttons(self):
        room = FakeRoom()

        apply_room_theme_bridge(room, "graphite")

        self.assertIn("QWidget", room.qss)
        self.assertIn(DEFAULT_THEME.accent, room.btn_save.qss)
        self.assertIn(DEFAULT_THEME.danger, room.btn_cancel.qss)
        self.assertIn(DEFAULT_THEME.warning, room.btn_batch_pause.qss)
        self.assertIn(DEFAULT_THEME.danger, room.btn_export_cancel.qss)
        self.assertEqual(room._theme_tokens, DEFAULT_THEME)

    def test_panel_qss_uses_theme_values(self):
        qss = themed_panel_qss({"panel": "#111111", "border": "#222222"})

        self.assertIn("#111111", qss)
        self.assertIn("#222222", qss)


if __name__ == "__main__":
    unittest.main()
