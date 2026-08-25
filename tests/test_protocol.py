import unittest

from go2_safe_control.protocol import (
    motion_mode_options,
    move_options,
    normal_motion_mode_options,
    parse_motion_mode,
    require_sport_action_success,
    sport_action_options,
    stand_down_options,
    stand_up_options,
    stop_options,
)
from go2_safe_control.safety import Velocity


class ProtocolTests(unittest.TestCase):
    def test_move_request_uses_go2_sport_parameter_names(self) -> None:
        self.assertEqual(
            move_options(Velocity(0.15, -0.10, 0.30)),
            {
                "api_id": 1008,
                "parameter": {"x": 0.15, "y": -0.10, "z": 0.30},
            },
        )

    def test_stop_request_uses_stop_move_api(self) -> None:
        self.assertEqual(stop_options(), {"api_id": 1003})

    def test_stand_down_request_uses_official_sport_api(self) -> None:
        self.assertEqual(stand_down_options(), {"api_id": 1005})

    def test_stand_up_request_uses_official_sport_api(self) -> None:
        self.assertEqual(stand_up_options(), {"api_id": 1004})

    def test_additional_sport_actions_use_official_api_ids(self) -> None:
        self.assertEqual(
            {action: sport_action_options(action)["api_id"] for action in (
                "balance_stand",
                "recovery_stand",
                "sit",
                "rise_sit",
                "hello",
                "stretch",
                "heart",
            )},
            {
                "balance_stand": 1002,
                "recovery_stand": 1006,
                "sit": 1009,
                "rise_sit": 1010,
                "hello": 1016,
                "stretch": 1017,
                "heart": 1036,
            },
        )

    def test_motion_mode_requests_use_switcher_api(self) -> None:
        self.assertEqual(motion_mode_options(), {"api_id": 1001})
        self.assertEqual(
            normal_motion_mode_options(),
            {"api_id": 1002, "parameter": {"name": "normal"}},
        )

    def test_motion_mode_response_is_decoded(self) -> None:
        response = {
            "data": {
                "header": {"status": {"code": 0}},
                "data": '{"name":"normal"}',
            }
        }
        self.assertEqual(parse_motion_mode(response), "normal")

    def test_failed_motion_mode_response_is_rejected(self) -> None:
        response = {
            "data": {
                "header": {"status": {"code": 3001}},
                "data": "{}",
            }
        }
        with self.assertRaisesRegex(RuntimeError, "3001"):
            parse_motion_mode(response)

    def test_failed_sport_action_response_reports_robot_error_code(self) -> None:
        response = {
            "data": {
                "header": {"status": {"code": 3203}},
                "data": "unknown api",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "3203.*固件或当前运动模式"):
            require_sport_action_success(response, "Sit")


if __name__ == "__main__":
    unittest.main()
