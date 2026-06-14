import unittest

from project_board_interactions import (
    BoardItem,
    Rect,
    bounding_rect,
    drag_ghosts,
    contiguous_range_ids,
    hit_test_items,
    merge_selection,
    normalized_rect,
)


class ProjectBoardInteractionTests(unittest.TestCase):
    def test_normalized_rect_accepts_reverse_drag(self):
        rect = normalized_rect(120, 90, 20, 10)

        self.assertEqual(rect, Rect(20.0, 10.0, 100.0, 80.0))

    def test_hit_test_items_selects_intersecting_cards(self):
        items = [
            BoardItem("a", Rect(0, 0, 50, 50)),
            BoardItem("b", Rect(80, 0, 50, 50)),
            BoardItem("c", Rect(180, 0, 50, 50)),
        ]

        selected = hit_test_items(items, Rect(40, 0, 90, 40))

        self.assertEqual(selected, {"a", "b"})

    def test_merge_selection_supports_add_and_subtract(self):
        self.assertEqual(merge_selection({"a"}, {"b"}, additive=True), {"a", "b"})
        self.assertEqual(merge_selection({"a", "b"}, {"b"}, subtractive=True), {"a"})
        self.assertEqual(merge_selection({"a", "b"}, {"c"}), {"c"})

    def test_contiguous_range_ids_supports_shift_selection(self):
        ordered = ["a", "b", "c", "d"]

        self.assertEqual(contiguous_range_ids(ordered, "b", "d"), ["b", "c", "d"])
        self.assertEqual(contiguous_range_ids(ordered, "d", "b"), ["b", "c", "d"])
        self.assertEqual(contiguous_range_ids(ordered, "missing", "c"), ["a", "b", "c"])

    def test_drag_ghosts_keep_item_offsets(self):
        ghosts = drag_ghosts(
            {
                "a": Rect(10, 20, 30, 40),
                "b": Rect(50, 80, 20, 20),
            },
            ["a", "b"],
            delta_x=7,
            delta_y=-5,
        )

        self.assertEqual(ghosts[0].x, 17)
        self.assertEqual(ghosts[0].y, 15)
        self.assertEqual(ghosts[1].x, 57)
        self.assertEqual(ghosts[1].y, 75)
        self.assertAlmostEqual(ghosts[0].opacity, 0.58)

    def test_bounding_rect_wraps_multiple_items(self):
        rect = bounding_rect([Rect(10, 20, 30, 40), Rect(5, 80, 15, 10)])

        self.assertEqual(rect, Rect(5, 20, 35, 70))


if __name__ == "__main__":
    unittest.main()
