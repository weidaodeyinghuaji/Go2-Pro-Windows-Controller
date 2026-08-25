import time
import unittest

import numpy as np

from go2_safe_control.person_detection import (
    DetectionSnapshot,
    PersonBox,
    PersonDetectionWorker,
    draw_person_boxes,
    person_boxes_from_yolox,
)
from go2_safe_control.session import VideoFrameData


class PersonDetectionPostprocessTests(unittest.TestCase):
    def test_only_person_class_is_returned_and_coordinates_are_clamped(self) -> None:
        # 这里直接测试已经解码的 YOLOX 结果：
        # 第 1 行是 person（类别 0），第 2 行是其他类别，必须被忽略。
        decoded = np.zeros((2, 85), dtype=np.float32)
        decoded[0, :4] = [40.0, 30.0, 100.0, 80.0]
        decoded[0, 4] = 0.9
        decoded[0, 5] = 0.8
        decoded[1, :4] = [20.0, 20.0, 10.0, 10.0]
        decoded[1, 4] = 0.99
        decoded[1, 6] = 0.99

        boxes = person_boxes_from_yolox(
            decoded,
            scale=0.5,
            frame_width=120,
            frame_height=90,
            confidence_threshold=0.45,
            already_decoded=True,
        )

        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0], PersonBox(0, 0, 120, 90, 0.72))

    def test_boxes_use_requested_green_or_red_color(self) -> None:
        frame = VideoFrameData(12, 12, b"\x00" * (12 * 12 * 3))
        box = (PersonBox(2, 2, 9, 9, 0.8),)

        green = draw_person_boxes(frame, box, "#00C853")
        red = draw_person_boxes(frame, box, "#D00000")

        offset = (2 * frame.width + 2) * 3
        self.assertEqual(green.rgb[offset : offset + 3], bytes((0, 200, 83)))
        self.assertEqual(red.rgb[offset : offset + 3], bytes((208, 0, 0)))

    def test_overlapping_person_boxes_are_suppressed(self) -> None:
        decoded = np.zeros((2, 85), dtype=np.float32)
        decoded[:, 4] = [0.95, 0.90]
        decoded[:, 5] = [0.90, 0.90]
        decoded[0, :4] = [50.0, 50.0, 40.0, 40.0]
        decoded[1, :4] = [52.0, 52.0, 40.0, 40.0]

        boxes = person_boxes_from_yolox(
            decoded,
            scale=1.0,
            frame_width=100,
            frame_height=100,
            already_decoded=True,
        )

        self.assertEqual(len(boxes), 1)


class _FakeDetector:
    def __init__(self, result: tuple[PersonBox, ...]) -> None:
        self.result = result
        self.frames: list[VideoFrameData] = []

    def detect(self, frame: VideoFrameData) -> tuple[PersonBox, ...]:
        self.frames.append(frame)
        return self.result


class PersonDetectionWorkerTests(unittest.TestCase):
    def test_worker_keeps_latest_result_and_refresh_reloads_detector(self) -> None:
        created: list[_FakeDetector] = []

        def factory(_model_path: object) -> _FakeDetector:
            detector = _FakeDetector((PersonBox(1, 2, 3, 4, 0.9),))
            created.append(detector)
            return detector

        events: list[tuple[str, str]] = []
        worker = PersonDetectionWorker(
            model_path="unused.onnx",
            detector_factory=factory,
            status_callback=lambda kind, message: events.append((kind, message)),
            min_interval=0.0,
        )
        frame = VideoFrameData(2, 1, b"\x00" * 6)
        try:
            worker.submit(frame)
            self.assertTrue(self._wait_until(lambda: worker.snapshot() is not None))
            snapshot = worker.snapshot()
            self.assertIsInstance(snapshot, DetectionSnapshot)
            self.assertEqual(len(snapshot.boxes), 1)

            worker.refresh()
            worker.submit(frame)
            self.assertTrue(self._wait_until(lambda: len(created) == 2))
            self.assertTrue(any(kind == "ready" for kind, _ in events))
        finally:
            worker.close()

    def test_stale_snapshot_is_not_returned(self) -> None:
        worker = PersonDetectionWorker(
            model_path="unused.onnx",
            detector_factory=lambda _path: _FakeDetector(tuple()),
            min_interval=0.0,
        )
        worker._snapshot = DetectionSnapshot(
            boxes=(PersonBox(1, 1, 2, 2, 0.8),),
            generated_at=time.monotonic() - 2.0,
        )

        self.assertIsNone(worker.snapshot(max_age=0.4))
        worker.close()

    def test_failed_model_is_not_reloaded_for_every_video_frame(self) -> None:
        attempts: list[int] = []

        def failing_factory(_model_path: object) -> _FakeDetector:
            attempts.append(1)
            raise RuntimeError("broken model")

        worker = PersonDetectionWorker(
            model_path="broken.onnx",
            detector_factory=failing_factory,
            min_interval=0.0,
        )
        frame = VideoFrameData(2, 1, b"\x00" * 6)
        try:
            worker.submit(frame)
            self.assertTrue(self._wait_until(lambda: len(attempts) == 1))
            for _ in range(5):
                worker.submit(frame)
                time.sleep(0.01)
            self.assertEqual(len(attempts), 1)

            worker.refresh()
            self.assertTrue(self._wait_until(lambda: len(attempts) == 2))
        finally:
            worker.close()

    @staticmethod
    def _wait_until(predicate: object, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


if __name__ == "__main__":
    unittest.main()
