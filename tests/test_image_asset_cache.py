import tempfile
import unittest
from pathlib import Path

from image_asset_cache import (
    DEFAULT_IMAGE_PROXY_MAX_SIDE,
    image_cache_key,
    proxy_path_for_image,
    should_proxy_image,
)


class ImageAssetCacheTests(unittest.TestCase):
    def test_should_proxy_large_2k_and_4k_assets(self):
        self.assertTrue(should_proxy_image(3840, 2160))
        self.assertTrue(should_proxy_image(2048, 2048))
        self.assertFalse(should_proxy_image(1080, 1920))

    def test_cache_key_changes_with_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "poster.jpg"
            path.write_bytes(b"one")
            first = image_cache_key(path, max_side=DEFAULT_IMAGE_PROXY_MAX_SIDE)
            path.write_bytes(b"two-two")
            second = image_cache_key(path, max_side=DEFAULT_IMAGE_PROXY_MAX_SIDE)
        self.assertNotEqual(first, second)

    def test_proxy_path_keeps_alpha_friendly_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay.png"
            path.write_bytes(b"fake")
            proxy = proxy_path_for_image(path, cache_root=tmp)
        self.assertEqual(proxy.suffix, ".png")


if __name__ == "__main__":
    unittest.main()
