import unittest

from go2_safe_control.safety import (
    ControlLimits,
    DeadmanKeys,
    SafetyPolicy,
    Velocity,
    watchdog_velocity,
)


class SafetyPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = SafetyPolicy(ControlLimits(linear=0.15, lateral=0.10, yaw=0.30))

    def test_disarmed_controller_never_requests_motion(self) -> None:
        self.assertEqual(self.policy.velocity_for({"w"}, focused=True), Velocity.zero())

    def test_default_limits_use_normal_low_speed_values(self) -> None:
        self.assertEqual(
            ControlLimits(),
            ControlLimits(linear=0.30, lateral=0.20, yaw=0.50),
        )

    def test_arm_requires_explicit_hardware_confirmation(self) -> None:
        self.assertFalse(self.policy.arm(hardware_confirmed=False))
        self.assertFalse(self.policy.armed)
        self.assertTrue(self.policy.arm(hardware_confirmed=True))
        self.assertTrue(self.policy.armed)

    def test_armed_controller_maps_keys_to_limited_velocity(self) -> None:
        self.policy.arm(hardware_confirmed=True)
        self.assertEqual(
            self.policy.velocity_for({"w", "a", "q"}, focused=True),
            Velocity(forward=0.15, lateral=0.10, yaw=0.30),
        )

    def test_watchdog_turns_stale_motion_into_stop(self) -> None:
        moving = Velocity(forward=0.15, lateral=0.0, yaw=0.0)
        self.assertEqual(
            watchdog_velocity(moving, last_update=10.0, now=10.36, timeout=0.35),
            Velocity.zero(),
        )

    def test_focus_loss_and_opposing_keys_are_safe(self) -> None:
        self.policy.arm(hardware_confirmed=True)
        self.assertEqual(
            self.policy.velocity_for({"w"}, focused=False),
            Velocity.zero(),
        )

    def test_keyboard_deadman_expires_if_release_event_is_missed(self) -> None:
        keys = DeadmanKeys(keyboard_timeout=0.45)
        keys.press("w", now=10.0, requires_repeat=True)
        self.assertEqual(keys.active(now=10.44), {"w"})
        self.assertEqual(keys.active(now=10.46), set())
        self.assertEqual(
            self.policy.velocity_for({"w", "s", "a", "d", "q", "e"}, focused=True),
            Velocity.zero(),
        )


if __name__ == "__main__":
    unittest.main()
