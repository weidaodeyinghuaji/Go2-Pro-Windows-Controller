import json
import unittest

from go2_safe_control.safety import Velocity
from go2_safe_control.workflow import (
    ACTION_LABELS,
    WorkflowRunner,
    WorkflowStep,
    default_workflow,
    parse_workflow,
    velocity_for_step,
    workflow_from_json,
    workflow_to_json,
)


class WorkflowTests(unittest.TestCase):
    def test_recovery_stand_label_explains_that_it_is_for_a_fallen_robot(self) -> None:
        self.assertEqual(ACTION_LABELS["recovery_stand"], "跌倒恢复")

    def test_default_workflow_matches_the_requested_demo(self) -> None:
        steps = default_workflow()

        self.assertEqual(
            [step.action for step in steps],
            [
                "stand_up",
                "wait",
                "forward",
                "turn_right",
                "forward",
                "stop",
                "stand_down",
            ],
        )
        self.assertEqual(steps[2], WorkflowStep("forward", 0.30, 3.0))
        self.assertEqual(steps[3], WorkflowStep("turn_right", 0.50, 2.0))

    def test_parser_validates_action_speed_duration_and_total_time(self) -> None:
        parsed = parse_workflow(
            [
                {"action": "前进", "speed": "0.30", "duration": "2.5"},
                {"action": "停止", "speed": "", "duration": ""},
            ]
        )
        self.assertEqual(parsed[0], WorkflowStep("forward", 0.30, 2.5))
        self.assertEqual(parsed[1], WorkflowStep("stop", 0.0, 0.0))

        with self.assertRaisesRegex(ValueError, "前进速度"):
            parse_workflow([{"action": "前进", "speed": "0.81", "duration": "1"}])
        with self.assertRaisesRegex(ValueError, "持续时间"):
            parse_workflow([{"action": "右转", "speed": "0.50", "duration": "0"}])
        with self.assertRaisesRegex(ValueError, "总时长"):
            parse_workflow(
                [
                    {"action": "等待", "speed": "", "duration": "30"}
                    for _ in range(5)
                ]
            )

    def test_velocity_mapping_uses_each_step_speed(self) -> None:
        self.assertEqual(
            velocity_for_step(WorkflowStep("forward", 0.35, 2.0)),
            Velocity(0.35, 0.0, 0.0),
        )
        self.assertEqual(
            velocity_for_step(WorkflowStep("turn_right", 0.60, 1.0)),
            Velocity(0.0, 0.0, -0.60),
        )
        self.assertEqual(
            velocity_for_step(WorkflowStep("wait", 0.0, 2.0)),
            Velocity.zero(),
        )

    def test_runner_advances_only_after_current_step_is_begun(self) -> None:
        runner = WorkflowRunner()
        runner.start(
            [
                WorkflowStep("forward", 0.30, 2.0),
                WorkflowStep("stop", 0.0, 0.0),
            ]
        )

        self.assertTrue(runner.awaiting_begin)
        self.assertFalse(runner.tick(now=100.0))
        runner.begin_current(now=100.0)
        self.assertFalse(runner.tick(now=101.9))
        self.assertTrue(runner.tick(now=102.0))
        self.assertEqual(runner.current_step.action, "stop")
        self.assertTrue(runner.awaiting_begin)
        runner.begin_current(now=102.0)
        self.assertTrue(runner.tick(now=102.0))
        self.assertFalse(runner.running)
        self.assertTrue(runner.finished)

    def test_cancel_stops_runner_immediately(self) -> None:
        runner = WorkflowRunner()
        runner.start([WorkflowStep("wait", 0.0, 5.0)])
        runner.begin_current(now=10.0)

        runner.cancel()

        self.assertFalse(runner.running)
        self.assertFalse(runner.finished)
        self.assertIsNone(runner.current_step)

    def test_workflow_json_round_trip_contains_no_runtime_secrets(self) -> None:
        steps = default_workflow()
        encoded = workflow_to_json(steps)

        self.assertEqual(workflow_from_json(encoded), steps)
        payload = json.loads(encoded)
        self.assertEqual(payload[0]["action"], "stand_up")
        self.assertEqual(set(payload[0]), {"action", "speed", "duration"})
        self.assertEqual(ACTION_LABELS["stand_down"], "安全卧趴")

    def test_additional_sport_actions_are_valid_timed_steps(self) -> None:
        actions = ["平衡站立", "跌倒恢复", "坐下", "坐起", "打招呼", "伸展", "比心"]
        steps = parse_workflow(
            {"action": action, "speed": "", "duration": "3"} for action in actions
        )
        self.assertEqual([step.label for step in steps], actions)
        self.assertTrue(all(step.speed == 0.0 and step.duration == 3.0 for step in steps))

    def test_old_recovery_stand_label_remains_readable(self) -> None:
        steps = parse_workflow(
            [{"action": "恢复站立", "speed": "", "duration": "3"}]
        )
        self.assertEqual(steps[0].action, "recovery_stand")


if __name__ == "__main__":
    unittest.main()
