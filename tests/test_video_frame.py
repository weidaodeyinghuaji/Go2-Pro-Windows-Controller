import unittest
import inspect

from go2_safe_control.app import (
    CAMERA_PANEL_HEIGHT,
    CAMERA_PREVIEW_MAX_HEIGHT,
    camera_subsample_factor,
    SafeControlApp,
)
from go2_safe_control.session import VideoFrameData


class VideoFrameDataTests(unittest.TestCase):
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
        # 通信层固定输出 480×270；它必须原尺寸显示，不能因宽度差 10px 被缩半。
        self.assertEqual(camera_subsample_factor(480, 270), 1)
        self.assertEqual(camera_subsample_factor(640, 480), 2)
        self.assertEqual(camera_subsample_factor(1280, 720), 3)

    def test_camera_panel_has_person_detection_refresh_control(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        refresh_source = inspect.getsource(SafeControlApp._refresh_person_detection)

        self.assertIn('text="刷新识别（F5）"', ui_source)
        self.assertIn("_refresh_person_detection", ui_source)
        self.assertIn("PERSON_BOX_RED", refresh_source)
        self.assertIn("PERSON_BOX_GREEN", refresh_source)
        self.assertIn("person_refresh_shortcut_var", ui_source)

    def test_camera_panel_uses_buttons_instead_of_person_status_text(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)

        self.assertIn('text="开启人员识别"', ui_source)
        self.assertIn('text="关闭人员识别"', ui_source)
        self.assertNotIn("textvariable=self.person_detection_var", ui_source)


if __name__ == "__main__":
    unittest.main()
