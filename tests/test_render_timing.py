import unittest

from render_timing import build_subtitle_frame_schedule


class RenderTimingTests(unittest.TestCase):
    def test_active_subtitles_get_minimum_sampling_without_word_times(self):
        schedule = build_subtitle_frame_schedule(
            [{"text": "A long subtitle without word timings", "start": 0.0, "end": 4.0, "style": {"anim_type": "none", "use_hl": False}}],
            4.0,
            event_fps=4,
            continuous_fps=6,
        )
        self.assertGreaterEqual(len(schedule), 16)


if __name__ == "__main__":
    unittest.main()