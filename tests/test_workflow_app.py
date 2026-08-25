import tkinter as tk
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from go2_safe_control.app import SafeControlApp
from go2_safe_control.person_detection import PERSON_BOX_GREEN, PERSON_BOX_RED
from go2_safe_control.safety import Velocity
from go2_safe_control.session import VideoFrameData
from go2_safe_control.workflow import WorkflowStep


class FakeSession:
    def __init__(self) -> None:
        self.connected = True
        self.walk_ready = False
        self.velocities: list[Velocity] = []
        self.prepare_calls = 0
        self.stop_calls = 0
        self.stand_up_calls = 0
        self.stand_down_calls = 0
        self.sport_action_calls: list[str] = []

    def update_velocity(self, velocity: Velocity) -> None:
        self.velocities.append(velocity)

    def prepare_walk_mode(self) -> None:
        self.prepare_calls += 1
        self.walk_ready = False

    def emergency_stop(self) -> None:
        self.stop_calls += 1
        self.walk_ready = False

    def stand_up(self) -> None:
        self.stand_up_calls += 1
        self.walk_ready = False

    def stand_down(self) -> None:
        self.stand_down_calls += 1
        self.walk_ready = False

    def sport_action(self, action: str) -> None:
        self.sport_action_calls.append(action)
        self.walk_ready = False

    def connect(self, _settings: object) -> None:
        pass

    def disconnect(self) -> None:
        self.connected = False

    def start_video(self) -> None:
        pass

    def stop_video(self) -> None:
        pass

    def shutdown(self, timeout: float = 3.0) -> None:
        del timeout


class WorkflowAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.session = FakeSession()
        with patch("go2_safe_control.app.RobotSession", return_value=self.session):
            self.app = SafeControlApp(self.root)
        self.log_directory = tempfile.TemporaryDirectory()
        self.app._error_log_path = Path(self.log_directory.name) / "error.log"
        self.app.input_guard.activate = Mock()
        self.app.input_guard.deactivate = Mock()
        self.app.hardware_ok_var.set(True)

    def tearDown(self) -> None:
        self.root.destroy()
        self.log_directory.cleanup()

    def test_ui_disables_motion_until_connected_confirmed_and_armed(self) -> None:
        self.app.policy.disarm()
        self.app._last_control_state_signature = None
        self.app._sync_control_states()
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app._motion_buttons.values()))

        self.app.policy.arm(hardware_confirmed=True)
        self.app._last_control_state_signature = None
        self.app._sync_control_states()
        self.assertTrue(all(button.instate(["!disabled"]) for button in self.app._motion_buttons.values()))

        self.session.connected = False
        self.app._last_control_state_signature = None
        self.app._sync_control_states()
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app._motion_buttons.values()))

    def test_connected_event_collapses_connection_details(self) -> None:
        self.app._set_connection_details_visible(True)

        self.app._handle_session_event("connected", "已连接：STA / 192.168.1.124")

        self.assertFalse(self.app._connection_details_visible)
        self.assertEqual(self.app.connection_toggle_button.cget("text"), "展开连接设置")

    def test_person_detection_ready_clears_loading_status(self) -> None:
        self.app._camera_requested = True
        self.app._person_detection_enabled = True
        self.app._refresh_person_detection()

        self.app._handle_person_detection_event("ready", "人员识别模型已就绪")

        self.assertIn("人员识别已就绪", self.app.camera_var.get())
        self.assertIn("红色框", self.app.camera_var.get())
        self.assertNotIn("正在", self.app.camera_var.get())

    def test_refresh_shortcut_defaults_to_f5_and_is_visible(self) -> None:
        self.assertEqual(self.app.person_refresh_shortcut_var.get(), "F5")
        self.assertEqual(
            tuple(self.app.person_refresh_shortcut_box.cget("values")),
            ("F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"),
        )
        self.assertIn("F5", self.app.person_refresh_button.cget("text"))

    def test_refresh_shortcut_toggles_box_color_once_per_key_press(self) -> None:
        self.app._camera_requested = True
        self.app._person_detection.refresh = Mock()
        event = SimpleNamespace(keysym="F5", keycode=116)

        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_RED)
        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_RED)
        self.app._person_detection.refresh.assert_called_once_with()

        self.assertEqual(self.app._on_key_release(event), "break")
        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_GREEN)
        self.assertEqual(self.app._person_detection.refresh.call_count, 2)

    def test_changing_refresh_shortcut_updates_button_and_key(self) -> None:
        self.app._camera_requested = True
        self.app._person_detection.refresh = Mock()
        self.app.person_refresh_shortcut_var.set("F8")
        self.app._on_refresh_shortcut_changed()

        self.assertIn("F8", self.app.person_refresh_button.cget("text"))
        self.assertIsNone(
            self.app._on_key_press(SimpleNamespace(keysym="F5", keycode=116))
        )
        self.assertEqual(
            self.app._on_key_press(SimpleNamespace(keysym="F8", keycode=119)),
            "break",
        )
        self.app._person_detection.refresh.assert_called_once_with()

    def test_refresh_shortcut_cannot_bypass_camera_requirement(self) -> None:
        self.app._camera_requested = False
        self.app._person_detection.refresh = Mock()

        result = self.app._on_key_press(SimpleNamespace(keysym="F5", keycode=116))

        self.assertEqual(result, "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_GREEN)
        self.app._person_detection.refresh.assert_not_called()
        self.assertIn("请先连接并开启摄像头", self.app.camera_var.get())

    def test_wait_only_workflow_completes_and_stops(self) -> None:
        self.app.workflow_steps = [WorkflowStep("wait", 0.0, 0.1)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.assertTrue(self.app.workflow_runner.running)
        self.assertEqual(
            self.app._workflow_velocity(now=10.0, focused=True),
            Velocity.zero(),
        )
        self.app._workflow_velocity(now=10.2, focused=True)

        self.assertFalse(self.app.workflow_runner.running)
        self.assertTrue(self.app.workflow_runner.finished)
        self.assertIn("已完成", self.app.workflow_status_var.get())
        self.assertGreaterEqual(self.session.stop_calls, 2)

    def test_movement_waits_for_walk_ready_then_uses_step_speed(self) -> None:
        self.app.workflow_steps = [WorkflowStep("forward", 0.35, 1.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.assertEqual(
            self.app._workflow_velocity(now=20.0, focused=True),
            Velocity.zero(),
        )
        self.assertEqual(self.session.prepare_calls, 1)
        self.session.walk_ready = True
        self.app._handle_session_event("walk_ready", "mcf 行走模式已就绪")

        velocity = self.app._workflow_velocity(now=20.1, focused=True)

        self.assertEqual(velocity, Velocity(0.35, 0.0, 0.0))
        self.assertTrue(self.app.policy.armed)

    def test_emergency_stop_cancels_active_workflow(self) -> None:
        self.app.workflow_steps = [WorkflowStep("wait", 0.0, 5.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.app._emergency_stop()

        self.assertFalse(self.app.workflow_runner.running)
        self.assertFalse(self.app.policy.armed)
        self.assertIn("急停", self.app.workflow_status_var.get())

    def test_direction_change_keeps_zero_velocity_for_interstep_delay(self) -> None:
        self.app.workflow_steps = [
            WorkflowStep("forward", 0.30, 1.0),
            WorkflowStep("backward", 0.25, 1.0),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.app._workflow_velocity(now=30.0, focused=True)
        self.session.walk_ready = True
        self.app._handle_session_event("walk_ready", "mcf 行走模式已就绪")
        self.assertEqual(
            self.app._workflow_velocity(now=30.1, focused=True),
            Velocity(0.30, 0.0, 0.0),
        )

        self.assertEqual(
            self.app._workflow_velocity(now=31.1, focused=True),
            Velocity.zero(),
        )
        self.assertEqual(
            self.app._workflow_velocity(now=31.2, focused=True),
            Velocity.zero(),
        )
        self.assertTrue(self.app.workflow_runner.awaiting_begin)
        self.assertEqual(
            self.app._workflow_velocity(now=31.31, focused=True),
            Velocity(-0.25, 0.0, 0.0),
        )

    def test_posture_motion_and_lie_down_sequence_runs_in_order(self) -> None:
        self.app.workflow_steps = [
            WorkflowStep("stand_up", 0.0, 1.0),
            WorkflowStep("wait", 0.0, 0.1),
            WorkflowStep("forward", 0.30, 0.1),
            WorkflowStep("stop", 0.0, 0.0),
            WorkflowStep("stand_down", 0.0, 1.0),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.app._workflow_velocity(now=40.0, focused=True)
        self.assertEqual(self.session.stand_up_calls, 1)
        self.app._workflow_velocity(now=41.0, focused=True)
        self.app._workflow_velocity(now=41.21, focused=True)
        self.app._workflow_velocity(now=41.31, focused=True)
        self.app._workflow_velocity(now=41.52, focused=True)
        self.assertEqual(self.session.prepare_calls, 1)
        self.session.walk_ready = True
        self.app._handle_session_event("walk_ready", "mcf 行走模式已就绪")
        self.assertEqual(
            self.app._workflow_velocity(now=41.60, focused=True),
            Velocity(0.30, 0.0, 0.0),
        )
        self.app._workflow_velocity(now=41.70, focused=True)
        self.app._workflow_velocity(now=41.91, focused=True)
        self.app._workflow_velocity(now=42.12, focused=True)
        self.assertEqual(self.session.stand_down_calls, 1)
        self.app._workflow_velocity(now=43.12, focused=True)

        self.assertFalse(self.app.workflow_runner.running)
        self.assertIn("已完成", self.app.workflow_status_var.get())

    def test_additional_sport_action_runs_from_workflow(self) -> None:
        self.app.workflow_steps = [WorkflowStep("hello", 0.0, 1.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.app._workflow_velocity(now=50.0, focused=True)

        self.assertEqual(self.session.sport_action_calls, ["hello"])
        self.assertFalse(self.app.policy.armed)

    def test_stand_up_then_heart_advances_to_following_motion(self) -> None:
        self.app.workflow_steps = [
            WorkflowStep("stand_up", 0.0, 1.0),
            WorkflowStep("heart", 0.0, 1.0),
            WorkflowStep("forward", 0.30, 1.0),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.app._workflow_velocity(now=60.0, focused=True)
        self.app._workflow_velocity(now=61.0, focused=True)
        self.app._workflow_velocity(now=61.21, focused=True)
        self.assertEqual(self.session.sport_action_calls, ["heart"])
        self.app._workflow_velocity(now=62.21, focused=True)
        self.app._workflow_velocity(now=62.42, focused=True)

        self.assertEqual(self.session.prepare_calls, 1)

    def test_heart_confirmation_timeout_is_logged_but_workflow_continues(self) -> None:
        self.app.workflow_steps = [
            WorkflowStep("heart", 0.0, 1.0),
            WorkflowStep("wait", 0.0, 1.0),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        self.app._workflow_velocity(now=70.0, focused=True)
        self.app._handle_session_event(
            "action_warning",
            "Heart 已发送但在 5.0 秒内未返回确认",
        )

        self.assertTrue(self.app.workflow_runner.running)
        self.assertIn("Heart", self.app.error_log_text.get("1.0", "end"))
        self.app._workflow_velocity(now=71.0, focused=True)
        self.app._workflow_velocity(now=71.21, focused=True)
        self.assertEqual(self.app.workflow_runner.current_step.action, "wait")

    def test_video_render_error_does_not_kill_workflow_heartbeat(self) -> None:
        self.app.workflow_steps = [WorkflowStep("heart", 0.0, 1.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        with (
            patch.object(
                self.app,
                "_render_latest_video_frame",
                side_effect=RuntimeError("bad camera frame"),
            ),
            patch.object(self.root, "after") as schedule,
        ):
            self.app._tick()

        self.assertEqual(self.session.sport_action_calls, ["heart"])
        schedule.assert_called_once_with(50, self.app._tick)

    def test_person_detection_only_receives_frames_after_it_is_enabled(self) -> None:
        frame = VideoFrameData(2, 1, b"\x00" * 6)
        self.app._person_detection.submit = Mock()

        self.app._queue_video_frame(frame)
        self.app._person_detection.submit.assert_not_called()

        self.app._start_person_detection()
        self.app._queue_video_frame(frame)

        self.assertTrue(self.app._person_detection_enabled)
        self.app._person_detection.submit.assert_called_once_with(frame)

    def test_stopping_person_detection_clears_boxes_without_stopping_camera(self) -> None:
        self.app._person_detection.clear = Mock()
        self.app._start_person_detection()

        self.app._stop_person_detection()

        self.assertFalse(self.app._person_detection_enabled)
        self.app._person_detection.clear.assert_called_once_with()
        self.assertEqual(self.session.stop_calls, 0)


    def test_action_error_keeps_connection_disarms_and_writes_log_without_popup(self) -> None:
        self.app.connection_var.set("已连接：STA / 192.168.1.124")
        self.app.policy.arm(hardware_confirmed=True)

        with (
            patch("go2_safe_control.app.messagebox.showwarning") as warning,
            patch("go2_safe_control.app.messagebox.showerror") as error,
        ):
            self.app._handle_session_event(
                "action_error",
                "Sit 被机器人拒绝（错误码 3203）",
            )

        self.assertEqual(
            self.app.connection_var.get(),
            "已连接：STA / 192.168.1.124",
        )
        self.assertFalse(self.app.policy.armed)
        self.assertIn("Go2 动作未执行", self.app.error_log_text.get("1.0", "end"))
        self.assertIn("错误码 3203", self.app.error_log_text.get("1.0", "end"))
        self.assertIn("错误码 3203", self.app._error_log_path.read_text(encoding="utf-8"))
        warning.assert_not_called()
        error.assert_not_called()

    def test_invalid_connect_input_writes_log_without_popup(self) -> None:
        self.app.mode_var.set("STA")
        self.app.ip_var.set("not-an-ip")

        with (
            patch("go2_safe_control.app.messagebox.showwarning") as warning,
            patch("go2_safe_control.app.messagebox.showerror") as error,
        ):
            self.app._connect()

        self.assertIn("IP 错误", self.app.error_log_text.get("1.0", "end"))
        warning.assert_not_called()
        error.assert_not_called()

    def test_ap_diagnostic_is_logged_without_becoming_recent_error(self) -> None:
        self.app.mode_var.set("AP")
        previous_summary = self.app.error_summary_var.get()

        with (
            patch("go2_safe_control.app.messagebox.showwarning") as warning,
            patch("go2_safe_control.app.messagebox.showerror") as error,
        ):
            self.app._handle_session_event(
                "diagnostic",
                "[AP] WebRTC 连接成功；等待准备行走模式",
            )

        displayed = self.app.error_log_text.get("1.0", "end")
        saved = self.app._error_log_path.read_text(encoding="utf-8")
        self.assertIn("AP 诊断", displayed)
        self.assertIn("WebRTC 连接成功", saved)
        self.assertEqual(self.app.error_summary_var.get(), previous_summary)
        warning.assert_not_called()
        error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
