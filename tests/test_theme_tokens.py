import unittest

from theme_tokens import DEFAULT_THEME, role_qss, room_base_qss, tokens_from_mapping


class ThemeTokensTests(unittest.TestCase):
    def test_tokens_from_mapping_accepts_aliases(self):
        tokens = tokens_from_mapping({"background": "#010203", "primary": "#abcdef"})

        self.assertEqual(tokens.bg, "#010203")
        self.assertEqual(tokens.accent, "#abcdef")

    def test_unknown_theme_name_uses_default(self):
        self.assertEqual(tokens_from_mapping("missing").bg, DEFAULT_THEME.bg)

    def test_room_qss_uses_token_colors(self):
        qss = room_base_qss({"bg": "#010203", "accent_2": "#445566"})

        self.assertIn("#010203", qss)
        self.assertIn("#445566", qss)
        self.assertIn("QTabWidget::pane", qss)

    def test_role_qss_uses_role_color(self):
        qss = role_qss("danger")

        self.assertIn(DEFAULT_THEME.danger, qss)


if __name__ == "__main__":
    unittest.main()
