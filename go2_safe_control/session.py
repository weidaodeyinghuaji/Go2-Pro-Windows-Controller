"""Go2 WebRTC 通信层。

Tkinter 界面运行在主线程；WebRTC/asyncio 运行在独立后台线程。
界面调用本类的普通方法，普通方法再把协程提交给后台事件循环。
这样网络等待不会卡死窗口，同时所有运动命令仍经过统一安全检查。

初学者阅读顺序：
1. 先看公开方法 connect/update_velocity/emergency_stop，它们由界面线程调用；
2. 再看 _submit，理解普通方法如何把 async 协程交给后台线程；
3. 最后看 _control_loop 与 _send_move，理解速度为何要连续发送。
名称前带单下划线的方法表示“类内部实现”，界面层不应直接调用。
"""

from __future__ import annotations

# asyncio 负责在一个后台线程中调度连接、发命令、收视频等异步任务。
import asyncio
import json
import random
# threading 用来把网络事件循环和 Tkinter 主线程隔离。
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

# 这是项目的第三方 WebRTC 依赖：提供主题表、连接对象和连接方式枚举。
from unitree_webrtc_connect import (
    RTC_TOPIC,
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

from .protocol import (
    SPORT_API_MOVE,
    motion_mode_options,
    normal_motion_mode_options,
    parse_motion_mode,
    require_sport_action_success,
    SportActionResponseError,
    sport_action_name,
    sport_action_options,
    stand_down_options,
    stand_up_options,
    stop_options,
)
from .safety import Velocity, watchdog_velocity


# Literal 限制事件名称只能从下面几个字符串中选择，拼错时 PyCharm 会提醒。
EventKind = Literal[
    "status",
    "connected",
    "disconnected",
    "error",
    "action_error",
    "action_warning",
    "walk_ready",
    "walk_not_ready",
    "camera",
    "diagnostic",
]
# Callable[[参数类型...], 返回类型] 用来描述“可被调用的回调函数”。
EventCallback = Callable[[EventKind, str], None]
VideoFrameCallback = Callable[["VideoFrameData"], None]
# frozenset 是不可修改的集合；这两个固件模式都允许基础 Move/StopMove。
WALK_READY_MODES = frozenset({"normal", "mcf"})


@dataclass(frozen=True)
class ConnectionSettings:
    """一次连接所需的信息；AES key 只保存在当前进程内。"""

    # Literal 说明 mode 只能是 "AP" 或 "STA"，而不是任意字符串。
    mode: Literal["AP", "STA"]
    ip: str = ""
    aes_key: str = ""


@dataclass(frozen=True)
class VideoFrameData:
    """从后台线程交给 GUI 的一帧 RGB 图像。"""
    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        """dataclass 创建对象后自动调用，用来阻止无效视频帧进入界面。"""

        if self.width <= 0 or self.height <= 0:
            raise ValueError("视频尺寸必须为正数")
        if len(self.rgb) != self.width * self.height * 3:
            raise ValueError("RGB 数据长度与视频尺寸不一致")

    def ppm_bytes(self) -> bytes:
        """为原始 RGB 数据加上 PPM 文件头，供 Tkinter PhotoImage 读取。"""

        # P6 是二进制 PPM；头部必须是 ASCII，后面直接拼接 RGB 字节。
        return f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.rgb


class RobotSession:
    """管理后台 WebRTC 连接，并执行停止看门狗和动作串行化。"""

    # 0.10 秒发送一次速度，即约 10Hz；超过 0.35 秒没更新就由看门狗归零。
    CONTROL_PERIOD = 0.10
    WATCHDOG_TIMEOUT = 0.35
    REQUEST_TIMEOUT = 0.80
    # 姿态动作可能在真正开始执行后才回复，不能沿用普通查询的 0.8 秒。
    ACTION_REQUEST_TIMEOUT = 5.0

    def __init__(
        self,
        on_event: EventCallback,
        on_video_frame: VideoFrameCallback | None = None,
    ) -> None:
        # 保存 GUI 提供的回调。后台线程不能直接操作 Tk 控件，只能把消息交回 GUI。
        self._on_event = on_event
        self._on_video_frame = on_video_frame
        # GUI 线程和网络线程都会读写速度/连接状态；Lock 防止它们同时修改。
        self._state_lock = threading.Lock()
        self._desired = Velocity.zero()
        self._last_update = 0.0
        self._connected = False
        self._walk_ready = False
        # 保存当前连接方式，用于只在 AP 模式下输出针对性的运行诊断。
        self._connection_mode: Literal["AP", "STA"] | None = None
        # safety_epoch 是“安全操作版本号”。每次急停或新动作都会加一，
        # 较早启动、较晚返回的准备任务发现版本不一致时必须作废。
        self._safety_epoch = 0
        self._stop_failure_reported = False
        self._video_enabled = False
        self._video_callback_registered = False

        # Tkinter 不能被网络等待阻塞，所以创建独立的 asyncio 事件循环和线程。
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="go2-webrtc-loop",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(timeout=2.0)

        # 连接成功前这些对象都不存在，所以类型中包含 None。
        self._conn: UnitreeWebRTCConnection | None = None
        self._command_lock: asyncio.Lock | None = None
        self._action_lock: asyncio.Lock | None = None
        self._control_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        """线程安全地读取当前连接状态。"""

        # with 会自动 acquire/release 锁，即使中间发生异常也能释放。
        with self._state_lock:
            return self._connected

    @property
    def walk_ready(self) -> bool:
        """线程安全地读取运动模式是否已经允许行走。"""

        with self._state_lock:
            return self._walk_ready

    def connect(self, settings: ConnectionSettings) -> None:
        """公开的非阻塞连接入口：提交任务后立即把控制权还给 GUI。"""

        self._submit(self._connect(settings))

    def update_velocity(self, velocity: Velocity) -> None:
        """更新“期望速度”；真正发送由后台 _control_loop 周期执行。"""

        with self._state_lock:
            # 模式未准备时，即使界面误传了非零值也在通信层再次归零。
            self._desired = velocity if self._walk_ready else Velocity.zero()
            # monotonic 只会向前走，不受用户修改系统时间影响，适合计算超时。
            self._last_update = time.monotonic()

    def prepare_walk_mode(self) -> None:
        """清零并异步检查 normal/mcf 模式；成功后才允许更新非零速度。"""

        with self._state_lock:
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._walk_ready = False
            self._safety_epoch += 1
            safety_epoch = self._safety_epoch
        self._submit(self._prepare_walk_mode(safety_epoch))

    def stand_down(self) -> None:
        """公开卧趴入口：先让旧速度和旧准备任务失效，再提交动作。"""

        with self._state_lock:
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._walk_ready = False
            self._safety_epoch += 1
        self._submit(self._stand_down())

    def stand_up(self) -> None:
        """公开站起入口；与卧趴使用相同的安全失效顺序。"""

        with self._state_lock:
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._walk_ready = False
            self._safety_epoch += 1
        self._submit(self._stand_up())

    def sport_action(self, action: str) -> None:
        """执行动作表中的高层动作，例如坐下、打招呼、伸展。"""

        # 先调用一次只为验证名称；未知动作在进入后台线程前就立即报错。
        sport_action_options(action)
        with self._state_lock:
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._walk_ready = False
            self._safety_epoch += 1
        self._submit(self._sport_action(action))

    def start_video(self) -> None:
        """异步请求开启只读摄像头通道。"""

        self._submit(self._start_video())

    def stop_video(self) -> None:
        self._submit(self._stop_video())

    def emergency_stop(self) -> None:
        """立即在本地清零并使准备状态失效，同时最多重试三次 StopMove。"""

        with self._state_lock:
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._walk_ready = False
            self._safety_epoch += 1
        if self.connected:
            self._submit(self._send_stop(retries=3))

    def disconnect(self) -> None:
        self.update_velocity(Velocity.zero())
        self._submit(self._disconnect())

    def shutdown(self, timeout: float = 3.0) -> None:
        """程序退出时同步等待断开，然后停止后台事件循环和线程。"""

        self.update_velocity(Velocity.zero())
        if self._loop.is_closed():
            return
        # 返回的 Future 能从当前线程等待后台协程完成。
        future = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        try:
            future.result(timeout=timeout)
        except Exception:
            # 退出流程不能无限卡住；实体遥控器仍是最终安全保障。
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1.0)

    def _run_loop(self) -> None:
        """后台线程入口：运行 asyncio，退出时取消遗留任务并关闭事件循环。"""

        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        self._loop.close()

    def _submit(self, coroutine: object) -> None:
        """把主线程创建的协程安全地交给后台 asyncio 线程。"""
        if self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)  # type: ignore[arg-type]

        def report_failure(done: object) -> None:
            """协程结束后的回调：把后台异常转成 GUI 可显示的 error 事件。"""

            try:
                done.result()  # type: ignore[attr-defined]
            except Exception as exc:
                self._emit("error", f"{type(exc).__name__}: {exc}")

        future.add_done_callback(report_failure)

    async def _connect(self, settings: ConnectionSettings) -> None:
        """后台连接实现：先清理旧连接，再按 AP/STA 创建新连接。"""

        # await 会暂停当前协程，让事件循环先处理其他任务，不会冻结 GUI。
        await self._disconnect()
        self._connection_mode = settings.mode
        if settings.mode == "AP":
            self._emit("diagnostic", "[AP] 开始连接 LocalAP，目标 192.168.12.1")
        self._emit("status", "正在建立 WebRTC 连接……")

        # kwargs 是稍后用 **kwargs 展开的可选关键字参数。
        kwargs: dict[str, str] = {}
        if settings.aes_key:
            kwargs["aes_128_key"] = settings.aes_key
        if settings.mode == "AP":
            method = WebRTCConnectionMethod.LocalAP
        else:
            method = WebRTCConnectionMethod.LocalSTA
            kwargs["ip"] = settings.ip

        conn = UnitreeWebRTCConnection(method, **kwargs)
        try:
            # wait_for 给连接过程设置 20 秒硬超时，避免一直卡在“正在连接”。
            await asyncio.wait_for(conn.connect(), timeout=20.0)
        except Exception:
            try:
                await conn.disconnect()
            except Exception:
                pass
            raise

        self._conn = conn
        self._command_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        with self._state_lock:
            self._connected = True
            self._walk_ready = False
            self._desired = Velocity.zero()
            self._last_update = time.monotonic()
            self._stop_failure_reported = False
        # create_task 让速度循环与当前连接协程并发运行。
        self._control_task = asyncio.create_task(self._control_loop())
        target = "Go2 热点" if settings.mode == "AP" else settings.ip
        self._emit("connected", f"已连接：{settings.mode} / {target}")
        if settings.mode == "AP":
            self._emit("diagnostic", "[AP] WebRTC 连接成功；等待准备行走模式")
        self._emit("walk_not_ready", "已连接；请先执行“准备行走模式”。")

    async def _disconnect(self) -> None:
        """后台断开实现：停止视频和运动，再取消控制循环并释放连接对象。"""

        with self._state_lock:
            self._safety_epoch += 1
        conn = self._conn
        if conn is None:
            with self._state_lock:
                self._connected = False
                self._walk_ready = False
            self._video_enabled = False
            self._video_callback_registered = False
            return

        await self._stop_video()
        await self._send_stop(retries=3)
        with self._state_lock:
            self._connected = False
            self._walk_ready = False
            self._desired = Velocity.zero()

        task = self._control_task
        self._control_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        try:
            await asyncio.wait_for(conn.disconnect(), timeout=2.0)
        finally:
            self._conn = None
            self._command_lock = None
            self._action_lock = None
            self._video_callback_registered = False
            self._emit("disconnected", "已断开；停止命令已发送。")

    async def _control_loop(self) -> None:
        """约 10 Hz 刷新速度；超时或异常时主动发送停止。"""

        # last_sent 用来判断是否刚从“移动”变成“停止”，从而补发 StopMove。
        last_sent = Velocity.zero()
        try:
            while self.connected:
                now = time.monotonic()
                with self._state_lock:
                    desired = self._desired
                    last_update = self._last_update
                    walk_ready = self._walk_ready
                if not walk_ready:
                    desired = Velocity.zero()
                # 看门狗可能把已经过期的 desired 改成零速度。
                effective = watchdog_velocity(
                    desired,
                    last_update=last_update,
                    now=now,
                    timeout=self.WATCHDOG_TIMEOUT,
                )

                if effective.moving:
                    await self._send_move(effective)
                    if getattr(self, "_connection_mode", None) == "AP" and effective != last_sent:
                        self._emit(
                            "diagnostic",
                            "[AP] Move 开始/更新："
                            f"x={effective.forward:+.2f} y={effective.lateral:+.2f} "
                            f"z={effective.yaw:+.2f}，通信层将以约 10Hz 持续发送",
                        )
                    last_sent = effective
                elif last_sent.moving:
                    if await self._send_stop(retries=2):
                        if getattr(self, "_connection_mode", None) == "AP":
                            self._emit("diagnostic", "[AP] Move 已停止并发送 StopMove")
                        last_sent = effective
                # sleep 让出事件循环，并控制发送频率约为 10Hz。
                await asyncio.sleep(self.CONTROL_PERIOD)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with self._state_lock:
                self._desired = Velocity.zero()
                self._connected = False
                self._walk_ready = False
            self._emit("error", f"运动通信异常，已请求停止：{type(exc).__name__}: {exc}")
            await self._send_stop(retries=3)

    async def _send_move(self, velocity: Velocity) -> None:
        """发送一帧无需回复的 Move 消息，供 10Hz 控制流连续调用。"""

        conn = self._conn
        lock = self._command_lock
        if conn is None or lock is None:
            raise RuntimeError("WebRTC 尚未连接")

        # Go2 的 Move 是连续的“无需回复”消息流。如果错误地等待 RPC 回复，
        # 10Hz 循环会一直等到超时，机器人只来得及前探，无法形成连续步态。
        # 用当前毫秒和随机数生成请求 ID；取模保证不超过常见 32 位正整数范围。
        request_id = (
            int(time.time() * 1000) % 2147483648
            + random.randint(0, 1000)
        )
        payload: dict[str, object] = {
            "header": {
                "identity": {"id": request_id, "api_id": SPORT_API_MOVE},
                "policy": {"priority": 0, "noreply": True},
            },
            "parameter": json.dumps(
                {
                    "x": velocity.forward,
                    "y": velocity.lateral,
                    "z": velocity.yaw,
                }
            ),
            "binary": [],
        }
        # async with 保证同一时刻只有一个协程占用数据通道发送运动命令。
        async with lock:
            conn.datachannel.pub_sub.publish_without_callback(
                RTC_TOPIC["SPORT_MOD"],
                payload,
            )

    async def _prepare_walk_mode(self, safety_epoch: int | None = None) -> None:
        """取得动作锁后准备行走，避免和站起、卧趴等动作同时发送。"""

        if not self.connected:
            self._emit("error", "尚未连接，无法准备行走模式。")
            return
        action_lock = self._action_lock
        if action_lock is None:
            raise RuntimeError("WebRTC 尚未连接")
        async with action_lock:
            await self._prepare_walk_mode_locked(safety_epoch)

    async def _prepare_walk_mode_locked(self, safety_epoch: int | None) -> None:
        """动作锁内部的模式检查与切换实现。"""

        with self._state_lock:
            self._walk_ready = False
            self._desired = Velocity.zero()
            if safety_epoch is None:
                safety_epoch = self._safety_epoch
        self._emit("walk_not_ready", "正在确认 Go2 行走模式……")
        if getattr(self, "_connection_mode", None) == "AP":
            self._emit("diagnostic", "[AP] 开始准备行走：先发送 StopMove，再查询运动模式")
        if not await self._send_stop(retries=3):
            return

        # 先查询，不盲目切模式；normal 和 mcf 都可直接使用基础运动 API。
        response = await self._send_request(
            RTC_TOPIC["MOTION_SWITCHER"],
            motion_mode_options(),
        )
        mode = parse_motion_mode(response)
        if getattr(self, "_connection_mode", None) == "AP":
            self._emit("diagnostic", f"[AP] 机器人报告当前运动模式：{mode}")
        # 网络响应回来时再次检查版本，防止期间的急停被旧任务覆盖。
        if not self._is_safety_epoch_current(safety_epoch):
            self._emit("walk_not_ready", "准备行走期间触发了停止；请重新准备行走模式。")
            return
        if mode not in WALK_READY_MODES:
            if getattr(self, "_connection_mode", None) == "AP":
                self._emit("diagnostic", f"[AP] 当前模式 {mode} 不允许基础行走，尝试切换 normal")
            self._emit("status", f"当前模式为 {mode}，正在切换到 normal；机器人可能站起。")
            await self._send_request(
                RTC_TOPIC["MOTION_SWITCHER"],
                normal_motion_mode_options(),
            )
            await asyncio.sleep(5.0)
            response = await self._send_request(
                RTC_TOPIC["MOTION_SWITCHER"],
                motion_mode_options(),
            )
            mode = parse_motion_mode(response)
            if not self._is_safety_epoch_current(safety_epoch):
                self._emit("walk_not_ready", "准备行走期间触发了停止；请重新准备行走模式。")
                return
        if mode not in WALK_READY_MODES:
            raise RuntimeError(f"行走模式切换失败，当前模式仍为 {mode}")

        with self._state_lock:
            if safety_epoch != self._safety_epoch:
                cancelled = True
            else:
                cancelled = False
                self._walk_ready = True
                self._desired = Velocity.zero()
                self._last_update = time.monotonic()
        if cancelled:
            self._emit("walk_not_ready", "准备行走期间触发了停止；请重新准备行走模式。")
            return
        self._emit("walk_ready", f"{mode} 行走模式已就绪；现在可以武装低速控制。")
        if getattr(self, "_connection_mode", None) == "AP":
            self._emit("diagnostic", f"[AP] 行走模式准备完成：{mode}")

    def _is_safety_epoch_current(self, safety_epoch: int) -> bool:
        """判断异步任务是否仍属于最新一次安全操作。"""

        with self._state_lock:
            return safety_epoch == self._safety_epoch

    async def _stand_down(self) -> None:
        if not self.connected:
            self._emit("error", "尚未连接，无法执行卧趴。")
            return
        action_lock = self._action_lock
        if action_lock is None:
            raise RuntimeError("WebRTC 尚未连接")
        async with action_lock:
            await self._stand_down_locked()

    async def _sport_action(self, action: str) -> None:
        name = sport_action_name(action)
        if not self.connected:
            self._emit("error", f"尚未连接，无法执行 {name}。")
            return
        action_lock = self._action_lock
        if action_lock is None:
            raise RuntimeError("WebRTC 尚未连接")
        try:
            async with action_lock:
                await self._sport_action_locked(action)
        except asyncio.TimeoutError:
            if action == "heart":
                # Heart 在部分固件上会完成实体动作却不及时回复 RPC。命令已成功
                # 发布时，确认超时不能被当成明确拒绝，否则会错误取消后续流程。
                self._emit(
                    "action_warning",
                    f"Heart 已发送，但在 {self.ACTION_REQUEST_TIMEOUT:.1f} 秒内未返回确认；"
                    "比心流程将按设定时长继续。",
                )
                return
            self._emit(
                "action_error",
                f"{name} 在 {self.ACTION_REQUEST_TIMEOUT:.1f} 秒内未返回确认；"
                "连接仍保留，请确认机器人姿态后再试。",
            )
        except SportActionResponseError as exc:
            self._emit("action_error", str(exc))

    async def _sport_action_locked(self, action: str) -> None:
        """在动作锁中执行“先停止，再发送高层动作”的固定安全顺序。"""
        name = sport_action_name(action)
        with self._state_lock:
            self._walk_ready = False
            self._desired = Velocity.zero()
        self._emit("walk_not_ready", f"正在停止移动并执行 {name}……")
        if not await self._send_stop(retries=3):
            return
        # 留出短暂停顿，让 StopMove 先被机器人处理，再发送姿态动作。
        await asyncio.sleep(0.15)

        # BalanceStand(1002) 的含义是“进入平衡控制状态”，不是“从任意姿态站起”。
        # 因此单独发送时，卧趴或坐姿下可能看起来完全没反应。先执行官方
        # StandUp(1004)，等待机械动作稳定，再进入 BalanceStand，按钮语义才完整。
        action_sequence = (
            ("stand_up", "balance_stand")
            if action == "balance_stand"
            else (action,)
        )
        for index, staged_action in enumerate(action_sequence):
            if index:
                await asyncio.sleep(2.0)
            staged_name = sport_action_name(staged_action)
            response = await self._send_request(
                RTC_TOPIC["SPORT_MOD"],
                sport_action_options(staged_action),
                timeout=self.ACTION_REQUEST_TIMEOUT,
            )
            require_sport_action_success(response, staged_name)

        if action == "recovery_stand":
            result_text = (
                "已发送 RecoveryStand；该命令只会在机器狗跌倒时产生明显恢复动作。"
            )
        elif action == "balance_stand":
            result_text = "已执行 StandUp → BalanceStand；机器狗已进入平衡站立状态。"
        else:
            result_text = f"已执行 {name}；如需移动，请重新执行一键准备并武装。"
        self._emit(
            "walk_not_ready",
            result_text,
        )

    async def _stand_down_locked(self) -> None:
        with self._state_lock:
            self._walk_ready = False
            self._desired = Velocity.zero()
        self._emit("walk_not_ready", "正在停止移动并执行卧趴……")
        if not await self._send_stop(retries=3):
            return
        await asyncio.sleep(0.15)
        await self._send_request(RTC_TOPIC["SPORT_MOD"], stand_down_options())
        self._emit("walk_not_ready", "已执行 StandDown 卧趴；再次行走前需重新准备行走模式。")

    async def _stand_up(self) -> None:
        if not self.connected:
            self._emit("error", "尚未连接，无法执行站起。")
            return
        action_lock = self._action_lock
        if action_lock is None:
            raise RuntimeError("WebRTC 尚未连接")
        async with action_lock:
            await self._stand_up_locked()

    async def _stand_up_locked(self) -> None:
        with self._state_lock:
            self._walk_ready = False
            self._desired = Velocity.zero()
        self._emit("walk_not_ready", "正在停止移动并执行站起……")
        if not await self._send_stop(retries=3):
            return
        await asyncio.sleep(0.15)
        await self._send_request(RTC_TOPIC["SPORT_MOD"], stand_up_options())
        self._emit("walk_not_ready", "已执行 StandUp 站起；移动前仍需一键准备并武装。")

    async def _start_video(self) -> None:
        """注册一次视频回调并打开 Go2 视频通道。"""

        conn = self._conn
        if conn is None or not self.connected:
            self._emit("error", "尚未连接，无法开启摄像头。")
            return
        if self._on_video_frame is None:
            self._emit("error", "摄像头预览回调未配置。")
            return
        self._video_enabled = True
        if not self._video_callback_registered:
            conn.video.add_track_callback(self._receive_video)
            self._video_callback_registered = True
        conn.video.switchVideoChannel(True)
        self._emit("camera", "摄像头已请求开启，正在等待画面……")

    async def _stop_video(self) -> None:
        was_enabled = self._video_enabled
        self._video_enabled = False
        conn = self._conn
        if conn is not None and was_enabled:
            conn.video.switchVideoChannel(False)
            self._emit("camera", "摄像头预览已关闭。")

    async def _receive_video(self, track: Any) -> None:
        """持续接收视频帧，统一缩放为 480×270 RGB 后交给 GUI。"""

        try:
            while self.connected:
                if not self._video_enabled:
                    await asyncio.sleep(0.05)
                    continue
                frame = await track.recv()
                # reformat 同时完成尺寸调整和像素格式转换。
                scaled = frame.reformat(width=480, height=270, format="rgb24")
                rgb = scaled.to_ndarray().tobytes()
                callback = self._on_video_frame
                if callback is not None:
                    callback(VideoFrameData(480, 270, rgb))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._video_enabled and self.connected:
                self._video_enabled = False
                self._emit("camera", f"摄像头视频流中断：{type(exc).__name__}: {exc}")

    async def _send_stop(self, retries: int) -> bool:
        """发送 StopMove；失败时短暂等待并重试，最终失败才报告一次。"""

        if self._conn is None or self._command_lock is None:
            return False
        for attempt in range(retries):
            try:
                await self._send_request(RTC_TOPIC["SPORT_MOD"], stop_options())
                self._stop_failure_reported = False
                return True
            except Exception:
                if attempt + 1 == retries:
                    if not self._stop_failure_reported:
                        self._stop_failure_reported = True
                        self._emit(
                            "error",
                            "停止命令未获确认；请立即使用实体遥控器急停。",
                        )
                else:
                    await asyncio.sleep(0.05)
        return False

    async def _send_request(
        self,
        topic: str,
        options: dict[str, object],
        timeout: float | None = None,
    ) -> object:
        """串行发送一个需要回复的请求，并限制单次等待时间。"""

        conn = self._conn
        lock = self._command_lock
        if conn is None or lock is None:
            raise RuntimeError("WebRTC 尚未连接")
        async with lock:
            return await asyncio.wait_for(
                conn.datachannel.pub_sub.publish_request_new(
                    topic,
                    options,
                ),
                timeout=self.REQUEST_TIMEOUT if timeout is None else timeout,
            )

    def _emit(self, kind: EventKind, message: str) -> None:
        """安全调用 GUI 回调；界面关闭后的回调异常不会击穿网络线程。"""

        try:
            self._on_event(kind, message)
        except Exception:
            pass
