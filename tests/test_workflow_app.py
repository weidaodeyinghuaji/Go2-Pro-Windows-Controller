import tkinter as tk
import tempfile
import os
import unittest
import wave
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
        self.audio_files: list[Path] = []
        self.audio_stop_calls = 0
        self.audio_catalog_ready = True
        self.synced_audio: set[str] = set()
        self.play_synced_calls: list[str] = []
        self.sync_calls: list[object] = []
        self.light_calls: list[tuple[str, int, str]] = []

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

    def play_audio(self, path: Path) -> None:
        self.audio_files.append(path)

    def stop_audio(self) -> None:
        self.audio_stop_calls += 1

    def has_synced_audio(self, asset_id: str) -> bool:
        return asset_id in self.synced_audio

    def play_synced_audio(self, asset_id: str) -> None:
        self.play_synced_calls.append(asset_id)

    def sync_sounds(self, sounds: object) -> None:
        self.sync_calls.append(sounds)

    def set_light(self, action: str, seconds: int, request_id: str) -> None:
        self.light_calls.append((action, seconds, request_id))

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
        self.log_directory = tempfile.TemporaryDirectory()
        self.session = FakeSession()
        with patch.dict(os.environ, {"LOCALAPPDATA": self.log_directory.name}), patch(
            "go2_safe_control.app.RobotSession", return_value=self.session
        ):
            self.app = SafeControlApp(self.root)
        self.app._error_log_path = Path(self.log_directory.name) / "error.log"
        self.app.input_guard.activate = Mock()
        self.app.input_guard.deactivate = Mock()
        self.app.hardware_ok_var.set(True)

    def tearDown(self) -> None:
        self.root.destroy()
        self.log_directory.cleanup()

    def test_audio_buttons_play_stop_and_log_errors_without_popup(self) -> None:
        source = Path(self.log_directory.name) / "sound.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(bytes(4410 * 2))
        with patch("go2_safe_control.app.filedialog.askopenfilename", return_value=str(source)):
            self.app._choose_audio()
        self.assertEqual(self.app._audio_file.suffix, ".wav")
        self.session.synced_audio.add(self.app._selected_audio_id)
        self.app._play_audio()
        self.assertEqual(self.session.play_synced_calls, [self.app._selected_audio_id])
        self.assertTrue(self.app._audio_busy)
        self.app._stop_audio()
        self.assertEqual(self.session.audio_stop_calls, 1)
        with patch("go2_safe_control.app.messagebox.showerror") as popup:
            self.app._handle_session_event("audio_error", "机器人拒绝播放")
            popup.assert_not_called()
        self.assertFalse(self.app._audio_busy)
        self.assertIn("机器人拒绝播放", self.app._error_log_path.read_text(encoding="utf-8"))

    def test_sound_workflow_requires_prior_sync_and_never_uploads_while_running(self) -> None:
        sound = self.app.sound_library.list_sounds()[0]
        self.app.workflow_steps = [
            WorkflowStep("play_sound", 0.0, 1.0, sound.asset_id),
            WorkflowStep("wait", 0.0, 0.1),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.assertFalse(self.app.workflow_runner.running)
        self.assertIn("同步", self.app._error_log_path.read_text(encoding="utf-8"))
        self.session.synced_audio.add(sound.asset_id)
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.assertTrue(self.app.workflow_runner.running)
        self.app._workflow_velocity(now=10.0, focused=True)
        self.assertEqual(self.session.play_synced_calls, [sound.asset_id])
        self.assertEqual(self.session.sync_calls, [])
        self.assertTrue(self.app._workflow_audio_pending)
        self.app._workflow_velocity(now=100.0, focused=True)
        self.assertEqual(self.app.workflow_runner.current_index, 0)
        with patch("go2_safe_control.app.time.monotonic", return_value=10.2):
            self.app._handle_session_event("audio_started", sound.asset_id)
        self.assertFalse(self.app._workflow_audio_pending)
        self.app._workflow_velocity(now=11.3, focused=True)
        self.assertEqual(self.app.workflow_runner.current_step.action, "wait")
        self.assertGreaterEqual(self.session.audio_stop_calls, 1)

    def test_sound_workflow_play_error_cancels_without_popup(self) -> None:
        sound = self.app.sound_library.list_sounds()[0]
        self.session.synced_audio.add(sound.asset_id)
        self.app.workflow_steps = [WorkflowStep("play_sound", 0.0, 1.0, sound.asset_id)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.app._workflow_velocity(now=10.0, focused=True)
        with patch("go2_safe_control.app.messagebox.showerror") as popup:
            self.app._handle_session_event("audio_error", "播放被机器人拒绝")
            popup.assert_not_called()
        self.assertFalse(self.app.workflow_runner.running)
        self.assertIn("播放被机器人拒绝", self.app._error_log_path.read_text(encoding="utf-8"))

    def test_red_light_then_existing_local_sound_are_independent_steps(self) -> None:
        sound = self.app.sound_library.list_sounds()[0]
        self.session.synced_audio.add(sound.asset_id)
        self.app.workflow_steps = [
            WorkflowStep("light_red_flash", 0.0, 8.0),
            WorkflowStep("play_sound", 0.0, max(1.0, sound.duration + 0.3), sound.asset_id),
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.app._workflow_velocity(now=10.0, focused=True)
        self.assertEqual(len(self.session.light_calls), 1)
        self.assertEqual(self.session.light_calls[0][:2], ("light_red_flash", 8))
        self.assertEqual(self.session.play_synced_calls, [])
        request_id = self.session.light_calls[0][2]
        with patch("go2_safe_control.app.time.monotonic", return_value=10.1):
            self.app._handle_session_event("light_started", request_id)
        self.assertEqual(self.app.workflow_runner.current_step.action, "play_sound")
        self.app._workflow_velocity(now=10.4, focused=True)
        self.assertEqual(self.session.play_synced_calls, [sound.asset_id])

    def test_failed_light_cancels_workflow_without_starting_sound(self) -> None:
        self.app.workflow_steps = [
            WorkflowStep("light_red", 0.0, 5.0), WorkflowStep("wait", 0.0, 1.0)
        ]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.app._workflow_velocity(now=10.0, focused=True)
        request_id = self.session.light_calls[0][2]
        self.app._handle_session_event("light_error", request_id + "|机器人拒绝灯光指令")
        self.assertFalse(self.app.workflow_runner.running)
        self.assertIn("机器人拒绝灯光指令", self.app._error_log_path.read_text(encoding="utf-8"))

    def test_light_reply_timeout_stops_waiting_instead_of_hanging(self) -> None:
        self.app.workflow_steps = [WorkflowStep("light_green", 0.0, 3.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()
        self.app._workflow_velocity(now=10.0, focused=True)
        self.app._workflow_velocity(now=16.0, focused=True)
        self.assertFalse(self.app.workflow_runner.running)
        self.assertIn("灯光确认超时", self.app._error_log_path.read_text(encoding="utf-8"))

    def test_popdown_style_heartbeat_failure_logs_call_stack_and_current_step(self) -> None:
        self.app.workflow_steps = [WorkflowStep("wait", 0.0, 5.0)]
        with patch.object(self.app, "_ask_confirmation", return_value=True):
            self.app._start_workflow()

        def raise_popdown() -> None:
            raise KeyError("popdown")

        with patch.object(self.app, "_sync_control_states", side_effect=raise_popdown), patch.object(
            self.app.root, "after"
        ):
            self.app._tick()

        self.assertFalse(self.app.workflow_runner.running)
        logged = self.app._error_log_path.read_text(encoding="utf-8")
        self.assertIn("raise_popdown", logged)
        self.assertIn("第 1 步", logged)

    def test_sound_workflow_save_includes_portable_audio_asset(self) -> None:
        sound = self.app.sound_library.list_sounds()[0]
        self.app.workflow_steps = [WorkflowStep("play_sound", 0.0, 1.0, sound.asset_id)]
        target = Path(self.log_directory.name) / "my_flow.json"
        with patch("go2_safe_control.app.filedialog.asksaveasfilename", return_value=str(target)):
            self.app._save_workflow()
        self.assertTrue(target.is_file())
        self.assertTrue((target.with_name("my_flow_声音素材") / f"{sound.asset_id}.wav").is_file())
        self.app.workflow_steps = [WorkflowStep("wait", 0.0, 1.0)]
        with patch("go2_safe_control.app.filedialog.askopenfilename", return_value=str(target)):
            self.app._load_workflow()
        self.assertEqual(self.app.workflow_steps[0].asset_id, sound.asset_id)

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

    def test_refresh_shortcut_is_fixed_to_f5_without_a_selector(self) -> None:
        self.assertFalse(hasattr(self.app, "person_refresh_shortcut_var"))
        self.assertFalse(hasattr(self.app, "person_refresh_shortcut_box"))
        self.assertIn("F5", self.app.person_refresh_button.cget("text"))

    def test_refresh_shortcut_toggles_box_color_once_per_key_press(self) -> None:
        self.app._camera_requested = True
        self.app._person_detection.clear = Mock()
        event = SimpleNamespace(keysym="F5", keycode=116)

        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_RED)
        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_RED)
        self.app._person_detection.clear.assert_called_once_with()

        self.assertEqual(self.app._on_key_release(event), "break")
        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_GREEN)
        self.assertEqual(self.app._person_detection.clear.call_count, 2)

    def test_refresh_shortcut_cannot_bypass_camera_requirement(self) -> None:
        self.app._camera_requested = False
        self.app._person_detection.clear = Mock()

        result = self.app._on_key_press(SimpleNamespace(keysym="F5", keycode=116))

        self.assertEqual(result, "break")
        self.assertEqual(self.app._person_box_color, PERSON_BOX_GREEN)
        self.app._person_detection.clear.assert_not_called()
        self.assertIn("请先连接并开启摄像头", self.app.camera_var.get())

    def test_record_shortcut_starts_and_stops_once_per_key_press(self) -> None:
        self.app._camera_requested = True
        self.app._start_recording = Mock()
        self.app._stop_recording = Mock()
        self.app._video_recorder.is_recording = False
        event = SimpleNamespace(keysym="r", keycode=82, state=0x0004)

        self.assertEqual(self.app._on_key_press(event), "break")
        self.assertEqual(self.app._on_key_press(event), "break")
        self.app._start_recording.assert_called_once_with()

        self.assertEqual(self.app._on_key_release(event), "break")
        self.app._video_recorder.is_recording = True
        self.assertEqual(self.app._on_key_press(event), "break")
        self.app._stop_recording.assert_called_once_with()

    def test_record_shortcut_cannot_bypass_camera_requirement(self) -> None:
        self.app._camera_requested = False
        self.app._start_recording = Mock()
        event = SimpleNamespace(keysym="r", keycode=82, state=0x0004)

        self.assertEqual(self.app._on_key_press(event), "break")

        self.app._start_recording.assert_not_called()
        self.assertIn("请先连接并开启摄像头", self.app.recording_var.get())

    def test_canceling_record_dialog_releases_shortcut_latch(self) -> None:
        self.app._camera_requested = True
        self.app._record_shortcut_down = True

        with patch("go2_safe_control.app.filedialog.asksaveasfilename", return_value=""):
            self.app._start_recording()

        self.assertFalse(self.app._record_shortcut_down)
        self.assertIn("已取消录制", self.app.recording_var.get())

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
