import unittest

from project_ui_kit import (
    ProjectMetrics,
    compact_project_grid_columns,
    project_card_width,
    project_room_stylesheet,
    project_selection_overlay_stylesheet,
)


class ProjectUiKitTests(unittest.TestCase):
    def test_grid_columns_never_drop_below_one(self):
        self.assertEqual(compact_project_grid_columns(0), 1)
        self.assertEqual(compact_project_grid_columns(120), 1)

    def test_grid_columns_scale_with_width(self):
        metrics = ProjectMetrics(card_min_width=160, grid_gap=12)

        self.assertEqual(compact_project_grid_columns(520, metrics), 3)

    def test_project_card_width_stays_within_bounds(self):
        metrics = ProjectMetrics(card_min_width=160, card_max_width=210, grid_gap=12)

        self.assertGreaterEqual(project_card_width(120, metrics), 160)
        self.assertLessEqual(project_card_width(1200, metrics), 210)

    def test_stylesheets_include_expected_object_names(self):
        room_qss = project_room_stylesheet()
        selection_qss = project_selection_overlay_stylesheet()

        self.assertIn("QFrame#projectCard", room_qss)
        self.assertIn("QFrame#projectSideRail", room_qss)
        self.assertIn("rgba", selection_qss)


if __name__ == "__main__":
    unittest.main()
