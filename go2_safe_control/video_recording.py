"""把 Go2 的 RGB 摄像头帧异步保存为常见视频文件。

界面线程只调用 :meth:`VideoRecorder.submit` 投递帧；编码、磁盘写入和文件
封装全部在后台线程完成，避免录制时卡住机器人控制界面。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Literal

import numpy as np

from .session import VideoFrameData


RecordingEvent = Literal["recording", "saved", "stopped", "error"]
RecordingCallback = Callable[[RecordingEvent, str], None]


@dataclass(frozen=True)
class RecordingProfile:
    """一个文件扩展名对应的容器编码设置。"""

    codec: str
    pixel_format: str


def recording_profile(path: Path) -> RecordingProfile:
    """按用户选择的扩展名返回兼容性优先的编码设置。"""

    suffix = path.suffix.lower()
    if suffix == ".mp4":
        return RecordingProfile("mpeg4", "yuv420p")
    if suffix == ".avi":
        return RecordingProfile("mjpeg", "yuvj420p")
    if suffix == ".mkv":
        return RecordingProfile("mpeg4", "yuv420p")
    raise ValueError("录制文件必须使用 MP4、AVI 或 MKV 扩展名。")


class VideoRecorder:
    """有界队列视频录制器；编码速度不足时丢弃旧帧而不阻塞界面。"""

    def __init__(
        self,
        callback: RecordingCallback | None = None,
        *,
        queue_size: int = 90,
    ) -> None:
        self._callback = callback
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[float, VideoFrameData] | None] | None = None
        self._thread: threading.Thread | None = None
        self._output_path: Path | None = None
        self._fps = 20
        self._accepting_frames = False
        self.is_recording = False

    @property
    def output_path(self) -> Path | None:
        """返回当前或最近一次录制所使用的完整文件路径。"""

        with self._lock:
            return self._output_path

    def start(self, output_path: Path, *, fps: int = 20) -> None:
        """开始一次录制；真正创建文件会等到第一帧到达。"""

        output_path = Path(output_path)
        recording_profile(output_path)
        if fps <= 0:
            raise ValueError("录制帧率必须大于 0。")
        with self._lock:
            if self.is_recording:
                raise RuntimeError("已有视频正在录制。")
            self._output_path = output_path
            self._fps = fps
            self._queue = queue.Queue(maxsize=self._queue_size)
            self._accepting_frames = True
            self.is_recording = True
            self._thread = threading.Thread(
                target=self._run,
                name="go2-video-recorder",
                daemon=True,
            )
            self._thread.start()
        self._emit("recording", f"正在录制：{output_path}")

    def submit(self, frame: VideoFrameData) -> None:
        """投递一帧；队列满时丢掉最旧帧，绝不等待编码线程。"""

        with self._lock:
            frame_queue = self._queue if self._accepting_frames else None
        if frame_queue is None:
            return
        item = (time.monotonic(), frame)
        try:
            frame_queue.put_nowait(item)
        except queue.Full:
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                frame_queue.put_nowait(item)
            except queue.Full:
                pass

    def stop(self, *, timeout: float = 5.0) -> bool:
        """停止接收新帧并等待文件封装；返回线程是否已正常退出。"""

        with self._lock:
            if not self.is_recording:
                return True
            should_signal = self._accepting_frames
            self._accepting_frames = False
            frame_queue = self._queue
            thread = self._thread
        if should_signal and frame_queue is not None:
            while True:
                try:
                    frame_queue.put_nowait(None)
                    break
                except queue.Full:
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        break
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        return thread is None or not thread.is_alive()

    close = stop

    def _run(self) -> None:
        output_path = self.output_path
        if output_path is None:
            return
        frame_queue = self._queue
        if frame_queue is None:
            return

        container = None
        stream = None
        first_timestamp: float | None = None
        last_pts = -1
        frames_written = 0
        try:
            import av

            profile = recording_profile(output_path)
            while True:
                item = frame_queue.get()
                if item is None:
                    break
                timestamp, frame = item
                if container is None:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    container = av.open(str(output_path), mode="w")
                    stream = container.add_stream(profile.codec, rate=self._fps)
                    # yuv420p 系列编码要求偶数宽高；Go2 常见 480×270 本身满足。
                    stream.width = max(2, frame.width - frame.width % 2)
                    stream.height = max(2, frame.height - frame.height % 2)
                    stream.pix_fmt = profile.pixel_format
                    first_timestamp = timestamp

                assert stream is not None and first_timestamp is not None
                pts = max(last_pts + 1, round((timestamp - first_timestamp) * self._fps))
                pixels = np.frombuffer(frame.rgb, dtype=np.uint8).reshape(
                    frame.height, frame.width, 3
                )
                video_frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                if (video_frame.width, video_frame.height) != (stream.width, stream.height):
                    video_frame = video_frame.reformat(
                        width=stream.width,
                        height=stream.height,
                        format="rgb24",
                    )
                video_frame.pts = pts
                video_frame.time_base = Fraction(1, self._fps)
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                last_pts = pts
                frames_written += 1

            if container is None or stream is None:
                self._emit("stopped", "录制已停止：未收到摄像头画面，未创建文件。")
                return
            for packet in stream.encode():
                container.mux(packet)
            container.close()
            container = None
            self._emit("saved", f"视频已保存：{output_path}")
        except Exception as exc:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            if frames_written == 0 and output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            self._emit("error", f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._accepting_frames = False
                self.is_recording = False
                self._queue = None
                self._thread = None

    def _emit(self, kind: RecordingEvent, message: str) -> None:
        if self._callback is not None:
            try:
                self._callback(kind, message)
            except Exception:
                # 状态显示失败不能反过来破坏视频封装线程。
                pass
