import inspect
import unittest
from pathlib import Path

from PIL import Image

from go2_safe_control import app
from go2_safe_control.splash import (
    SPLASH_BRAND,
    SPLASH_DURATION_MS,
    SPLASH_FEATURES,
    SPLASH_STATUS,
    SPLASH_SUBTITLE,
    splash_image_path,
    splash_window_geometry,
)


class SplashScreenTests(unittest.TestCase):
    def test_splash_image_is_packaged_and_readable(self) -> None:
        image_path = splash_image_path()

        self.assertTrue(image_path.is_file())
        with Image.open(image_path) as image:
            self.assertGreaterEqual(image.width, 1600)
            self.assertGreaterEqual(image.height, 900)

    def test_startup_uses_automatic_splash_transition(self) -> None:
        main_source = inspect.getsource(app.main)

        self.assertIn("show_splash", main_source)
        self.assertIn("SafeControlApp", main_source)
        self.assertGreaterEqual(SPLASH_DURATION_MS, 1200)
        self.assertLessEqual(SPLASH_DURATION_MS, 3000)

    def test_splash_is_a_small_centered_window(self) -> None:
        self.assertEqual(
            splash_window_geometry(1920, 1080, 1672, 941),
            (760, 428, 580, 326),
        )
        self.assertEqual(
            splash_window_geometry(1366, 768, 1672, 941),
            (760, 428, 303, 170),
        )

        from go2_safe_control.splash import SplashScreen

        source = inspect.getsource(SplashScreen)
        self.assertIn("splash_window_geometry", source)
        self.assertNotIn('f"{screen_width}x{screen_height}+0+0"', source)

    def test_splash_has_no_interactive_controls(self) -> None:
        from go2_safe_control.splash import SplashScreen

        source = inspect.getsource(SplashScreen)
        self.assertNotIn("Button(", source)
        self.assertNotIn("Entry(", source)
        self.assertNotIn("Combobox(", source)

    def test_splash_uses_project_brand_without_third_party_branding(self) -> None:
        self.assertEqual(SPLASH_BRAND, "AI故障预警")
        visible_copy = " ".join(
            (SPLASH_BRAND, SPLASH_SUBTITLE, SPLASH_FEATURES, SPLASH_STATUS)
        )
        self.assertNotIn("宇树", visible_copy)
        self.assertNotIn("Unitree", visible_copy)
        self.assertNotIn("Go2", visible_copy)

    def test_pyinstaller_spec_includes_splash_asset(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        spec_text = (project_root / "packaging" / "go2_controller.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn('"assets" / "splash.png"', spec_text)
        self.assertIn('"assets"', spec_text)


if __name__ == "__main__":
    unittest.main()
