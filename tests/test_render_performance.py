import unittest

from render_performance import export_render_profile


class RenderPerformanceTests(unittest.TestCase):
    def test_export_modes_keep_subtitle_layer_full_scale(self):
        for mode in ("标准高清", "清晰快速", "极速出片"):
            with self.subTest(mode=mode):
                profile = export_render_profile(mode, default_scale=0.5)

                self.assertGreaterEqual(profile["render_scale"], 1.0)


if __name__ == "__main__":
    unittest.main()