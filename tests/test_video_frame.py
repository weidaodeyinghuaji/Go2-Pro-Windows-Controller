import unittest
import inspect

from go2_safe_control.app import (
    CAMERA_PANEL_HEIGHT,
    CAMERA_PREVIEW_MAX_HEIGHT,
    camera_subsample_factor,
    fit_camera_preview_size,
    SafeControlApp,
)
from go2_safe_control.session import VideoFrameData, video_processing_size


class VideoFrameDataTests(unittest.TestCase):
    def test_detection_frame_preserves_native_aspect_and_useful_detail(self) -> None:
        # 旧实现强制 480×270：4:3 输入会变形，480p 人腿又过小，站姿模型全拒绝。
        self.assertEqual(video_processing_size(640, 480), (640, 480))
        self.assertEqual(video_processing_size(1280, 720), (960, 540))
        self.assertEqual(video_processing_size(1920, 1080), (960, 540))
        self.assertEqual(video_processing_size(480, 270), (480, 270))

    def test_ppm_bytes_have_header_and_rgb_payload(self) -> None:
        frame = VideoFrameData(width=2, height=1, rgb=b"\x01\x02\x03\x04\x05\x06")

        self.assertEqual(frame.ppm_bytes(), b"P6\n2 1\n255\n\x01\x02\x03\x04\x05\x06")

    def test_invalid_rgb_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VideoFrameData(width=2, height=1, rgb=b"short")

    def test_camera_panel_reserves_space_and_scales_frames_to_fit(self) -> None:
        self.assertGreaterEqual(CAMERA_PANEL_HEIGHT, 380)
        self.assertGreaterEqual(CAMERA_PREVIEW_MAX_HEIGHT, 260)
        self.assertEqual(camera_subsample_factor(320, 240), 1)
        # 即使某个摄像头原生只有 480×270，预览也不能因为宽度差 10px 缩半。
        self.assertEqual(camera_subsample_factor(480, 270), 1)
        self.assertEqual(camera_subsample_factor(640, 480), 2)
        self.assertEqual(camera_subsample_factor(1280, 720), 3)
        self.assertEqual(fit_camera_preview_size(480, 270, 960, 540), (960, 540))
        self.assertEqual(fit_camera_preview_size(480, 270, 800, 600), (800, 450))

    def test_camera_panel_has_person_detection_refresh_control(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        refresh_source = inspect.getsource(SafeControlApp._refresh_person_detection)
        render_source = inspect.getsource(SafeControlApp._render_latest_video_frame)

        self.assertIn('text="刷新识别（F5）"', ui_source)
        self.assertIn("_refresh_person_detection", ui_source)
        self.assertIn("PERSON_BOX_RED", refresh_source)
        self.assertIn("PERSON_BOX_GREEN", refresh_source)
        self.assertIn("split_person_boxes_by_color", render_source)
        self.assertIn("PERSON_BOX_GREEN", render_source)
        self.assertIn("PERSON_BOX_RED", render_source)
        self.assertIn("self._person_detection.clear()", refresh_source)
        self.assertNotIn("self._person_detection.refresh()", refresh_source)
        self.assertNotIn("刷新快捷键", ui_source)
        self.assertNotIn("person_refresh_shortcut_box", ui_source)

    def test_requested_camera_status_text_is_removed(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)

        self.assertNotIn("实时显示 Go2 前置摄像头", ui_source)
        self.assertNotIn("textvariable=self.camera_var", ui_source)

    def test_camera_panel_has_recording_controls_and_full_width_preview(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        render_source = inspect.getsource(SafeControlApp._render_latest_video_frame)

        self.assertIn("开始录制", ui_source)
        self.assertIn("停止并保存", ui_source)
        self.assertIn('camera_preview.pack(fill="both", expand=True', ui_source)
        self.assertIn("fit_camera_preview_size", render_source)

    def test_camera_panel_uses_buttons_instead_of_person_status_text(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)

        self.assertIn('text="开启人员识别"', ui_source)
        self.assertIn('text="关闭人员识别"', ui_source)
        self.assertNotIn("textvariable=self.person_detection_var", ui_source)


if __name__ == "__main__":
    unittest.main()
