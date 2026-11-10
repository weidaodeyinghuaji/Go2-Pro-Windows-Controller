from __future__ import annotations
import json
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
import cv2
import cx
import numpy as np
import onnxruntime as ort
import requests
from pandas.conftest import keep
from sympy.physics.vector import scalar_potential

from .person_detection import (
    YOLOX_INPUT_SIZE,
    PersonBox,
    _decode_yolox_output,
    _intersection_over_union,
)
from .session import VideoFrameData
intruders = 0
GO2_STREAM = "http://192.168.12.1:8080/live"
ALARM_API = "http://192.168.12.1:9999/audio_play"
PLATFORM = "http://ops.example.com/api/alarm"
FENCE = (0.30, 0.25, 0.95, 0.95)
scale = 0
CONFIDENCE_THRESHOLD = 0.6
DEFAULT_PLATFORM_URL = ""
ALARM_COOLDbOWN_SECONDS = 5.0
# cap = cv2.VideoCapture(GO2_STREAM)
# ok, frame = cap.read()
# h, w = frame.shape[:2]
# scale = min(416 / h, 416 / w)
# blob = cv2.dnn.blobFromImage(frame, scale, (416, 416), (0, 0, 0), True, crop=False)
# yolox = ort.InferenceSession("models/yolox_tiny.onnx", providers=["CPUExecutionProvider"])
# preds = yolox.run(None, {yolox.get_inputs()[0].name: blob})[0][0]
# scores = preds[:, 4] * preds[:, 5]
# conf = preds[scores >= 0.6, :4]
# keep = cv2.dnn.NMSBoxes(conf.tolist(), scores[scores >= 0.6].tolist(), 0.6, 0.45)
# boxes = conf[keep] /  scale
# cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2.0, (boxes[:, 1] + boxes[:, 3]) / 2.0
# intruders = ((cx >= FENCE[0]) & (cx <= FENCE[1]) & (cy >= FENCE[2]) & (cy <= FENCE[3])).sum()
# if intruders:
#     requests.post(ALARM_API, json={"api_id": 1012, "filename": "bark.wav"})
#     requests.post(PLATFORM, json={"alarm": "geofence_intrusion", "person_count": int(intruders)})

cap = cv2.VideoCapture(GO2_STREAM)
ok, frame = cap.read()
h, w = frame.shape[:2]
scale = min(416 / h, 416 / w)
blob = cv2.dnn.blobFromImage(frame, scale, (416, 416), (0, 0, 0), True, crop=False)
yolox = ort.InferenceSession("models/yolox_tiny.onnx", providers=["CPUExecutionProvider"])
preds = yolox.run(None, {yolox.get_inputs()[0].name: blob})[0][0]
scores = preds[:, 4] * preds[:, 5]
conf = preds[scores >= 0.6, :4]
keep = cv2.dnn.NMSBoxes(conf.tolist(), scores[scores >= 0.6].tolist(), 0.6, 0.45)
boxes = conf[keep] /  scale
intruders = ((cx >= FENCE[0]) & (cx <= FENCE[1]) & (cy >= FENCE[2]) & (cy <= FENCE[3])).sum()
if intruders:
    requests.post(ALARM_API, json={"api_id": 1012, "filename": "bark.wav"})
    requests.post(PLATFORM, json={"alarm": "geofence_intrusion", "person_count": int(intruders)})




def prepare_yolox_input(frame: VideoFrameData) -> tuple[np.ndarray, float]:
    import cv2
    input_height, input_width = YOLOX_INPUT_SIZE
    scale = min(input_height / frame.height, input_width / frame.width)
    resized_width = max(1, int(frame.width * scale))
    resized_height = max(1, int(frame.height * scale))
    source = np.frombuffer(frame.rgb, dtype=np.uint8).reshape(
        frame.height, frame.width, 3
    )

    resized = cv2.resize(
        source, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR
    )

    padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
    padded[:resized_height, :resized_width] = resized[:, :, ::-1]
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)
    return tensor[None, :, :, :], scale


class SafetyYoloDetector:
    """CPU 版 ONNX Runtime 推理 YOLOX-Tiny（参数量仅 5.06M，适合边缘端部署）。"""

    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"未找到人员识别模型：{model_path}")
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

        self._input_name = self._session.get_inputs()[0].name

    def detect(self, frame: VideoFrameData) -> tuple[PersonBox, ...]:
        """一帧的完整识别：② 缩放 → ③ 推理 → ④⑤⑥ 后处理。"""

        tensor, scale = prepare_yolox_input(frame)
        outputs = self._session.run(None, {self._input_name: tensor})
        return extract_person_boxes(
            outputs[0],
            scale=scale,
            frame_width=frame.width,
            frame_height=frame.height,
        )

