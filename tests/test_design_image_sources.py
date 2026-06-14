import tempfile
import unittest
import importlib.util
from pathlib import Path

HAS_PYQT = importlib.util.find_spec("PyQt6") is not None

if HAS_PYQT:
    from ui_components import design_image_source, normalize_design_room_state, render_design_html


@unittest.skipUnless(HAS_PYQT, "PyQt6 is required by ui_components")
class DesignImageSourceTests(unittest.TestCase):
    def test_proxy_path_is_preferred_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy = Path(tmp) / "proxy.jpg"
            original = Path(tmp) / "original.jpg"
            proxy.write_bytes(b"proxy")
            original.write_bytes(b"original")

            src = design_image_source(
                {
                    "proxy_path": str(proxy),
                    "source_path": str(original),
                    "src": str(original),
                }
            )

        self.assertIn("proxy.jpg", src)
        self.assertTrue(src.startswith("file://"))

    def test_missing_proxy_falls_back_to_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_proxy = Path(tmp) / "proxy.jpg"
            original = Path(tmp) / "original.jpg"
            original.write_bytes(b"original")

            src = design_image_source(
                {
                    "proxy_path": str(missing_proxy),
                    "path": str(missing_proxy),
                    "source_path": str(original),
                }
            )

        self.assertIn("original.jpg", src)

    def test_normalize_preserves_proxy_metadata_for_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy = Path(tmp) / "proxy.jpg"
            proxy.write_bytes(b"proxy")
            state = {
                "width": 1080,
                "height": 1920,
                "pages": [
                    {
                        "duration": 5,
                        "layers": [
                            {
                                "type": "image",
                                "proxy_path": str(proxy),
                                "src": "missing",
                                "width": 100,
                                "height": 100,
                            }
                        ],
                    }
                ],
            }
            clean = normalize_design_room_state(state)
            html = render_design_html(clean, 0.1)

        self.assertIn("proxy.jpg", html)


if __name__ == "__main__":
    unittest.main()
