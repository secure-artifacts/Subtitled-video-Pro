import unittest

from professional_ui_system import UiDensity, professional_app_qss


class ProfessionalUiSystemTests(unittest.TestCase):
    def test_professional_qss_contains_core_widgets(self):
        qss = professional_app_qss()

        self.assertIn("QPushButton", qss)
        self.assertIn("QLineEdit", qss)
        self.assertIn("QScrollBar:vertical", qss)
        self.assertIn("QTabWidget::pane", qss)

    def test_density_values_are_compact(self):
        density = UiDensity()

        self.assertLessEqual(density.radius, 8)
        self.assertLessEqual(density.compact_control_h, 28)
        self.assertLessEqual(density.font_size, 12)


if __name__ == "__main__":
    unittest.main()
