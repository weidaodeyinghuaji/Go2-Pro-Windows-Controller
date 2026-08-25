"""Go2 摄像头的人员识别逻辑。

模型推理放在独立线程，Tkinter 和 WebRTC 线程只负责交接最新一帧。
模块在真正开始识别前不会导入 Pillow/ONNX Runtime，因此即使识别环境损坏，
机器狗的连接、停止和运动控制界面仍能正常启动。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from .session import VideoFrameData


YOLOX_INPUT_SIZE = (416, 416)
PERSON_CLASS_INDEX = 0
PERSON_BOX_GREEN = "#00C853"
PERSON_BOX_RED = "#D00000"
DEFAULT_CONFIDENCE = 0.45
DEFAULT_NMS_THRESHOLD = 0.45


def default_model_path() -> Path:
    """返回通用安装器下载的固定 YOLOX-Tiny ONNX 模型位置。"""

    return Path(__file__).resolve().parents[1] / "models" / "yolox_tiny.onnx"


@dataclass(frozen=True)
class PersonBox:
    """一名人员在原始摄像头画面中的矩形范围。"""

    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float


@dataclass(frozen=True)
class DetectionSnapshot:
    """一次识别结果及其生成时间，用于丢弃已经过期的框。"""

    boxes: tuple[PersonBox, ...]
    generated_at: float


class PersonDetector(Protocol):
    """后台工作线程所需的最小模型接口，测试时可以替换成假模型。"""

    def detect(self, frame: VideoFrameData) -> tuple[PersonBox, ...]: ...


DetectorFactory = Callable[[Path], PersonDetector]
StatusCallback = Callable[[str, str], None]


def _decode_yolox_output(output: np.ndarray) -> np.ndarray:
    """把 YOLOX ONNX 的网格偏移输出还原为中心点和宽高。"""

    predictions = np.asarray(output, dtype=np.float32)
    if predictions.ndim == 3:
        predictions = predictions[0]
    if predictions.ndim != 2 or predictions.shape[1] < 6:
        raise ValueError(f"YOLOX 输出形状无效：{predictions.shape}")

    grids: list[np.ndarray] = []
    expanded_strides: list[np.ndarray] = []
    input_height, input_width = YOLOX_INPUT_SIZE
    for stride in (8, 16, 32):
        grid_height = input_height // stride
        grid_width = input_width // stride
        x_grid, y_grid = np.meshgrid(np.arange(grid_width), np.arange(grid_height))
        grid = np.stack((x_grid, y_grid), axis=2).reshape(-1, 2)
        grids.append(grid)
        expanded_strides.append(np.full((grid.shape[0], 1), stride))

    grid_array = np.concatenate(grids, axis=0).astype(np.float32)
    stride_array = np.concatenate(expanded_strides, axis=0).astype(np.float32)
    if predictions.shape[0] != grid_array.shape[0]:
        raise ValueError(
            "YOLOX 模型尺寸与程序不匹配："
            f"模型输出 {predictions.shape[0]} 个候选框，程序期望 {grid_array.shape[0]} 个"
        )

    decoded = predictions.copy()
    decoded[:, :2] = (decoded[:, :2] + grid_array) * stride_array
    decoded[:, 2:4] = np.exp(np.clip(decoded[:, 2:4], -20.0, 20.0)) * stride_array
    return decoded


def _intersection_over_union(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    """计算一个框和其余框的重叠比例。"""

    left_top = np.maximum(box[:2], boxes[:, :2])
    right_bottom = np.minimum(box[2:], boxes[:, 2:])
    size = np.maximum(0.0, right_bottom - left_top)
    intersection = size[:, 0] * size[:, 1]
    box_area = max(0.0, float(box[2] - box[0])) * max(0.0, float(box[3] - box[1]))
    boxes_area = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
        0.0, boxes[:, 3] - boxes[:, 1]
    )
    return intersection / np.maximum(box_area + boxes_area - intersection, 1e-6)


def _non_maximum_suppression(
    boxes: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> list[int]:
    """保留置信度更高的框，删除同一个人产生的重复框。"""

    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        remaining = order[1:]
        overlaps = _intersection_over_union(boxes[index], boxes[remaining])
        order = remaining[overlaps <= threshold]
    return keep


def person_boxes_from_yolox(
    output: np.ndarray,
    *,
    scale: float,
    frame_width: int,
    frame_height: int,
    confidence_threshold: float = DEFAULT_CONFIDENCE,
    nms_threshold: float = DEFAULT_NMS_THRESHOLD,
    already_decoded: bool = False,
) -> tuple[PersonBox, ...]:
    """从 YOLOX 输出中只提取 COCO 的 ``person``（类别 0）。"""

    if scale <= 0 or frame_width <= 0 or frame_height <= 0:
        raise ValueError("人员识别的画面尺寸或缩放比例无效")
    predictions = np.asarray(output, dtype=np.float32)
    if not already_decoded:
        predictions = _decode_yolox_output(predictions)
    elif predictions.ndim == 3:
        predictions = predictions[0]
    if predictions.ndim != 2 or predictions.shape[1] <= 5 + PERSON_CLASS_INDEX:
        raise ValueError(f"YOLOX 输出形状无效：{predictions.shape}")

    scores = predictions[:, 4] * predictions[:, 5 + PERSON_CLASS_INDEX]
    selected = scores >= confidence_threshold
    if not np.any(selected):
        return tuple()

    candidates = predictions[selected, :4]
    candidate_scores = scores[selected]
    boxes = np.empty_like(candidates)
    boxes[:, 0] = candidates[:, 0] - candidates[:, 2] / 2.0
    boxes[:, 1] = candidates[:, 1] - candidates[:, 3] / 2.0
    boxes[:, 2] = candidates[:, 0] + candidates[:, 2] / 2.0
    boxes[:, 3] = candidates[:, 1] + candidates[:, 3] / 2.0
    boxes /= scale
    boxes[:, (0, 2)] = np.clip(boxes[:, (0, 2)], 0, frame_width)
    boxes[:, (1, 3)] = np.clip(boxes[:, (1, 3)], 0, frame_height)

    keep = _non_maximum_suppression(boxes, candidate_scores, nms_threshold)
    results: list[PersonBox] = []
    for index in keep:
        x1, y1, x2, y2 = boxes[index]
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        results.append(
            PersonBox(
                int(round(float(x1))),
                int(round(float(y1))),
                int(round(float(x2))),
                int(round(float(y2))),
                round(float(candidate_scores[index]), 6),
            )
        )
    return tuple(results)


def _prepare_yolox_input(frame: VideoFrameData) -> tuple[np.ndarray, float]:
    """把 480×270 RGB 帧按官方 YOLOX 方式缩放并填充到 416×416。"""

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow；请重新运行 01_安装环境_双击.bat") from exc

    input_height, input_width = YOLOX_INPUT_SIZE
    scale = min(input_height / frame.height, input_width / frame.width)
    resized_width = max(1, int(frame.width * scale))
    resized_height = max(1, int(frame.height * scale))
    source = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    resized = source.resize((resized_width, resized_height), Image.Resampling.BILINEAR)
    padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
    # YOLOX 官方模型使用 BGR 通道，并把缩放结果放在左上角。
    padded[:resized_height, :resized_width] = np.asarray(resized)[:, :, ::-1]
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)
    return tensor[None, :, :, :], scale


class OnnxYoloxPersonDetector:
    """使用 CPU 版 ONNX Runtime 执行 YOLOX-Tiny 人员识别。"""

    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(
                f"未找到人员识别模型：{model_path}；请重新运行 01_安装环境_双击.bat"
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("缺少 onnxruntime；请重新运行 01_安装环境_双击.bat") from exc

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name

    def detect(self, frame: VideoFrameData) -> tuple[PersonBox, ...]:
        tensor, scale = _prepare_yolox_input(frame)
        outputs = self._session.run(None, {self._input_name: tensor})
        if not outputs:
            raise RuntimeError("YOLOX 模型没有返回输出")
        return person_boxes_from_yolox(
            outputs[0],
            scale=scale,
            frame_width=frame.width,
            frame_height=frame.height,
        )


def draw_person_boxes(
    frame: VideoFrameData,
    boxes: tuple[PersonBox, ...],
    color: str,
) -> VideoFrameData:
    """在 RGB 帧上绘制人员框；不会保存图片或修改原始帧。"""

    if not boxes:
        return frame
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow；请重新运行 01_安装环境_双击.bat") from exc

    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    for box in boxes:
        draw.rectangle((box.x1, box.y1, box.x2, box.y2), outline=color, width=3)
        label = f"PERSON {box.confidence:.0%}"
        text_box = draw.textbbox((box.x1, box.y1), label, font=font)
        text_height = text_box[3] - text_box[1]
        text_width = text_box[2] - text_box[0]
        label_top = max(0, box.y1 - text_height - 4)
        draw.rectangle(
            (box.x1, label_top, min(frame.width, box.x1 + text_width + 6), box.y1),
            fill=color,
        )
        draw.text((box.x1 + 3, label_top + 1), label, fill="white", font=font)
    return VideoFrameData(frame.width, frame.height, image.tobytes())


class PersonDetectionWorker:
    """只处理最新帧的后台识别线程，避免慢电脑积压视频。"""

    def __init__(
        self,
        model_path: str | Path,
        *,
        detector_factory: DetectorFactory = OnnxYoloxPersonDetector,
        status_callback: StatusCallback | None = None,
        min_interval: float = 0.10,
    ) -> None:
        self._model_path = Path(model_path)
        self._detector_factory = detector_factory
        self._status_callback = status_callback
        self._min_interval = max(0.0, min_interval)
        self._condition = threading.Condition()
        self._latest_frame: VideoFrameData | None = None
        self._snapshot: DetectionSnapshot | None = None
        self._generation = 0
        self._started = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    def submit(self, frame: VideoFrameData) -> None:
        """覆盖待处理帧；后台来不及处理时旧帧会被主动丢弃。"""

        self._ensure_started()
        with self._condition:
            if self._stopped:
                return
            self._latest_frame = frame
            self._condition.notify_all()

    def refresh(self) -> None:
        """清空旧结果并要求后台线程重新创建模型会话。"""
        self._ensure_started()
        with self._condition:
            if self._stopped:
                return
            self._generation += 1
            self._snapshot = None
            self._condition.notify_all()

    def clear(self) -> None:
        """关闭摄像头时清除待识别帧和画面上的旧框。"""

        with self._condition:
            self._latest_frame = None
            self._snapshot = None

    def snapshot(self, max_age: float = 0.5) -> DetectionSnapshot | None:
        """返回仍与当前实时画面接近的最近识别结果。"""

        with self._condition:
            snapshot = self._snapshot
        if snapshot is None or time.monotonic() - snapshot.generated_at > max_age:
            return None
        return snapshot

    def close(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _ensure_started(self) -> None:
        with self._condition:
            if self._started or self._stopped:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                name="go2-person-detection",
                daemon=True,
            )
            self._thread.start()

    def _emit(self, kind: str, message: str) -> None:
        callback = self._status_callback
        if callback is None:
            return
        try:
            callback(kind, message)
        except Exception:
            # 日志回调本身异常时不能杀死识别线程或影响摄像头。
            pass

    def _run(self) -> None:
        detector: PersonDetector | None = None
        detector_generation = -1
        failed_generation: int | None = None
        last_finished_at = 0.0
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopped
                    or self._latest_frame is not None
                    or detector_generation != self._generation
                )
                if self._stopped:
                    return
                generation = self._generation
                frame = self._latest_frame
                self._latest_frame = None

            # 同一代模型一旦加载/推理失败，就等待用户点击刷新；不能每帧重载并刷日志。
            if failed_generation == generation:
                continue
            if detector is None or detector_generation != generation:
                self._emit("loading", "正在加载人员识别模型……")
                try:
                    detector = self._detector_factory(self._model_path)
                except Exception as exc:
                    detector = None
                    detector_generation = generation
                    failed_generation = generation
                    self._emit("error", f"{type(exc).__name__}: {exc}")
                    continue
                detector_generation = generation
                failed_generation = None
                self._emit("ready", "人员识别模型已就绪")

            if frame is None or detector is None:
                continue
            remaining = self._min_interval - (time.monotonic() - last_finished_at)
            if remaining > 0:
                time.sleep(remaining)
            try:
                boxes = detector.detect(frame)
            except Exception as exc:
                self._emit("error", f"{type(exc).__name__}: {exc}")
                detector = None
                failed_generation = generation
                continue
            finished_at = time.monotonic()
            last_finished_at = finished_at
            with self._condition:
                # 刷新发生在推理过程中时，旧模型结果不能重新出现在画面上。
                if generation == self._generation and not self._stopped:
                    self._snapshot = DetectionSnapshot(boxes, finished_at)
