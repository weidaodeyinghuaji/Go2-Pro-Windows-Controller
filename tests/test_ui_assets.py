from __future__ import annotations

import unittest

from PIL import Image

from go2_safe_control.ui_assets import ASSET_ROOT, ICON_FILES, ICON_ROOT, ui_asset_path


class UiAssetTests(unittest.TestCase):
    def test_brand_images_and_all_declared_icons_are_readable(self) -> None:
        expected = {
            ui_asset_path("main_background.png"),
            ui_asset_path("header_art.png"),
            ui_asset_path("app_icon.png"),
            ASSET_ROOT / "app_icon.ico",
        }
        expected.update(ICON_ROOT / filename for filename in ICON_FILES.values())

        for path in expected:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)

        for path in expected - {ASSET_ROOT / "app_icon.ico"}:
            with self.subTest(image=path.name), Image.open(path) as image:
                image.verify()

    def test_icons_keep_transparency_for_clean_button_edges(self) -> None:
        for filename in ICON_FILES.values():
            with self.subTest(filename=filename), Image.open(ICON_ROOT / filename) as image:
                self.assertIn("A", image.getbands())


if __name__ == "__main__":
    unittest.main()