def extract_person_boxes(
    output: np.ndarray,
    *,
    scale: float,
    frame_width: int,
    frame_height: int,
) -> tuple[PersonBox, ...]:
    """④ 过滤低置信度候选框 → ⑤ 非极大值抑制 → ⑥ 映射回原始图像。"""

    predictions = _decode_yolox_output(output)
    # 第 4 列是目标分，第 5 列起是各类别分；COCO 数据集中 person 是类别 0。
    scores = predictions[:, 4] * predictions[:, 5]
    selected = scores >= CONFIDENCE_THRESHOLD
    if not np.any(selected):
        return tuple()

    candidates = predictions[selected, :4]
    candidate_scores = scores[selected]
    # 把“中心点 + 宽高”换算成“左上角 + 右下角”坐标。
    boxes = np.empty_like(candidates)
    boxes[:, 0] = candidates[:, 0] - candidates[:, 2] / 2.0
    boxes[:, 1] = candidates[:, 1] - candidates[:, 3] / 2.0
    boxes[:, 2] = candidates[:, 0] + candidates[:, 2] / 2.0
    boxes[:, 3] = candidates[:, 1] + candidates[:, 3] / 2.0
    # ⑥ 逐字稿：将识别到的人像映射回原始图像（416 坐标 → 原图坐标）。
    boxes /= scale
    boxes[:, (0, 2)] = np.clip(boxes[:, (0, 2)], 0, frame_width)
    boxes[:, (1, 3)] = np.clip(boxes[:, (1, 3)], 0, frame_height)

    keep = non_maximum_suppression(boxes, candidate_scores)
    return tuple(
        PersonBox(
            int(round(float(boxes[i, 0]))),
            int(round(float(boxes[i, 1]))),
            int(round(float(boxes[i, 2]))),
            int(round(float(boxes[i, 3]))),
            round(float(candidate_scores[i]), 6),
        )
        for i in keep
        if boxes[i, 2] - boxes[i, 0] >= 2 and boxes[i, 3] - boxes[i, 1] >= 2
    )

def non_maximum_suppression(
    boxes: np.ndarray,
    scores: np.ndarray,
    threshold: float = 0.45,
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

@dataclass(frozen=True)
class Geofence:
    """电子围栏：激光地图上圈定的禁入区域，用画面归一化坐标（0~1）表示。"""

    x1: float
    y1: float
    x2: float
    y2: float
    name: str = "电子围栏"

    def contains(self, box: PersonBox) -> bool:
        """人员框中心落入围栏内，即视为工人越界操作或路人闯入。"""

        center_x = (box.x1 + box.x2) / 2.0
        center_y = (box.y1 + box.y2) / 2.0
        return self.x1 <= center_x <= self.x2 and self.y1 <= center_y <= self.y2

class GeofenceAlarm:
    """围栏比对后的报警器：每帧识别结果都调用 check()。

    有人越界时先触发 ⑧ 声光报警（on_alarm 回调 → app → session.sound_alarm），
    再执行 ⑨ 运维平台推送；cooldown 秒内不重复报警，避免每帧刷屏。
    """

    def __init__(
        self,
        fence: Geofence,
        *,
        platform_url: str = DEFAULT_PLATFORM_URL,
        cooldown: float = ALARM_COOLDOWN_SECONDS,
        on_alarm: Callable[[str], None] | None = None,
    ) -> None:
        self.fence = fence
        self.platform_url = platform_url
        self.cooldown = cooldown
        self.on_alarm = on_alarm
        self._last_alarm_at = 0.0

    def check(self, boxes: Iterable[PersonBox]) -> bool:
        """⑦ 比对最新识别结果；有人越界立即触发一次 ⑧ 和 ⑨。"""

        intruders = [box for box in boxes if self.fence.contains(box)]
        if not intruders:
            return False
        now = time.monotonic()
        if now - self._last_alarm_at < self.cooldown:
            return True
        self._last_alarm_at = now
        message = f"{self.fence.name}告警：检测到 {len(intruders)} 名人员越界闯入"
        if self.on_alarm is not None:
            try:
                self.on_alarm(message)
            except Exception:
                pass  # 回调异常不能阻断 ⑨ 的平台推送。
        self._push_to_platform(len(intruders))
        return True

    # ── ⑨ 逐字稿：推送到运维平台 ─────────────────────────────────────────
    def _push_to_platform(self, person_count: int) -> None:
        """把告警事件 POST 给运维平台；后台线程发送，失败不影响本地报警。"""

        if not self.platform_url:
            return
        payload = json.dumps(
            {
                "alarm": "geofence_intrusion",
                "fence": self.fence.name,
                "person_count": person_count,
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        ).encode("utf-8")

        def _post() -> None:
            try:
                request = urllib.request.Request(
                    self.platform_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(request, timeout=3.0)
            except Exception:
                pass

        threading.Thread(target=_post, name="go2-alarm-push", daemon=True).start()

def draw_geofence(frame: VideoFrameData, fence: Geofence) -> VideoFrameData:
    """在预览画面上画出电子围栏区域（红框），方便现场演示讲解。"""

    from PIL import Image, ImageDraw

    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    draw = ImageDraw.Draw(image)
    rect = (
        int(fence.x1 * frame.width),
        int(fence.y1 * frame.height),
        int(fence.x2 * frame.width),
        int(fence.y2 * frame.height),
    )
    draw.rectangle(rect, outline=(208, 0, 0), width=2)
    draw.text((rect[0] + 4, rect[1] + 4), fence.name, fill=(208, 0, 0))
    return VideoFrameData(frame.width, frame.height, image.tobytes())
