import time
import unittest

import numpy as np

from go2_safe_control.person_detection import (
    DetectionSnapshot,
    PERSON_BOX_GREEN,
    PERSON_BOX_RED,
    MediaPipePoseClassifier,
    PersonBox,
    PersonDetectionWorker,
    StandingPersonDetector,
    draw_person_boxes,
    is_standing_pose,
    person_boxes_from_yolox,
    split_person_boxes_by_color,
)
from go2_safe_control.session import VideoFrameData


class PersonDetectionPostprocessTests(unittest.TestCase):
    def test_red_mode_only_draws_highest_confidence_person(self) -> None:
        boxes = (
            PersonBox(0, 0, 30, 80, 0.63),
            PersonBox(40, 0, 80, 90, 0.94),
            PersonBox(90, 0, 120, 70, 0.81),
        )

        self.assertEqual(
            split_person_boxes_by_color(boxes, PERSON_BOX_GREEN),
            (boxes, tuple()),
        )
        self.assertEqual(
            split_person_boxes_by_color(boxes, PERSON_BOX_RED),
            (
                tuple(),
                (PersonBox(40, 0, 80, 90, 0.94),),
            ),
        )

    def test_standing_pose_is_accepted_and_seated_pose_is_rejected(self) -> None:
        standing = np.zeros((33, 3), dtype=np.float32)
        standing[:, 2] = 1.0
        for shoulder, hip, knee, ankle, x in (
            (11, 23, 25, 27, 0.42),
            (12, 24, 26, 28, 0.58),
        ):
            standing[shoulder, :2] = (x, 0.20)
            standing[hip, :2] = (x, 0.45)
            standing[knee, :2] = (x, 0.68)
            standing[ankle, :2] = (x, 0.92)
        self.assertTrue(is_standing_pose(standing))

        seated = standing.copy()
        seated[25, :2] = (0.62, 0.52)
        seated[27, :2] = (0.62, 0.78)
        seated[26, :2] = (0.78, 0.52)
        seated[28, :2] = (0.78, 0.78)
        self.assertFalse(is_standing_pose(seated))

    def test_standing_pose_accepts_one_clear_leg_when_other_leg_is_occluded(self) -> None:
        landmarks = np.zeros((33, 3), dtype=np.float32)
        landmarks[:, 2] = 1.0
        for shoulder, hip, knee, ankle, x in (
            (11, 23, 25, 27, 0.42),
            (12, 24, 26, 28, 0.58),
        ):
            landmarks[shoulder, :2] = (x, 0.20)
            landmarks[hip, :2] = (x, 0.45)
            landmarks[knee, :2] = (x, 0.68)
            landmarks[ankle, :2] = (x, 0.92)
        # 左脚被遮挡，但右腿关键点完整且笔直；仍应识别为站立。
        landmarks[[25, 27], 2] = 0.20

        self.assertTrue(is_standing_pose(landmarks))

    def test_pose_classifier_sends_square_minimum_size_crop_to_mediapipe(self) -> None:
        captured: list[np.ndarray] = []

        class FakeImageFormat:
            SRGB = "srgb"

        class FakeImage:
            def __init__(self, *, image_format: object, data: np.ndarray) -> None:
                del image_format
                self.data = data

        class FakeMediaPipe:
            ImageFormat = FakeImageFormat
            Image = FakeImage

        class FakeResult:
            pose_landmarks: list[object] = []

        class FakeLandmarker:
            def detect(self, image: FakeImage) -> FakeResult:
                captured.append(image.data)
                return FakeResult()

        classifier = object.__new__(MediaPipePoseClassifier)
        classifier._mp = FakeMediaPipe
        classifier._landmarker = FakeLandmarker()
        frame = VideoFrameData(100, 60, b"\x72" * (100 * 60 * 3))

        self.assertFalse(classifier.is_standing(frame, PersonBox(40, 5, 60, 55, 0.9)))
        self.assertEqual(captured[0].shape, (256, 256, 3))

    def test_standing_detector_returns_all_standing_candidates_in_confidence_order(self) -> None:
        candidates = (
            PersonBox(0, 0, 30, 80, 0.63),
            PersonBox(40, 0, 80, 90, 0.94),
            PersonBox(90, 0, 120, 70, 0.81),
        )

        class FakePoseClassifier:
            def is_standing(self, _frame: VideoFrameData, box: PersonBox) -> bool:
                return box.confidence != 0.81

        detector = StandingPersonDetector(
            "unused.onnx",
            candidate_detector=_FakeDetector(candidates),
            pose_classifier=FakePoseClassifier(),
        )
        frame = VideoFrameData(2, 1, b"\x00" * 6)

        self.assertEqual(
            detector.detect(frame),
            (
                PersonBox(40, 0, 80, 90, 0.94),
                PersonBox(0, 0, 30, 80, 0.63),
            ),
        )

    def test_only_person_class_is_returned_and_coordinates_are_clamped(self) -> None:
        # 这里直接测试已经解码的 YOLOX 结果：
        # 第 1 行是 person（类别 0），第 2 行是其他类别，必须被忽略。
        decoded = np.zeros((2, 85), dtype=np.float32)
        # 使用明显高于宽的完整人体框，代表站立人员。
        decoded[0, :4] = [40.0, 40.0, 20.0, 100.0]
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
        self.assertEqual(boxes[0], PersonBox(60, 0, 100, 90, 0.72))

    def test_yolox_candidates_remain_available_for_pose_verification(self) -> None:
        decoded = np.zeros((2, 85), dtype=np.float32)
        decoded[:, 4] = [0.95, 0.90]
        decoded[:, 5] = 0.90
        # YOLOX 只负责找人；坐姿判断必须交给后续人体关键点模型。
        decoded[0, :4] = [30.0, 50.0, 24.0, 80.0]
        decoded[1, :4] = [75.0, 60.0, 50.0, 58.0]

        boxes = person_boxes_from_yolox(
            decoded,
            scale=1.0,
            frame_width=100,
            frame_height=100,
            already_decoded=True,
        )

        self.assertEqual(
            boxes,
            (
                PersonBox(18, 10, 42, 90, 0.855),
                PersonBox(50, 31, 100, 89, 0.81),
            ),
        )

    def test_default_yolox_threshold_keeps_distant_person_candidate(self) -> None:
        decoded = np.zeros((1, 85), dtype=np.float32)
        decoded[0, :4] = [50.0, 50.0, 24.0, 70.0]
        decoded[0, 4] = 0.60
        decoded[0, 5] = 0.60

        boxes = person_boxes_from_yolox(
            decoded,
            scale=1.0,
            frame_width=100,
            frame_height=100,
            already_decoded=True,
        )

        self.assertEqual(len(boxes), 1)

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
        decoded[0, :4] = [50.0, 50.0, 30.0, 70.0]
        decoded[1, :4] = [52.0, 52.0, 30.0, 70.0]

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
            self.assertTrue(self._wait_until(lambda: any(kind == "diagnostic" for kind, _ in events)))

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
