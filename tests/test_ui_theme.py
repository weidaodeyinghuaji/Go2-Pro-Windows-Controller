import unittest

from go2_safe_control.ui_theme import COLORS, ease_out_cubic


class UiThemeTests(unittest.TestCase):
    def test_palette_is_restrained_and_keeps_safety_colors_semantic(self) -> None:
        self.assertEqual(COLORS.background, "#F3F5F7")
        self.assertEqual(COLORS.surface, "#FFFFFF")
        self.assertEqual(COLORS.primary, "#2563EB")
        self.assertEqual(COLORS.danger, "#C62828")
        self.assertNotEqual(COLORS.primary, COLORS.danger)

    def test_ease_out_curve_is_monotonic_and_finishes_exactly(self) -> None:
        samples = [ease_out_cubic(index / 10) for index in range(11)]

        self.assertEqual(samples[0], 0.0)
        self.assertEqual(samples[-1], 1.0)
        self.assertEqual(samples, sorted(samples))
        self.assertGreater(samples[5], 0.5)

    def test_ease_out_curve_clamps_out_of_range_input(self) -> None:
        self.assertEqual(ease_out_cubic(-1.0), 0.0)
        self.assertEqual(ease_out_cubic(2.0), 1.0)


if __name__ == "__main__":
    unittest.main()
