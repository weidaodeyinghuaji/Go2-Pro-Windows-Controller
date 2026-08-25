import unittest

from go2_safe_control.app import (
    ClickMotionLatch,
    OneClickActivation,
    control_key_from_event,
    normalize_aes_key,
    parse_control_limits,
)


class AppInputTests(unittest.TestCase):
    def test_windows_physical_w_key_works_when_ime_changes_keysym(self) -> None:
        self.assertEqual(control_key_from_event("ProcessKey", 87), "w")

    def test_space_is_resolved_from_physical_keycode(self) -> None:
        self.assertEqual(control_key_from_event("ProcessKey", 32), "space")

    def test_unrelated_key_is_ignored(self) -> None:
        self.assertIsNone(control_key_from_event("F1", 112))

    def test_aes_key_accepts_plain_32_hex_characters(self) -> None:
        self.assertEqual(normalize_aes_key("A" * 32), "a" * 32)

    def test_aes_key_cleans_label_spaces_and_uuid_hyphens(self) -> None:
        self.assertEqual(
            normalize_aes_key("AES-128 Key : 01234567-89ab-cdef-0123-456789abcdef\n"),
            "0123456789abcdef0123456789abcdef",
        )

    def test_aes_key_cleans_zero_width_marks_and_wrapping_quotes(self) -> None:
        self.assertEqual(
            normalize_aes_key("\ufeff‘0123456789abcdef0123456789abcdef’\u200b"),
            "0123456789abcdef0123456789abcdef",
        )

    def test_aes_key_rejects_password_or_wrong_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "整理后为 11 个字符"):
            normalize_aes_key("password123")

    def test_33_hex_characters_are_not_silently_truncated(self) -> None:
        with self.assertRaisesRegex(ValueError, "奇数位无法表示 16 字节"):
            normalize_aes_key("a" * 33)

    def test_one_click_activation_arms_only_after_walk_ready(self) -> None:
        flow = OneClickActivation()

        flow.begin()

        self.assertTrue(flow.pending)
        self.assertTrue(flow.consume_walk_ready())
        self.assertFalse(flow.pending)
        self.assertFalse(flow.consume_walk_ready())

    def test_cancelled_one_click_activation_cannot_arm_later(self) -> None:
        flow = OneClickActivation()
        flow.begin()

        flow.cancel()

        self.assertFalse(flow.consume_walk_ready())

    def test_click_motion_stays_active_until_explicit_stop(self) -> None:
        motion = ClickMotionLatch()

        motion.start("w")

        self.assertEqual(motion.active_keys(), {"w"})
        self.assertEqual(motion.active_keys(), {"w"})
        motion.stop()
        self.assertEqual(motion.active_keys(), set())

    def test_clicking_another_direction_replaces_previous_motion(self) -> None:
        motion = ClickMotionLatch()
        motion.start("w")

        motion.start("q")

        self.assertEqual(motion.active_keys(), {"q"})

    def test_custom_speed_values_are_parsed(self) -> None:
        limits = parse_control_limits("0.45", "0.25", "0.70")

        self.assertEqual(limits.linear, 0.45)
        self.assertEqual(limits.lateral, 0.25)
        self.assertEqual(limits.yaw, 0.70)

    def test_custom_speed_rejects_non_finite_and_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "前后速度"):
            parse_control_limits("nan", "0.20", "0.50")
        with self.assertRaisesRegex(ValueError, "前后速度"):
            parse_control_limits("0.81", "0.20", "0.50")
        with self.assertRaisesRegex(ValueError, "横移速度"):
            parse_control_limits("0.30", "0", "0.50")
        with self.assertRaisesRegex(ValueError, "转向速度"):
            parse_control_limits("0.30", "0.20", "1.01")


if __name__ == "__main__":
    unittest.main()
