import tempfile
import time
import unittest
from pathlib import Path

from go2_safe_control.session import VideoFrameData
from go2_safe_control.video_recording import VideoRecorder, recording_profile


class VideoRecordingTests(unittest.TestCase):
    def test_common_video_extensions_have_stable_profiles(self) -> None:
        self.assertEqual(recording_profile(Path("demo.mp4")).codec, "mpeg4")
        self.assertEqual(recording_profile(Path("demo.avi")).codec, "mjpeg")
        self.assertEqual(recording_profile(Path("demo.mkv")).codec, "mpeg4")

        with self.assertRaisesRegex(ValueError, "MP4、AVI 或 MKV"):
            recording_profile(Path("demo.mov"))

    def test_records_rgb_frames_in_every_supported_format(self) -> None:
        import av

        with tempfile.TemporaryDirectory() as directory:
            frame = VideoFrameData(64, 48, bytes([32, 96, 160]) * (64 * 48))
            for extension in ("mp4", "avi", "mkv"):
                with self.subTest(extension=extension):
                    messages: list[tuple[str, str]] = []
                    output = Path(directory) / f"camera.{extension}"
                    recorder = VideoRecorder(
                        lambda kind, message: messages.append((kind, message))
                    )
                    recorder.start(output, fps=20)
                    for _ in range(4):
                        recorder.submit(frame)
                        time.sleep(0.055)

                    self.assertTrue(recorder.stop(timeout=5.0))
                    self.assertTrue(output.is_file())
                    self.assertGreater(output.stat().st_size, 0)
                    self.assertTrue(any(kind == "saved" for kind, _ in messages))

                    with av.open(str(output)) as container:
                        decoded = next(container.decode(video=0))
                        self.assertEqual((decoded.width, decoded.height), (64, 48))

    def test_start_rejects_a_second_active_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = VideoRecorder()
            recorder.start(Path(directory) / "first.mp4")
            with self.assertRaisesRegex(RuntimeError, "正在录制"):
                recorder.start(Path(directory) / "second.mp4")
            recorder.stop(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
