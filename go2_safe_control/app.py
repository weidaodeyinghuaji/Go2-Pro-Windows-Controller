"""Tkinter 图形界面和用户交互编排。

这是项目最大的文件，但它不直接拼 WebRTC 请求：
``safety.py`` 负责安全计算，``workflow.py`` 负责流程，``session.py`` 负责通信。
初学时先看小模块，最后再按“连接→武装→移动→停止”的路径阅读本文件。

本文件的阅读地图：
- 顶部常量与小函数：输入数据清洗和尺寸计算；
- OneClickActivation / ClickMotionLatch：两个很小的界面状态对象；
- SafeControlApp.__init__：创建整套界面需要的数据；
- _build_ui：只负责摆放控件，代码长但业务逻辑少，可最后阅读；
- _connect / _prepare_and_arm / _emergency_stop：人工控制主流程；
- _start_workflow / _workflow_velocity：自动流程主流程；
- _tick：每 50ms 执行一次的 GUI 心跳，是界面与通信层的汇合点。
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
import threading
import time
# Tkinter 是 Python 自带 GUI 库；tk 是常用短别名。
import tkinter as tk
import unicodedata
from dataclasses import asdict
from pathlib import Path
# filedialog=文件选择框，messagebox=提示框，ttk=较现代的控件外观。
from tkinter import filedialog, messagebox, ttk

from .person_detection import (
    PERSON_BOX_GREEN,
    PERSON_BOX_RED,
    PersonDetectionWorker,
    default_model_path,
    draw_person_boxes,
)
from .safety import ControlLimits, DeadmanKeys, SafetyPolicy, Velocity
from .session import ConnectionSettings, EventKind, RobotSession, VideoFrameData
from .windows_input import WindowsControlInputGuard
from .workflow import (
    ACTION_LABELS,
    SPORT_ACTIONS,
    MOTION_ACTIONS,
    WorkflowRunner,
    WorkflowStep,
    default_workflow,
    parse_workflow,
    velocity_for_step,
    workflow_from_json,
    workflow_to_json,
)


# 以下大写名称是模块常量：运行中不会被随意修改的统一配置。
CONTROL_KEYS = {"w", "a", "s", "d", "q", "e"}
APP_TITLE = "宇树Go2机器狗远程二次开发与调试控制台"
HEADER_FONT_SIZE = 20
HEADER_HEIGHT = 68
CAMERA_PANEL_HEIGHT = 390
# 通信层输出正好是 480×270。这里必须允许 480；若写成 470，整数缩放会把
# 只超出 10px 的画面直接缩成 240×135，看起来就像没有铺满预览框。
CAMERA_PREVIEW_MAX_WIDTH = 480
CAMERA_PREVIEW_MAX_HEIGHT = 270
LINEAR_SPEED_RANGE = (0.05, 0.80)
LATERAL_SPEED_RANGE = (0.05, 0.50)
YAW_SPEED_RANGE = (0.10, 1.00)
WINDOW_MAX_WIDTH = 1080
WINDOW_MAX_HEIGHT = 720
WINDOW_MARGIN_X = 80
WINDOW_MARGIN_Y = 100
# 保持 Windows 原生浅色界面；只保留安全相关按钮原有的红色和橙色语义。
UI_BG = "SystemButtonFace"
UI_DANGER = "#C00000"
UI_WARNING = "#F0A000"
UI_TEXT = "SystemWindowText"
UI_MUTED = "#555555"
UI_FONT = "Microsoft YaHei UI"
UI_MONO_FONT = "Consolas"
# Windows 物理键码映射。即使中文输入法把 keysym 变成中文，也能认出实体 W 键。
PHYSICAL_KEYCODES = {
    32: "space",
    65: "a",
    68: "d",
    69: "e",
    81: "q",
    83: "s",
    87: "w",
}


def window_size_for_screen(screen_width: int, screen_height: int) -> tuple[int, int]:
    """根据屏幕尺寸计算窗口大小；放不下的内容由滚动区域访问。"""

    # min 限制最大尺寸，max 保证窗口不会小到完全无法操作。
    width = min(WINDOW_MAX_WIDTH, max(640, screen_width - WINDOW_MARGIN_X))
    height = min(WINDOW_MAX_HEIGHT, max(520, screen_height - WINDOW_MARGIN_Y))
    return width, height


def camera_subsample_factor(width: int, height: int) -> int:
    """计算 Tkinter 预览需要缩小的整数倍数。"""

    if width <= 0 or height <= 0:
        raise ValueError("摄像头画面尺寸必须为正数。")
    # ceil 向上取整，保证缩放后的宽和高都不会超过预览区。
    return max(
        1,
        math.ceil(width / CAMERA_PREVIEW_MAX_WIDTH),
        math.ceil(height / CAMERA_PREVIEW_MAX_HEIGHT),
    )


def control_key_from_event(keysym: str, keycode: int) -> str | None:
    """优先按物理键码识别控制键，识别失败再使用 Tkinter 键名。"""

    physical = PHYSICAL_KEYCODES.get(keycode)
    if physical is not None:
        return physical
    key = keysym.lower()
    return key if key in CONTROL_KEYS or key == "space" else None


def _compact_aes_key(value: str) -> str:
    """清除复制 AES key 时常见的标签、引号、空白和不可见字符。"""

    # Unicode 类别 Cf 包含零宽字符和 BOM，它们肉眼看不见却会破坏长度校验。
    text = "".join(ch for ch in value if unicodedata.category(ch) != "Cf").strip()
    text = text.strip("'\"`‘’“”")
    text = re.sub(
        r"^(?:(?:aes(?:-128)?\s*)?key|密钥)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.strip().strip("'\"`‘’“”")
    if text.lower().startswith("0x"):
        text = text[2:]
    return re.sub(r"[\s-]+", "", text)


def normalize_aes_key(value: str) -> str:
    """把 AES key 规范为 32 位小写 hex；无效时给出具体原因。"""

    compact = _compact_aes_key(value)
    if not compact:
        return ""
    if not re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        all_hex = bool(re.fullmatch(r"[0-9a-fA-F]+", compact))
        if len(compact) == 33 and all_hex:
            detail = (
                "。这 33 个字符虽然都是十六进制，但奇数位无法表示 16 字节 AES key；"
                "请重新复制设备结果中的 dev.key，程序不会擅自截掉任意一位"
            )
        elif all_hex:
            detail = ""
        else:
            invalid_count = sum(ch not in "0123456789abcdefABCDEF" for ch in compact)
            detail = f"，其中有 {invalid_count} 个非十六进制字符"
        raise ValueError(
            f"AES key 整理后为 {len(compact)} 个字符，需要 32 个十六进制字符{detail}。\n"
            "请只复制获取结果中“Key:”后面的密钥；不要粘贴账号密码或 Wi-Fi 密码。"
        )
    return compact.lower()


def parse_control_limits(linear: str, lateral: str, yaw: str) -> ControlLimits:
    """解析三个速度输入框，并限制在控制器允许的安全范围内。"""

    def parse(label: str, value: str, allowed: tuple[float, float]) -> float:
        """内部小函数：三个输入框共用同一套数字和范围校验。"""

        try:
            parsed = float(value.strip())
        except ValueError as exc:
            raise ValueError(f"{label}必须是数字。") from exc
        minimum, maximum = allowed
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            raise ValueError(
                f"{label}必须在 {minimum:.2f} 到 {maximum:.2f} 之间。"
            )
        return parsed

    return ControlLimits(
        linear=parse("前后速度", linear, LINEAR_SPEED_RANGE),
        lateral=parse("横移速度", lateral, LATERAL_SPEED_RANGE),
        yaw=parse("转向速度", yaw, YAW_SPEED_RANGE),
    )


class OneClickActivation:
    """记录“一键准备”是否仍在等待后台返回 walk_ready。"""

    def __init__(self) -> None:
        self.pending = False

    def begin(self) -> None:
        self.pending = True

    def cancel(self) -> None:
        self.pending = False

    def consume_walk_ready(self) -> bool:
        """若正在等待则消费这次成功事件；同一事件只能消费一次。"""

        if not self.pending:
            return False
        self.pending = False
        return True


class ClickMotionLatch:
    """记录鼠标点击后持续生效的一个方向；点击停止时清空。"""

    def __init__(self) -> None:
        self._key: str | None = None

    def start(self, key: str) -> None:
        if key not in CONTROL_KEYS:
            raise ValueError(f"未知运动方向：{key}")
        self._key = key

    def stop(self) -> None:
        self._key = None

    def active_keys(self) -> set[str]:
        """用与键盘相同的集合格式返回当前锁存方向。"""

        return {self._key} if self._key is not None else set()


class SafeControlApp:
    """控制台主对象：创建控件、响应操作，并协调安全层和通信层。"""

    def __init__(self, root: tk.Tk) -> None:
        # root 是整个 Tkinter 应用的根窗口，所有控件都直接或间接属于它。
        self.root = root
        self.root.title(APP_TITLE)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width, window_height = window_size_for_screen(
            screen_width,
            screen_height,
        )
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.root.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.root.minsize(min(860, window_width), min(540, window_height))
        self._configure_styles()

        # ---------- 纯逻辑状态：它们不直接画界面，也不直接连机器人 ----------
        self.policy = SafetyPolicy(ControlLimits())
        self.activation = OneClickActivation()
        self._modal_open = False
        self.input_guard = WindowsControlInputGuard()
        self.deadman_keys = DeadmanKeys(keyboard_timeout=0.45)
        self.click_motion = ClickMotionLatch()
        self.workflow_runner = WorkflowRunner()
        self.workflow_steps = default_workflow()
        self._workflow_preparing = False
        self._workflow_resume_after = 0.0
        self._animation_frame = 0
        self._last_animation_at = 0.0
        self._motion_buttons: dict[str, ttk.Button] = {}
        self._sport_action_buttons: dict[str, ttk.Button] = {}
        self._workflow_edit_buttons: list[ttk.Button] = []
        self._workflow_file_buttons: list[ttk.Button] = []
        self._speed_inputs: list[ttk.Spinbox] = []
        self._connection_details_visible = True
        self._camera_requested = False
        self._activity_busy = False
        self._last_control_state_signature: tuple[object, ...] | None = None
        self.last_velocity = Velocity.zero()
        # 视频帧来自后台线程，Tkinter 只能在主线程显示，因此用锁交接“最新一帧”。
        self._video_lock = threading.Lock()
        self._latest_video_frame: VideoFrameData | None = None
        self._camera_photo: tk.PhotoImage | None = None
        # 人员识别在独立线程中运行；绿色是首次启动，手动刷新成功后改用红框。
        self._person_box_color = PERSON_BOX_GREEN
        self._person_detection_enabled = False
        self._person_detection = PersonDetectionWorker(
            default_model_path(),
            status_callback=self._queue_person_detection_event,
        )
        # 错误同时显示在界面并追加到用户目录，方便没有开发环境时排查问题。
        local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        self._error_log_path = local_app_data / "Go2 Controller" / "logs" / "error.log"
        self._error_log_entries: list[str] = []
        self._last_error_signature: tuple[str, str] | None = None
        self._last_error_at = 0.0
        # 把两个回调交给通信层：有状态消息或视频帧时，通信层会调用它们。
        self.session = RobotSession(self._queue_session_event, self._queue_video_frame)

        # ---------- Tkinter 变量：控件显示内容变化时，变量会自动通知控件 ----------
        self.mode_var = tk.StringVar(value="STA")
        self.ip_var = tk.StringVar(value="192.168.1.124")
        self.aes_var = tk.StringVar()
        self.aes_hint_var = tk.StringVar(value="可留空；粘贴后会显示整理后的字符数")
        self.hardware_ok_var = tk.BooleanVar(value=False)
        self.connection_var = tk.StringVar(value="未连接")
        self.arm_var = tk.StringVar(value="未武装：不会发送移动命令")
        self.walk_mode_var = tk.StringVar(value="行走模式未准备")
        self.camera_var = tk.StringVar(value="摄像头未开启")
        self.error_summary_var = tk.StringVar(value="暂无错误；按钮校验和通信错误会显示在这里")
        self.workflow_status_var = tk.StringVar(value="流程未启动；可编辑下方步骤")
        self.header_activity_var = tk.StringVar(value="未连接 · 本地界面就绪")
        self.workflow_action_var = tk.StringVar(value="前进")
        self.workflow_speed_var = tk.StringVar(value="0.30")
        self.workflow_duration_var = tk.StringVar(value="2.00")
        self.velocity_var = tk.StringVar(value="x=0.00  y=0.00  z=0.00")
        self.linear_speed_var = tk.StringVar(value=f"{self.policy.limits.linear:.2f}")
        self.lateral_speed_var = tk.StringVar(value=f"{self.policy.limits.lateral:.2f}")
        self.yaw_speed_var = tk.StringVar(value=f"{self.policy.limits.yaw:.2f}")

        # 创建控件、监听 AES 输入变化、绑定键盘/关闭事件，然后启动 50ms 心跳。
        self._build_ui()
        self.aes_var.trace_add("write", self._update_aes_hint)
        self._bind_events()
        self._tick()

    def _configure_styles(self) -> None:
        """保留 Windows 原生浅色主题，只统一字体、间距和安全按钮。"""

        self.root.configure(background=UI_BG)
        style = ttk.Style(self.root)
        # 不切换 clam/第三方主题，让每台 Windows 使用原本熟悉的系统控件颜色。
        style.configure(".", font=(UI_FONT, 10))
        style.configure("App.TFrame")
        style.configure("Surface.TFrame")
        style.configure("Raised.TFrame")
        style.configure("Panel.TLabelframe")
        style.configure(
            "Panel.TLabelframe.Label",
            font=(UI_FONT, 11, "bold"),
            padding=(4, 0),
        )
        style.configure("Muted.TLabel", foreground=UI_MUTED)
        style.configure(
            "HeaderTitle.TLabel",
            font=(UI_FONT, HEADER_FONT_SIZE, "bold"),
        )
        style.configure(
            "HeaderMeta.TLabel",
            font=(UI_MONO_FONT, 10, "bold"),
        )
        style.configure(
            "Telemetry.TLabel",
            font=(UI_MONO_FONT, 12, "bold"),
            padding=(10, 7),
            relief="sunken",
        )
        style.configure("Status.TLabel", padding=(10, 7), relief="groove")
        style.configure("Camera.TLabel", padding=8, relief="sunken", anchor="center")
        style.configure("DangerText.TLabel", foreground=UI_DANGER)
        style.configure("Path.TLabel", foreground=UI_MUTED)
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=5)
        style.configure("TSpinbox", padding=4)
        style.configure("TCheckbutton", padding=5)

        for name in ("Primary.TButton", "Secondary.TButton", "Motion.TButton"):
            style.configure(name, padding=(13, 8), font=(UI_FONT, 10, "bold"))
        style.configure(
            "Warning.TButton",
            background=UI_WARNING,
            foreground="black",
            padding=(13, 8),
            font=(UI_FONT, 10, "bold"),
        )
        style.map("Warning.TButton", background=[("active", "#FFB52E")])
        style.configure(
            "Danger.TButton",
            background=UI_DANGER,
            foreground="white",
            padding=(13, 8),
            font=(UI_FONT, 10, "bold"),
        )
        style.map("Danger.TButton", background=[("active", "#D82727")])
        style.configure("Treeview", rowheight=28)
        style.configure(
            "Treeview.Heading",
            font=(UI_FONT, 10, "bold"),
            padding=(6, 7),
        )

    def _build_ui(self) -> None:
        """创建并排版全部 Tkinter 控件；这里主要是界面结构，不发送机器人命令。"""

        """创建窗口中的全部区域和控件；这里只定义布局，不连接机器人。"""
        page = ttk.Frame(self.root, style="App.TFrame")
        page.pack(fill="both", expand=True)
        self.page_scrollbar = ttk.Scrollbar(page, orient="vertical")
        self.page_scrollbar.pack(side="right", fill="y")
        self.page_canvas = tk.Canvas(
            page,
            highlightthickness=0,
            borderwidth=0,
            background=UI_BG,
            yscrollcommand=self.page_scrollbar.set,
        )
        self.page_canvas.pack(side="left", fill="both", expand=True)
        self.page_scrollbar.configure(command=self.page_canvas.yview)

        container = ttk.Frame(self.page_canvas, padding=10, style="App.TFrame")
        self._page_window = self.page_canvas.create_window(
            (0, 0),
            window=container,
            anchor="nw",
        )
        container.bind("<Configure>", self._update_page_scrollregion)
        self.page_canvas.bind("<Configure>", self._resize_page_width)
        self.root.bind("<MouseWheel>", self._scroll_page, add="+")

        header = ttk.Frame(container, height=HEADER_HEIGHT, padding=(8, 6), style="Surface.TFrame")
        header.pack(fill="x", pady=(0, 8))
        header.pack_propagate(False)
        title_block = ttk.Frame(header, style="Surface.TFrame")
        title_block.pack(side="left", fill="both", expand=True)
        ttk.Label(
            title_block,
            text=APP_TITLE,
            style="HeaderTitle.TLabel",
            anchor="w",
        ).pack(anchor="w")
        ttk.Label(
            title_block,
            textvariable=self.header_activity_var,
            style="HeaderMeta.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(3, 0))
        self.emergency_button = tk.Button(
            header,
            text="紧急停止（空格）",
            command=self._emergency_stop,
            background=UI_DANGER,
            foreground="white",
            activebackground="#D82727",
            activeforeground="white",
            font=(UI_FONT, 10, "bold"),
            relief="raised",
            padx=18,
            pady=6,
        )
        self.emergency_button.pack(side="right", padx=(12, 0))
        self.activity_progress = ttk.Progressbar(
            header,
            mode="indeterminate",
            length=84,
        )
        self.activity_progress.pack(side="right")

        connection_slot = ttk.Frame(container, style="App.TFrame")
        connection_slot.pack(fill="x", pady=(0, 8))
        workspace = ttk.Frame(container, style="App.TFrame")
        workspace.pack(fill="both", expand=True)
        self.main_tabs = ttk.Notebook(workspace, height=500)
        self.main_tabs.pack(fill="both", expand=True)
        safety = ttk.Frame(self.main_tabs, padding=12)
        controls = ttk.Frame(self.main_tabs, padding=12)
        camera = ttk.Frame(self.main_tabs, padding=12)
        workflow = ttk.Frame(self.main_tabs, padding=12)
        error_log = ttk.Frame(self.main_tabs, padding=12)
        self.main_tabs.add(safety, text="安全与姿态")
        self.main_tabs.add(controls, text="运动控制")
        self.main_tabs.add(camera, text="摄像头与识别")
        self.main_tabs.add(workflow, text="自动流程")
        self.main_tabs.add(error_log, text="错误日志")
        self.main_tabs.bind("<<NotebookTabChanged>>", self._resize_active_tab)

        connection = ttk.LabelFrame(
            connection_slot,
            text="连接与认证",
            padding=10,
            style="Panel.TLabelframe",
        )
        connection.pack(fill="x")
        connection.columnconfigure(0, weight=1)
        summary = ttk.Frame(connection)
        summary.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            summary,
            textvariable=self.connection_var,
            style="Status.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.connection_toggle_button = ttk.Button(
            summary,
            text="收起连接设置",
            command=self._toggle_connection_details,
            style="Secondary.TButton",
        )
        self.connection_toggle_button.pack(side="right", padx=(8, 0))
        self.disconnect_button = ttk.Button(
            summary,
            text="断开并停止",
            command=self._disconnect,
            style="Secondary.TButton",
        )
        self.disconnect_button.pack(side="right", padx=(8, 0))
        self.connect_button = ttk.Button(
            summary,
            text="建立连接",
            command=self._connect,
            style="Primary.TButton",
        )
        self.connect_button.pack(side="right", padx=(8, 0))

        self.connection_details = ttk.Frame(connection, padding=(0, 10, 0, 0))
        self.connection_details.grid(row=1, column=0, sticky="ew")
        self.connection_details.columnconfigure(3, weight=1)
        self.connection_details.columnconfigure(5, weight=1)
        ttk.Label(self.connection_details, text="模式").grid(row=0, column=0, sticky="w")
        self.mode_box = ttk.Combobox(
            self.connection_details,
            textvariable=self.mode_var,
            values=("STA", "AP"),
            state="readonly",
            width=8,
        )
        self.mode_box.grid(row=0, column=1, sticky="w", padx=(8, 18))
        self.mode_box.bind("<<ComboboxSelected>>", self._on_mode_changed)

        ttk.Label(self.connection_details, text="Go2 IP（STA）").grid(row=0, column=2, sticky="w")
        self.ip_entry = ttk.Entry(self.connection_details, textvariable=self.ip_var, width=18)
        self.ip_entry.grid(row=0, column=3, sticky="ew", padx=(8, 18))

        ttk.Label(self.connection_details, text="AES key").grid(row=0, column=4, sticky="w")
        self.aes_entry = ttk.Entry(self.connection_details, textvariable=self.aes_var, show="•")
        self.aes_entry.grid(row=0, column=5, sticky="ew", padx=(8, 0))
        ttk.Label(
            self.connection_details,
            textvariable=self.aes_hint_var,
            style="Muted.TLabel",
        ).grid(row=1, column=5, sticky="w", padx=(8, 0), pady=(3, 0))

        ttk.Checkbutton(
            safety,
            variable=self.hardware_ok_var,
            text=(
                "四周已清空，实体遥控器在手，Go2 已以低速测试姿态站稳。"
            ),
        ).pack(anchor="w")
        safety_actions = ttk.Frame(safety)
        safety_actions.pack(fill="x", pady=(10, 0))
        self.prepare_button = ttk.Button(
            safety_actions,
            text="一键准备并武装",
            command=self._prepare_and_arm,
            style="Primary.TButton",
        )
        self.prepare_button.pack(side="left")
        posture_actions = ttk.LabelFrame(
            safety,
            text="姿态控制  /  执行后需重新武装",
            padding=8,
            style="Panel.TLabelframe",
        )
        posture_actions.pack(fill="x", pady=(8, 0))
        for index, (action, label) in enumerate(
            (
                ("stand_down", "安全卧趴"),
                ("stand_up", "一键站起"),
                ("balance_stand", "平衡站立"),
                ("recovery_stand", "跌倒恢复"),
                ("sit", "坐下"),
                ("rise_sit", "坐起"),
            )
        ):
            button = ttk.Button(
                posture_actions,
                text=label,
                command=lambda selected=action: self._run_sport_action(selected),
                style="Secondary.TButton",
            )
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=3, pady=3)
            self._sport_action_buttons[action] = button
        for column in range(3):
            posture_actions.columnconfigure(column, weight=1)

        interaction_actions = ttk.LabelFrame(
            safety,
            text="互动动作  /  执行后需重新武装",
            padding=8,
            style="Panel.TLabelframe",
        )
        interaction_actions.pack(fill="x", pady=(8, 0))
        for column, (action, label) in enumerate(
            (("hello", "打招呼"), ("stretch", "伸展"), ("heart", "比心"))
        ):
            button = ttk.Button(
                interaction_actions,
                text=label,
                command=lambda selected=action: self._run_sport_action(selected),
                style="Secondary.TButton",
            )
            button.grid(row=0, column=column, sticky="ew", padx=3, pady=3)
            interaction_actions.columnconfigure(column, weight=1)
            self._sport_action_buttons[action] = button
        safety_status = ttk.Frame(safety, style="Raised.TFrame")
        safety_status.pack(fill="x", pady=(10, 0))
        ttk.Label(
            safety_status,
            textvariable=self.walk_mode_var,
            style="Status.TLabel",
        ).pack(side="left", fill="x", expand=True)
        ttk.Label(
            safety_status,
            textvariable=self.arm_var,
            style="Status.TLabel",
        ).pack(side="right", fill="x", expand=True)

        ttk.Label(
            controls,
            text=(
                "点击方向按钮后会持续移动，直到点击“停止移动”、按空格或窗口失焦；"
                "键盘 W/S/A/D/Q/E 仍是按住移动、松键停止。"
            ),
            wraplength=680,
            style="Muted.TLabel",
        ).pack(anchor="w")

        pad = ttk.Frame(controls, padding=(0, 16))
        pad.pack()
        self._motion_button(pad, "Q\n左转", "q", 0, 0)
        self._motion_button(pad, "W\n前进", "w", 0, 1)
        self._motion_button(pad, "E\n右转", "e", 0, 2)
        self._motion_button(pad, "A\n左移", "a", 1, 0)
        self._motion_button(pad, "S\n后退", "s", 1, 1)
        self._motion_button(pad, "D\n右移", "d", 1, 2)

        self.stop_motion_button = tk.Button(
            controls,
            text="停止移动（保持武装）",
            command=self._stop_motion,
            background=UI_WARNING,
            foreground="black",
            activebackground="#FFB52E",
            activeforeground="black",
            font=(UI_FONT, 10, "bold"),
            relief="raised",
            pady=7,
        )
        self.stop_motion_button.pack(fill="x", padx=80, pady=(4, 10))

        speed_frame = ttk.LabelFrame(
            controls,
            text="速度设置（应用时会先停止当前移动）",
            padding=8,
            style="Panel.TLabelframe",
        )
        speed_frame.pack(fill="x", padx=18, pady=(0, 4))
        speed_inputs = ttk.Frame(speed_frame)
        speed_inputs.pack()
        for column, (label, variable, allowed, increment) in enumerate(
            (
                ("前后 m/s", self.linear_speed_var, LINEAR_SPEED_RANGE, 0.05),
                ("横移 m/s", self.lateral_speed_var, LATERAL_SPEED_RANGE, 0.05),
                ("转向 rad/s", self.yaw_speed_var, YAW_SPEED_RANGE, 0.10),
            )
        ):
            ttk.Label(speed_inputs, text=label).grid(row=0, column=column * 2, padx=(5, 3))
            speed_input = ttk.Spinbox(
                speed_inputs,
                textvariable=variable,
                from_=allowed[0],
                to=allowed[1],
                increment=increment,
                width=6,
                format="%.2f",
            )
            speed_input.grid(row=0, column=column * 2 + 1, padx=(0, 9))
            self._speed_inputs.append(speed_input)
        self.apply_speed_button = ttk.Button(
            speed_inputs,
            text="应用速度",
            command=self._apply_speed_settings,
            style="Primary.TButton",
        )
        self.apply_speed_button.grid(row=0, column=6, padx=(4, 5))
        ttk.Label(
            controls,
            textvariable=self.velocity_var,
            style="Telemetry.TLabel",
        ).pack(pady=(8, 0))

        ttk.Label(
            camera,
            text="开启后显示 Go2 前置摄像头；不录像、不保存。",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(anchor="w")
        camera_preview = ttk.Frame(camera, height=CAMERA_PREVIEW_MAX_HEIGHT)
        camera_preview.pack(fill="x", pady=12)
        camera_preview.pack_propagate(False)
        self.camera_label = ttk.Label(
            camera_preview,
            text="等待开启摄像头",
            anchor="center",
            style="Camera.TLabel",
            width=60,
        )
        self.camera_label.pack(fill="both", expand=True, ipadx=4)
        camera_actions = ttk.Frame(camera)
        camera_actions.pack(fill="x")
        self.camera_button = ttk.Button(
            camera_actions,
            text="开启摄像头",
            command=self._toggle_camera,
            style="Primary.TButton",
        )
        self.camera_button.pack(side="left")
        self.camera_stop_button = ttk.Button(
            camera_actions,
            text="关闭摄像头",
            command=self._stop_camera,
            style="Secondary.TButton",
        )
        self.camera_stop_button.pack(side="left", padx=8)
        recognition_actions = ttk.Frame(camera)
        recognition_actions.pack(fill="x", pady=(8, 0))
        self.person_start_button = ttk.Button(
            recognition_actions,
            text="开启人员识别",
            command=self._start_person_detection,
            style="Secondary.TButton",
        )
        self.person_start_button.pack(side="left", padx=(0, 8))
        self.person_stop_button = ttk.Button(
            recognition_actions,
            text="关闭人员识别",
            command=self._stop_person_detection,
            style="Secondary.TButton",
        )
        self.person_stop_button.pack(side="left", padx=(0, 8))
        self.person_refresh_button = ttk.Button(
            recognition_actions,
            text="刷新识别",
            command=self._refresh_person_detection,
            style="Secondary.TButton",
        )
        self.person_refresh_button.pack(side="left")
        ttk.Label(
            camera,
            textvariable=self.camera_var,
            wraplength=480,
            style="Status.TLabel",
        ).pack(
            anchor="w", pady=(10, 0)
        )

        ttk.Label(
            workflow,
            text="按顺序执行；移动按时间控制，不代表精确里程。空格或红色急停随时中止。",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(anchor="w")

        tree_frame = ttk.Frame(workflow)
        tree_frame.pack(fill="both", expand=True, pady=(10, 8))
        self.workflow_tree = ttk.Treeview(
            tree_frame,
            columns=("action", "speed", "duration", "state"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        for column, heading, width, anchor in (
            ("action", "动作", 115, "w"),
            ("speed", "速度", 72, "center"),
            ("duration", "时长", 68, "center"),
            ("state", "状态", 90, "center"),
        ):
            self.workflow_tree.heading(column, text=heading)
            self.workflow_tree.column(column, width=width, anchor=anchor, stretch=True)
        workflow_scroll = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.workflow_tree.yview,
        )
        self.workflow_tree.configure(yscrollcommand=workflow_scroll.set)
        self.workflow_tree.pack(side="left", fill="both", expand=True)
        workflow_scroll.pack(side="right", fill="y")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._select_workflow_step)

        editor = ttk.LabelFrame(
            workflow,
            text="步骤编辑",
            padding=8,
            style="Panel.TLabelframe",
        )
        editor.pack(fill="x")
        # 编辑区采用两行自适应布局，避免窗口较窄或 Windows 缩放较大时，
        # “时长”和右侧操作按钮被挤出可视区域。
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        ttk.Label(editor, text="动作").grid(row=0, column=0, sticky="w")
        action_box = ttk.Combobox(
            editor,
            textvariable=self.workflow_action_var,
            values=tuple(ACTION_LABELS.values()),
            state="readonly",
            width=11,
        )
        action_box.grid(
            row=0,
            column=1,
            columnspan=3,
            padx=(8, 0),
            pady=(0, 8),
            sticky="ew",
        )
        action_box.bind("<<ComboboxSelected>>", self._workflow_action_changed)
        ttk.Label(editor, text="速度").grid(row=1, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.workflow_speed_var, width=7).grid(
            row=1, column=1, padx=(8, 14), sticky="ew"
        )
        ttk.Label(editor, text="时长（秒）").grid(row=1, column=2, sticky="w")
        self.workflow_duration_entry = ttk.Entry(
            editor,
            textvariable=self.workflow_duration_var,
            width=7,
        )
        self.workflow_duration_entry.grid(
            row=1, column=3, padx=(8, 0), sticky="ew"
        )
        editor_actions = ttk.Frame(editor)
        editor_actions.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        editor_buttons = (
            ("添加", self._add_workflow_step, "Primary.TButton"),
            ("更新", self._update_workflow_step, "Secondary.TButton"),
            ("删除", self._delete_workflow_step, "Secondary.TButton"),
            ("上移", lambda: self._move_workflow_step(-1), "Secondary.TButton"),
            ("下移", lambda: self._move_workflow_step(1), "Secondary.TButton"),
        )
        for column, (text, command, style_name) in enumerate(editor_buttons):
            editor_actions.columnconfigure(column, weight=1)
            button = ttk.Button(
                editor_actions,
                text=text,
                command=command,
                style=style_name,
            )
            button.grid(row=0, column=column, padx=2, sticky="ew")
            self._workflow_edit_buttons.append(button)

        file_actions = ttk.Frame(workflow)
        file_actions.pack(fill="x", pady=(8, 0))
        file_buttons = (
            ("保存流程", self._save_workflow),
            ("读取流程", self._load_workflow),
            ("恢复示例", self._restore_default_workflow),
        )
        for column, (text, command) in enumerate(file_buttons):
            file_actions.columnconfigure(column, weight=1)
            button = ttk.Button(
                file_actions,
                text=text,
                command=command,
                style="Secondary.TButton",
            )
            button.grid(row=0, column=column, padx=2, sticky="ew")
            self._workflow_file_buttons.append(button)

        run_actions = ttk.Frame(workflow)
        run_actions.pack(fill="x", pady=(10, 0))
        self.workflow_start_button = tk.Button(
            run_actions,
            text="一键启动流程",
            command=self._start_workflow,
            background="#1F6FB2",
            foreground="white",
            activebackground="#2F80C5",
            activeforeground="white",
            font=(UI_FONT, 10, "bold"),
            relief="raised",
            pady=8,
        )
        run_actions.columnconfigure(0, weight=1)
        run_actions.columnconfigure(1, weight=1)
        self.workflow_start_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")
        self.workflow_stop_button = tk.Button(
            run_actions,
            text="立即停止流程",
            command=self._stop_workflow,
            background=UI_DANGER,
            foreground="white",
            activebackground="#D82727",
            activeforeground="white",
            font=(UI_FONT, 10, "bold"),
            relief="raised",
            pady=8,
        )
        self.workflow_stop_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")
        ttk.Label(
            workflow,
            textvariable=self.workflow_status_var,
            wraplength=480,
            style="Status.TLabel",
        ).pack(anchor="w", pady=(8, 0))
        self._refresh_workflow_tree()

        ttk.Label(
            error_log,
            textvariable=self.error_summary_var,
            wraplength=480,
            style="DangerText.TLabel",
        ).pack(anchor="w")
        log_body = ttk.Frame(error_log)
        log_body.pack(fill="both", expand=True, pady=(8, 6))
        self.error_log_text = tk.Text(
            log_body,
            height=7,
            wrap="word",
            state="disabled",
            font=(UI_MONO_FONT, 9),
            background="SystemWindow",
            foreground="SystemWindowText",
            insertbackground="SystemWindowText",
            relief="sunken",
            borderwidth=1,
            highlightthickness=0,
        )
        log_scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.error_log_text.yview)
        self.error_log_text.configure(yscrollcommand=log_scroll.set)
        self.error_log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        log_actions = ttk.Frame(error_log)
        log_actions.pack(fill="x")
        ttk.Button(
            log_actions,
            text="打开日志目录",
            command=self._open_error_log_directory,
            style="Secondary.TButton",
        ).pack(side="left")
        ttk.Button(
            log_actions,
            text="清空窗口（文件保留）",
            command=self._clear_error_log_display,
            style="Secondary.TButton",
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            error_log,
            text=f"日志文件：{self._error_log_path}",
            wraplength=480,
            style="Path.TLabel",
        ).pack(anchor="w", pady=(6, 0))
        self._sync_control_states()

    # ==================== 页面滚动与尺寸适配 ====================

    def _update_header_activity(self, now: float) -> None:
        """显示稳定的全局状态；耗时操作只使用系统进度条反馈。"""

        if now - self._last_animation_at < 0.25:
            return
        self._last_animation_at = now
        self._animation_frame = (self._animation_frame + 1) % 4

        busy = False
        if self.workflow_runner.running or self._workflow_preparing:
            workflow_text = "自动流程运行中"
            busy = True
        else:
            workflow_text = "流程空闲"
        if "正在" in self.connection_var.get():
            connection_text = "连接处理中"
            busy = True
        elif self.session.connected:
            connection_text = "已连接"
        else:
            connection_text = "未连接"
        if "正在" in self.camera_var.get():
            camera_text = "摄像头处理中"
            busy = True
        elif self._person_detection_enabled:
            camera_text = "摄像头已开 · 人员识别已开"
        elif self._camera_requested:
            camera_text = "摄像头已开"
        else:
            camera_text = "摄像头关闭"
        arm_text = "已武装" if self.policy.armed else "未武装"
        self.header_activity_var.set(
            f"连接：{connection_text}  ·  控制：{arm_text}  ·  "
            f"{camera_text}  ·  {workflow_text}"
        )

        if busy and not self._activity_busy:
            self.activity_progress.start(12)
        elif not busy and self._activity_busy:
            self.activity_progress.stop()
            self.activity_progress.configure(value=0)
        self._activity_busy = busy

    def _sync_motion_button_feedback(self) -> None:
        """让被锁存的方向按钮保持按下外观，停止后立即恢复。"""

        active_keys = self.click_motion.active_keys()
        for key, button in self._motion_buttons.items():
            button.state(["pressed"] if key in active_keys else ["!pressed"])

    def _log_issue(self, title: str, message: str) -> None:
        """把错误写入界面和文件；不使用会打断操作的错误弹窗。"""

        self._append_log(title, message, update_error_summary=True)

    def _log_diagnostic(self, message: str) -> None:
        """把 AP 运行状态写入同一日志，但不把正常状态标成“最近错误”。"""

        self._append_log("AP 诊断", message, update_error_summary=False)

    def _append_log(
        self,
        title: str,
        message: str,
        *,
        update_error_summary: bool,
    ) -> None:
        """统一追加界面与磁盘日志，并对短时间内的重复消息限流。"""

        clean_message = " ".join(str(message).strip().splitlines()) or "未提供详细信息"
        signature = (title, clean_message)
        now = time.monotonic()
        # 摄像头或网络循环可能在很短时间内重复报告同一错误，避免刷满日志文件。
        if signature == self._last_error_signature and now - self._last_error_at < 2.0:
            return
        self._last_error_signature = signature
        self._last_error_at = now
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}: {clean_message}"
        self._error_log_entries.append(line)
        del self._error_log_entries[:-200]
        if update_error_summary:
            self.error_summary_var.set(f"最近错误：{title} — {clean_message[:160]}")

        try:
            self._error_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._error_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
        except OSError:
            # 文件不可写时仍保留界面日志，不能因记录错误再次打断控制流程。
            pass

        try:
            self.error_log_text.configure(state="normal")
            self.error_log_text.insert("end", line + "\n")
            self.error_log_text.see("end")
            self.error_log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_error_log_display(self) -> None:
        """只清空本次界面显示，磁盘日志继续保留。"""

        self._error_log_entries.clear()
        self.error_summary_var.set("界面日志已清空；历史记录仍保留在日志文件中")
        self.error_log_text.configure(state="normal")
        self.error_log_text.delete("1.0", "end")
        self.error_log_text.configure(state="disabled")

    def _open_error_log_directory(self) -> None:
        """在 Windows 资源管理器中打开日志所在目录。"""

        try:
            self._error_log_path.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self._error_log_path.parent))
        except (OSError, AttributeError) as exc:
            self._log_issue("打开日志目录失败", str(exc))

    def _set_connection_details_visible(self, visible: bool) -> None:
        """展开或折叠低频连接参数，连接状态和操作按钮始终可见。"""

        self._connection_details_visible = visible
        if visible:
            self.connection_details.grid()
            self.connection_toggle_button.configure(text="收起连接设置")
        else:
            self.connection_details.grid_remove()
            self.connection_toggle_button.configure(text="展开连接设置")
        self.root.after_idle(self._resize_active_tab)

    def _toggle_connection_details(self) -> None:
        self._set_connection_details_visible(not self._connection_details_visible)

    @staticmethod
    def _set_enabled(widget: tk.Misc, enabled: bool) -> None:
        """统一设置 ttk/tk 控件的可用状态。"""

        widget.configure(state="normal" if enabled else "disabled")

    def _sync_control_states(self) -> None:
        """根据连接、武装、摄像头和流程状态禁用当前不可执行的操作。"""

        connected = self.session.connected
        workflow_running = self.workflow_runner.running or self._workflow_preparing
        connection_busy = self.connection_var.get().startswith("正在")
        settings_locked = connected or connection_busy
        hardware_confirmed = self.hardware_ok_var.get()
        motion_ready = connected and hardware_confirmed and self.policy.armed and not workflow_running
        signature = (
            connected,
            workflow_running,
            connection_busy,
            hardware_confirmed,
            self.policy.armed,
            self.mode_var.get(),
            self._camera_requested,
            self._person_detection_enabled,
        )
        if signature == self._last_control_state_signature:
            return
        self._last_control_state_signature = signature

        self._set_enabled(self.connect_button, not settings_locked)
        self._set_enabled(self.disconnect_button, connected or connection_busy)
        self.mode_box.configure(state="disabled" if settings_locked else "readonly")
        self._set_enabled(
            self.ip_entry,
            not settings_locked and self.mode_var.get() == "STA",
        )
        self._set_enabled(self.aes_entry, not settings_locked)
        self._set_enabled(
            self.prepare_button,
            connected and hardware_confirmed and not workflow_running,
        )
        for button in self._sport_action_buttons.values():
            self._set_enabled(button, connected and hardware_confirmed and not workflow_running)
        for button in self._motion_buttons.values():
            self._set_enabled(button, motion_ready)
        self._set_enabled(self.stop_motion_button, connected)
        for speed_input in self._speed_inputs:
            self._set_enabled(speed_input, not workflow_running)
        self._set_enabled(self.apply_speed_button, not workflow_running)

        self._set_enabled(self.camera_button, connected and not self._camera_requested)
        self._set_enabled(self.camera_stop_button, connected and self._camera_requested)
        self._set_enabled(
            self.person_start_button,
            connected and self._camera_requested and not self._person_detection_enabled,
        )
        self._set_enabled(
            self.person_stop_button,
            self._person_detection_enabled,
        )
        self._set_enabled(
            self.person_refresh_button,
            connected and self._camera_requested,
        )

        workflow_can_start = connected and hardware_confirmed and not workflow_running
        self._set_enabled(self.workflow_start_button, workflow_can_start)
        self._set_enabled(self.workflow_stop_button, workflow_running)
        for button in (*self._workflow_edit_buttons, *self._workflow_file_buttons):
            self._set_enabled(button, not workflow_running)

    def _update_page_scrollregion(self, _event: object = None) -> None:
        """更新滚动边界；内容比窗口矮时固定贴顶，避免上方出现空白。"""

        bounds = self.page_canvas.bbox("all")
        if bounds is None:
            return
        viewport_width = max(1, self.page_canvas.winfo_width())
        viewport_height = max(1, self.page_canvas.winfo_height())
        content_width = max(0, bounds[2] - bounds[0])
        content_height = max(0, bounds[3] - bounds[1])
        scroll_width = max(content_width, viewport_width)
        scroll_height = max(content_height, viewport_height)
        self.page_canvas.configure(scrollregion=(0, 0, scroll_width, scroll_height))

        if content_height <= viewport_height:
            # Canvas 会保留最大化前的 yview；内容变矮后必须主动回到0，否则顶部留白。
            self.page_canvas.yview_moveto(0.0)
            return

        first, _last = self.page_canvas.yview()
        max_first = max(0.0, (content_height - viewport_height) / scroll_height)
        if first > max_first:
            self.page_canvas.yview_moveto(max_first)

    def _resize_page_width(self, event: tk.Event[tk.Misc]) -> None:
        """窗口变化时同步页面宽度和单一主工作区高度。"""

        self.page_canvas.itemconfigure(self._page_window, width=event.width)
        self.root.after_idle(self._resize_active_tab)

    def _resize_active_tab(self, _event: object = None) -> None:
        """按当前页真实高度调整工作区；超出窗口时交给整页滚动访问。"""

        selected = self.main_tabs.select()
        if not selected:
            return
        active_page = self.main_tabs.nametowidget(selected)
        viewport_height = max(1, self.page_canvas.winfo_height())
        connection_height = 148 if self._connection_details_visible else 68
        available_height = viewport_height - HEADER_HEIGHT - connection_height - 36
        required_height = active_page.winfo_reqheight() + 38
        tab_height = max(430, min(620, max(available_height, required_height)))
        self.main_tabs.configure(height=tab_height)
        self.root.after_idle(self._update_page_scrollregion)

    def _scroll_page(self, event: tk.Event[tk.Misc]) -> None:
        """把鼠标滚轮转换成整页滚动；表格等自带滚动控件除外。"""

        if event.delta == 0 or event.widget.winfo_class() in {
            "Treeview",
            "TCombobox",
            "TSpinbox",
        }:
            return
        bounds = self.page_canvas.bbox("all")
        if bounds is not None and bounds[3] - bounds[1] <= self.page_canvas.winfo_height():
            self.page_canvas.yview_moveto(0.0)
            return
        units = -1 if event.delta > 0 else 1
        self.page_canvas.yview_scroll(units * 3, "units")

    # ==================== 自动流程编辑、保存和执行 ====================

    def _workflow_row(self, step: WorkflowStep, state: str = "待执行") -> tuple[str, ...]:
        """把一个 WorkflowStep 转成表格需要的四列文字。"""

        speed = f"{step.speed:.2f}" if step.moving else "—"
        duration = f"{step.duration:.1f}s" if step.duration else "—"
        return step.label, speed, duration, state

    def _refresh_workflow_tree(self, *, completed: bool = False) -> None:
        """根据内存中的流程和运行状态，重新绘制右侧步骤表。"""

        if not hasattr(self, "workflow_tree"):
            return
        selected = self.workflow_tree.selection()
        selected_index = int(selected[0]) if selected else None
        self.workflow_tree.delete(*self.workflow_tree.get_children())
        current = self.workflow_runner.current_index if self.workflow_runner.running else -1
        # enumerate 同时得到步骤下标和步骤对象，用下标判断已完成/执行中。
        for index, step in enumerate(self.workflow_steps):
            if completed or (current >= 0 and index < current):
                state = "已完成"
            elif index == current:
                state = "准备中" if self.workflow_runner.awaiting_begin else "执行中"
            else:
                state = "待执行"
            self.workflow_tree.insert("", "end", iid=str(index), values=self._workflow_row(step, state))
        if selected_index is not None and selected_index < len(self.workflow_steps):
            self.workflow_tree.selection_set(str(selected_index))

    def _ensure_workflow_editable(self) -> bool:
        if self.workflow_runner.running:
            self._log_issue("流程正在运行", "请先点击“立即停止流程”，再编辑步骤。")
            return False
        return True

    def _selected_workflow_index(self) -> int | None:
        selected = self.workflow_tree.selection()
        return int(selected[0]) if selected else None

    def _select_workflow_step(self, _event: object = None) -> None:
        index = self._selected_workflow_index()
        if index is None or index >= len(self.workflow_steps):
            return
        step = self.workflow_steps[index]
        self.workflow_action_var.set(step.label)
        self.workflow_speed_var.set(f"{step.speed:.2f}" if step.moving else "")
        self.workflow_duration_var.set(f"{step.duration:.2f}" if step.duration else "")

    def _workflow_action_changed(self, _event: object = None) -> None:
        label = self.workflow_action_var.get()
        action = next((key for key, value in ACTION_LABELS.items() if value == label), "")
        if action in {"forward", "backward"}:
            self.workflow_speed_var.set("0.30")
            self.workflow_duration_var.set("2.00")
        elif action in {"left", "right"}:
            self.workflow_speed_var.set("0.20")
            self.workflow_duration_var.set("2.00")
        elif action in {"turn_left", "turn_right"}:
            self.workflow_speed_var.set("0.50")
            self.workflow_duration_var.set("2.00")
        elif action == "wait":
            self.workflow_speed_var.set("")
            self.workflow_duration_var.set("2.00")
        elif action in SPORT_ACTIONS:
            self.workflow_speed_var.set("")
            self.workflow_duration_var.set("3.00")
        else:
            self.workflow_speed_var.set("")
            self.workflow_duration_var.set("")

    def _step_from_editor(self) -> WorkflowStep | None:
        """读取下方三个编辑框，并复用 parse_workflow 完成安全校验。"""

        try:
            return parse_workflow(
                [
                    {
                        "action": self.workflow_action_var.get(),
                        "speed": self.workflow_speed_var.get(),
                        "duration": self.workflow_duration_var.get(),
                    }
                ]
            )[0]
        except ValueError as exc:
            self._log_issue("流程步骤错误", str(exc))
            return None

    def _add_workflow_step(self) -> None:
        """把编辑器中的一步追加到流程；追加前重新校验整套流程。"""

        if not self._ensure_workflow_editable():
            return
        step = self._step_from_editor()
        if step is None:
            return
        try:
            parse_workflow(asdict(item) for item in [*self.workflow_steps, step])
        except ValueError as exc:
            self._log_issue("流程错误", str(exc))
            return
        self.workflow_steps.append(step)
        self._refresh_workflow_tree()
        self.workflow_tree.selection_set(str(len(self.workflow_steps) - 1))

    def _update_workflow_step(self) -> None:
        """用编辑器内容替换选中步骤，只有候选流程整体合法才提交。"""

        if not self._ensure_workflow_editable():
            return
        index = self._selected_workflow_index()
        if index is None:
            self._log_issue("未选择步骤", "请先在流程表中选择要更新的步骤。")
            return
        step = self._step_from_editor()
        if step is None:
            return
        # 先操作副本 candidate；校验失败时原列表完全不变。
        candidate = list(self.workflow_steps)
        candidate[index] = step
        try:
            parse_workflow(asdict(item) for item in candidate)
        except ValueError as exc:
            self._log_issue("流程错误", str(exc))
            return
        self.workflow_steps = candidate
        self._refresh_workflow_tree()
        self.workflow_tree.selection_set(str(index))

    def _delete_workflow_step(self) -> None:
        """删除选中步骤，但始终至少保留一步。"""

        if not self._ensure_workflow_editable():
            return
        index = self._selected_workflow_index()
        if index is None:
            self._log_issue("未选择步骤", "请先选择要删除的步骤。")
            return
        if len(self.workflow_steps) == 1:
            self._log_issue("不能删除", "流程至少需要保留一个步骤。")
            return
        del self.workflow_steps[index]
        self._refresh_workflow_tree()
        next_index = min(index, len(self.workflow_steps) - 1)
        self.workflow_tree.selection_set(str(next_index))

    def _move_workflow_step(self, direction: int) -> None:
        """把选中步骤与上一步或下一步交换位置。"""

        if not self._ensure_workflow_editable():
            return
        index = self._selected_workflow_index()
        if index is None:
            self._log_issue("未选择步骤", "请先选择要移动的步骤。")
            return
        target = index + direction
        if not 0 <= target < len(self.workflow_steps):
            return
        # Python 支持无需临时变量的同时交换赋值。
        self.workflow_steps[index], self.workflow_steps[target] = (
            self.workflow_steps[target],
            self.workflow_steps[index],
        )
        self._refresh_workflow_tree()
        self.workflow_tree.selection_set(str(target))

    def _restore_default_workflow(self) -> None:
        if not self._ensure_workflow_editable():
            return
        self.workflow_steps = default_workflow()
        self.workflow_status_var.set("已恢复示例流程；尚未启动")
        self._refresh_workflow_tree()

    def _save_workflow(self) -> None:
        """弹出保存对话框，把校验后的步骤写成 UTF-8 JSON。"""

        if not self._ensure_workflow_editable():
            return
        path = filedialog.asksaveasfilename(
            title="保存自动流程",
            defaultextension=".json",
            filetypes=(("Go2 流程", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            Path(path).write_text(workflow_to_json(self.workflow_steps), encoding="utf-8")
        except (OSError, ValueError) as exc:
            self._log_issue("保存流程失败", str(exc))
            return
        self.workflow_status_var.set(f"流程已保存：{Path(path).name}")

    def _load_workflow(self) -> None:
        """从 JSON 文件读取流程；文件内容仍必须通过全部安全校验。"""

        if not self._ensure_workflow_editable():
            return
        path = filedialog.askopenfilename(
            title="读取自动流程",
            filetypes=(("Go2 流程", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            self.workflow_steps = workflow_from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._log_issue("读取流程失败", str(exc))
            return
        self.workflow_status_var.set(f"已读取流程：{Path(path).name}")
        self._refresh_workflow_tree()

    def _start_workflow(self) -> None:
        """检查连接和现场条件，展示完整预览，确认后启动自动流程。"""

        if self.workflow_runner.running:
            self._log_issue("流程正在运行", "当前流程尚未结束。")
            return
        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        if not self.hardware_ok_var.get():
            self._log_issue("未完成安全确认", "必须先勾选完整的硬件和现场安全确认。")
            return
        try:
            steps = parse_workflow(asdict(step) for step in self.workflow_steps)
        except ValueError as exc:
            self._log_issue("流程错误", str(exc))
            return
        total = sum(step.duration for step in steps)
        # 列表推导式为确认框生成“1. 站起、2. 等待……”这样的摘要。
        lines = [
            f"{index}. {step.label}"
            + (f" {step.speed:.2f}，{step.duration:.1f} 秒" if step.moving else "")
            + (f"，等待 {step.duration:.1f} 秒" if step.duration and not step.moving else "")
            for index, step in enumerate(steps, start=1)
        ]
        confirmed = self._ask_confirmation(
            "确认启动自动流程",
            "程序将自动准备行走模式并依次执行以下步骤：\n\n"
            + "\n".join(lines)
            + f"\n\n预计总时长约 {total:.1f} 秒。"
            "\n执行期间必须始终准备实体遥控器急停。确认启动吗？",
        )
        if not confirmed:
            return
        # 启动前先经过统一急停，清掉人工按键、旧武装和任何残留速度。
        self._emergency_stop(show_status=False)
        self.workflow_runner.start(steps)
        self._workflow_preparing = False
        self._workflow_resume_after = 0.0
        self.deadman_keys.clear()
        self.click_motion.stop()
        self.workflow_start_button.configure(state="disabled")
        self.workflow_status_var.set("自动流程已启动，正在进入第 1 步……")
        self._refresh_workflow_tree()

    def _cancel_workflow(self, message: str) -> bool:
        """取消流程并清理相关状态；返回取消前是否确实在运行。"""

        was_running = self.workflow_runner.running or self._workflow_preparing
        self.workflow_runner.cancel()
        self._workflow_preparing = False
        self._workflow_resume_after = 0.0
        if hasattr(self, "workflow_start_button"):
            self.workflow_start_button.configure(state="normal")
        if was_running:
            self.workflow_status_var.set(message)
            self._refresh_workflow_tree()
        return was_running

    def _stop_workflow(self) -> None:
        if not self.workflow_runner.running and not self._workflow_preparing:
            self.workflow_status_var.set("当前没有正在运行的自动流程")
            return
        self._emergency_stop(show_status=False)
        self.workflow_status_var.set("自动流程已由用户停止；已解除武装并发送 StopMove")

    def _begin_workflow_step(self, step: WorkflowStep, *, now: float) -> None:
        """当前步骤第一次进入时发送一次性动作，并开始本步骤计时。"""

        self.deadman_keys.clear()
        self.click_motion.stop()
        self.session.update_velocity(Velocity.zero())
        if step.action == "stand_up":
            self.policy.disarm()
            self.input_guard.deactivate()
            self.session.stand_up()
        elif step.action == "stand_down":
            self.policy.disarm()
            self.input_guard.deactivate()
            self.session.stand_down()
        elif step.action in SPORT_ACTIONS:
            self.policy.disarm()
            self.input_guard.deactivate()
            self.session.sport_action(step.action)
        self.workflow_runner.begin_current(now=now)
        self.workflow_status_var.set(
            f"正在执行第 {self.workflow_runner.current_index + 1}/{self.workflow_runner.step_count} 步："
            f"{step.label}"
        )
        self._refresh_workflow_tree()

    def _finish_workflow(self) -> None:
        self.deadman_keys.clear()
        self.click_motion.stop()
        self.policy.disarm()
        self.input_guard.deactivate()
        self.last_velocity = Velocity.zero()
        self.session.update_velocity(Velocity.zero())
        self.session.emergency_stop()
        self._workflow_preparing = False
        self.workflow_start_button.configure(state="normal")
        self.workflow_status_var.set("自动流程已完成；已停止移动并解除武装")
        self.velocity_var.set("x=0.00  y=0.00  z=0.00")
        self._refresh_workflow_tree(completed=True)

    def _workflow_velocity(self, *, now: float, focused: bool) -> Velocity | None:
        """推进自动流程并计算本帧速度；None 表示当前没有自动流程。"""

        # 返回 None 与返回 Velocity.zero() 含义不同：None 允许后续读取人工按键，
        # zero 表示自动流程正在控制，但本帧必须保持停止。
        """让自动流程向前运行一步，并计算当前应发送的速度。"""
        if not self.workflow_runner.running:
            return None
        step = self.workflow_runner.current_step
        if step is None:
            return Velocity.zero()

        if self.workflow_runner.awaiting_begin and now < self._workflow_resume_after:
            self.workflow_status_var.set("步骤切换中：保持停止 0.2 秒……")
            return Velocity.zero()

        if step.moving and self.workflow_runner.awaiting_begin:
            if not self.session.connected or not self.hardware_ok_var.get():
                self._emergency_stop(show_status=False)
                self.workflow_status_var.set("自动流程因连接或安全确认状态变化而中止")
                return Velocity.zero()
            if not self.session.walk_ready or not self.policy.armed:
                if not self._workflow_preparing:
                    self._workflow_preparing = True
                    self.activation.begin()
                    self.walk_mode_var.set("自动流程正在准备行走模式……")
                    self.arm_var.set("自动流程准备中；模式确认成功后自动武装")
                    self.session.prepare_walk_mode()
                self.workflow_status_var.set(
                    f"第 {self.workflow_runner.current_index + 1} 步等待行走模式准备完成……"
                )
                self._refresh_workflow_tree()
                return Velocity.zero()

        if self.workflow_runner.awaiting_begin:
            self._begin_workflow_step(step, now=now)

        if step.moving and (not self.session.walk_ready or not self.policy.armed):
            self._emergency_stop(show_status=False)
            self.workflow_status_var.set("自动流程因行走模式或武装状态失效而中止")
            return Velocity.zero()

        if not focused or not self.session.connected or not self.hardware_ok_var.get():
            self._emergency_stop(show_status=False)
            self.workflow_status_var.set("自动流程因窗口失焦、断线或安全确认变化而中止")
            return Velocity.zero()

        velocity = velocity_for_step(step)
        advanced = self.workflow_runner.tick(now=now)
        if advanced:
            self.session.update_velocity(Velocity.zero())
            if not self.workflow_runner.running:
                self._finish_workflow()
                return Velocity.zero()
            self._workflow_resume_after = now + 0.20
            self.workflow_status_var.set("步骤切换中：保持停止 0.2 秒……")
            self._refresh_workflow_tree()
            return Velocity.zero()

        self.workflow_status_var.set(
            f"第 {self.workflow_runner.current_index + 1}/{self.workflow_runner.step_count} 步："
            f"{step.label}，剩余 {self.workflow_runner.remaining_seconds(now=now):.1f} 秒"
        )
        return velocity

    def _motion_button(
        self,
        parent: ttk.Frame,
        text: str,
        key: str,
        row: int,
        column: int,
    ) -> None:
        button = ttk.Button(
            parent,
            text=text,
            width=12,
            command=lambda: self._start_clicked_motion(key),
            style="Motion.TButton",
        )
        button.grid(row=row, column=column, padx=6, pady=6, ipadx=8, ipady=8)
        self._motion_buttons[key] = button
        button.bind(
            "<ButtonPress-1>",
            self._focus_motion_button,
        )

    # ==================== 连接、准备和高层动作 ====================

    def _bind_events(self) -> None:
        """把 Tkinter 事件名称绑定到本类方法。"""

        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_mode_changed(self, _event: object = None) -> None:
        self._last_control_state_signature = None
        self._sync_control_states()

    def _connect(self) -> None:
        """校验界面输入，清除旧运动状态，再请求通信层连接。"""

        mode = self.mode_var.get()
        ip = self.ip_var.get().strip()
        try:
            aes_key = normalize_aes_key(self.aes_var.get())
        except ValueError as exc:
            self._log_issue("AES key 格式错误", str(exc))
            return
        if mode == "STA":
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                self._log_issue("IP 错误", "请输入有效的 Go2 IPv4 地址。")
                return
        self._emergency_stop(show_status=False)
        self.connect_button.configure(state="disabled")
        self.connection_var.set("正在连接……")
        # GUI 不直接等待网络；RobotSession.connect 会把任务提交给后台线程。
        self.session.connect(
            ConnectionSettings(mode=mode, ip=ip, aes_key=aes_key)  # type: ignore[arg-type]
        )

    def _update_aes_hint(self, *_args: object) -> None:
        compact = _compact_aes_key(self.aes_var.get())
        if not compact:
            self.aes_hint_var.set("可留空；若固件要求密钥，连接时会明确报错")
        elif re.fullmatch(r"[0-9a-fA-F]{32}", compact):
            self.aes_hint_var.set("整理后 32/32 个十六进制字符：格式正确（不会保存）")
        else:
            self.aes_hint_var.set(f"整理后 {len(compact)}/32 个字符：格式尚不正确")

    def _disconnect(self) -> None:
        self._emergency_stop(show_status=False)
        self.connection_var.set("正在停止并断开……")
        self.session.disconnect()

    def _prepare_and_arm(self) -> None:
        """执行人工确认，并启动“检查模式成功后自动武装”的一次性流程。"""

        if self.workflow_runner.running:
            self._log_issue("自动流程正在运行", "请先停止自动流程，再使用手动准备。")
            return
        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        if not self.hardware_ok_var.get():
            self._log_issue("未完成安全确认", "必须先勾选完整的硬件和现场安全确认。")
            return
        confirmed = self._ask_confirmation(
            "一键准备并武装",
            "程序将先停止当前移动，确认 normal/mcf 行走模式，成功后自动武装。\n"
            "只有其他模式才尝试切换，机器人可能自动站起。\n"
            "准备完成后，点击界面方向按钮会持续移动；键盘仍需按住。\n\n确认继续吗？",
        )
        if not confirmed:
            return
        self._emergency_stop(show_status=False)
        self.activation.begin()
        self.walk_mode_var.set("正在确认可用行走模式……")
        self.arm_var.set("正在准备；模式确认成功后将自动武装")
        self.session.prepare_walk_mode()

    def _ask_confirmation(self, title: str, message: str) -> bool:
        """统一显示危险操作确认框，并避免弹窗暂时失焦被误判成急停。"""

        self._modal_open = True
        try:
            return messagebox.askyesno(title, message, icon="warning")
        finally:
            self._modal_open = False

    def _activate_control_input(self) -> None:
        if self.policy.armed:
            self.input_guard.activate(self.root.winfo_id())

    def _stand_down(self) -> None:
        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        if not self.hardware_ok_var.get():
            self._log_issue("未完成安全确认", "必须先勾选完整的硬件和现场安全确认。")
            return
        confirmed = self._ask_confirmation(
            "确认安全卧趴",
            "Go2 将立即停止移动并降低身体执行 StandDown。\n请确认机身和四条腿下方无人、无物。\n\n确认继续吗？",
        )
        if not confirmed:
            return
        self._emergency_stop(show_status=False)
        self.walk_mode_var.set("正在停止并执行卧趴……")
        self.session.stand_down()

    def _stand_up(self) -> None:
        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        if not self.hardware_ok_var.get():
            self._log_issue("未完成安全确认", "必须先勾选完整的硬件和现场安全确认。")
            return
        confirmed = self._ask_confirmation(
            "确认一键站起",
            "Go2 将立即停止移动并执行 StandUp 站起。\n"
            "请确认机器狗四周和上方无人、无物，地面平整防滑。\n\n确认继续吗？",
        )
        if not confirmed:
            return
        self._emergency_stop(show_status=False)
        self.walk_mode_var.set("正在停止并执行站起……")
        self.session.stand_up()

    def _run_sport_action(self, action: str) -> None:
        """高层动作统一入口：特殊处理站起/卧趴，其余动作走通用确认流程。"""

        if action == "stand_down":
            self._stand_down()
            return
        if action == "stand_up":
            self._stand_up()
            return
        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        if not self.hardware_ok_var.get():
            self._log_issue("未完成安全确认", "必须先勾选完整的硬件和现场安全确认。")
            return
        label = ACTION_LABELS[action]
        usage_note = ""
        if action == "balance_stand":
            usage_note = "\n程序会先执行 StandUp，再进入 BalanceStand 平衡状态。"
        elif action == "recovery_stand":
            usage_note = "\nRecoveryStand 仅用于机器狗已经跌倒的情况；正常直立时不会有明显动作。"
        confirmed = self._ask_confirmation(
            f"确认执行{label}",
            f"Go2 将立即停止移动并执行“{label}”。\n"
            f"{usage_note}\n"
            "请确认四周和上方无人、无物，地面平整防滑，实体遥控器可随时急停。\n\n"
            "确认继续吗？",
        )
        if not confirmed:
            return
        self._emergency_stop(show_status=False)
        self.walk_mode_var.set(f"正在停止并执行{label}……")
        self.session.sport_action(action)

    def _toggle_camera(self) -> None:
        """连接存在时请求通信层打开摄像头。"""

        if not self.session.connected:
            self._log_issue("尚未连接", "请先连接 Go2。")
            return
        self._camera_requested = True
        self.camera_var.set("正在请求摄像头画面……")
        self.session.start_video()
        self._last_control_state_signature = None
        self._sync_control_states()

    def _stop_camera(self) -> None:
        self._camera_requested = False
        self.session.stop_video()
        with self._video_lock:
            self._latest_video_frame = None
        self._person_detection.clear()
        self._person_detection_enabled = False
        self._camera_photo = None
        self.camera_label.configure(image="", text="摄像头已关闭")
        self.camera_var.set("摄像头未开启")
        self._last_control_state_signature = None
        self._sync_control_states()

    def _start_person_detection(self) -> None:
        """允许后续摄像头帧进入 YOLO；首次开启沿用初始绿色框。"""

        self._person_detection_enabled = True
        if self._camera_requested:
            self.camera_var.set("摄像头已开启 · 人员识别已开启（绿色框）")
        else:
            self.camera_var.set("人员识别已开启，等待摄像头画面")
        self._last_control_state_signature = None
        self._sync_control_states()

    def _stop_person_detection(self) -> None:
        """停止向 YOLO 送帧并立即清除旧框，不关闭摄像头。"""

        self._person_detection_enabled = False
        self._person_detection.clear()
        self.camera_var.set(
            "摄像头已开启 · 人员识别已关闭"
            if self._camera_requested
            else "摄像头未开启"
        )
        self._last_control_state_signature = None
        self._sync_control_states()

    def _refresh_person_detection(self) -> None:
        """重新加载模型、清空旧框，并按用户要求切换为红色人员框。"""

        self._person_box_color = PERSON_BOX_RED
        self._person_detection.refresh()
        self.camera_var.set("正在刷新人员识别模型；刷新后使用红色框")

    # ==================== 人工输入和统一停止链 ====================

    def _emergency_stop(self, *, show_status: bool = True) -> None:
        """软件急停的统一入口：取消流程、解除武装、清零并请求 StopMove。"""
        self._cancel_workflow("自动流程已被急停中止")
        self.activation.cancel()
        self.input_guard.deactivate()
        self.policy.disarm()
        self.deadman_keys.clear()
        self.click_motion.stop()
        self.last_velocity = Velocity.zero()
        self.session.emergency_stop()
        self.velocity_var.set("x=0.00  y=0.00  z=0.00")
        if show_status:
            self.arm_var.set("急停已触发：已解除武装并发送 StopMove（失败时重试）")

    def _on_key_press(self, event: tk.Event[tk.Misc]) -> str | None:
        """处理物理按键；返回 'break' 会阻止事件继续进入中文输入法。"""

        if self._typing_in_field():
            return None
        key = control_key_from_event(event.keysym, event.keycode)
        if key == "space":
            self._emergency_stop()
            return "break"
        if key in CONTROL_KEYS:
            self.click_motion.stop()
            self._press(key, requires_repeat=True)
            return "break"
        return None

    def _on_key_release(self, event: tk.Event[tk.Misc]) -> str | None:
        if self._typing_in_field():
            return None
        key = control_key_from_event(event.keysym, event.keycode)
        if key in CONTROL_KEYS:
            self._release(key)
            return "break"
        return None

    def _on_focus_out(self, _event: object) -> None:
        """窗口焦点变化后延迟检查，避免控件之间切换焦点被误判。"""

        if self._modal_open:
            return
        self.root.after_idle(self._stop_if_app_unfocused)

    def _stop_if_app_unfocused(self) -> None:
        if self._modal_open:
            return
        if self.root.focus_displayof() is None:
            self._emergency_stop(show_status=False)
            self.walk_mode_var.set("行走模式已因窗口失焦失效；请重新准备")
            self.arm_var.set("窗口失去焦点：已停止并解除武装")

    def _typing_in_field(self) -> bool:
        """判断焦点是否在文本类控件，输入 IP/key 时不抢 W/A/S/D。"""

        widget = self.root.focus_get()
        return isinstance(
            widget,
            (tk.Entry, tk.Spinbox, ttk.Entry, ttk.Spinbox, ttk.Combobox),
        )

    def _press(self, key: str, *, requires_repeat: bool) -> None:
        """在全部门禁通过后，把键盘按下状态交给 DeadmanKeys。"""

        if key not in CONTROL_KEYS:
            return
        if self.workflow_runner.running:
            self.arm_var.set("自动流程运行中：手动方向键已禁用；空格仍可急停")
            return
        if not self.session.connected:
            self.arm_var.set("不能移动：尚未连接 Go2")
            return
        if not self.session.walk_ready:
            self.arm_var.set("不能移动：请先点击“一键准备并武装”")
            return
        if not self.policy.armed:
            self.arm_var.set("不能移动：请先点击“一键准备并武装”")
            return
        self.deadman_keys.press(
            key,
            now=time.monotonic(),
            requires_repeat=requires_repeat,
        )

    def _focus_motion_button(self, event: tk.Event[tk.Misc]) -> None:
        try:
            event.widget.focus_set()
        except tk.TclError:
            return

    def _start_clicked_motion(self, key: str) -> None:
        """鼠标方向按钮采用锁存：点击一次持续移动，直到明确停止。"""

        if self.workflow_runner.running:
            self.arm_var.set("自动流程运行中：手动方向按钮已禁用；空格仍可急停")
            return
        if not self._motion_allowed():
            return
        self.deadman_keys.clear()
        self.click_motion.start(key)
        self.arm_var.set("持续移动中：点击停止移动、按空格或切换窗口会立即停止")

    def _motion_allowed(self) -> bool:
        """人工移动的三道门禁：已连接、模式就绪、已武装。"""

        if not self.session.connected:
            self.arm_var.set("不能移动：尚未连接 Go2")
            return False
        if not self.session.walk_ready:
            self.arm_var.set("不能移动：请先点击“一键准备并武装”")
            return False
        if not self.policy.armed:
            self.arm_var.set("不能移动：请先点击“一键准备并武装”")
            return False
        return True

    def _stop_motion(self) -> None:
        """停止当前移动但保留武装，方便再次点击方向按钮。"""

        self.deadman_keys.clear()
        self.click_motion.stop()
        self.last_velocity = Velocity.zero()
        self.session.update_velocity(Velocity.zero())
        self.velocity_var.set("x=0.00  y=0.00  z=0.00")
        if self.policy.armed and self.session.walk_ready:
            self.arm_var.set("已停止移动，仍保持武装；可再次点击方向按钮")

    def _apply_speed_settings(self) -> None:
        """校验并应用手动速度；应用前先停止，避免运行中速度突变。"""

        if self.workflow_runner.running:
            self._log_issue("自动流程正在运行", "请先停止自动流程，再修改手动速度。")
            return
        try:
            limits = parse_control_limits(
                self.linear_speed_var.get(),
                self.lateral_speed_var.get(),
                self.yaw_speed_var.get(),
            )
        except ValueError as exc:
            self._log_issue("速度设置错误", str(exc))
            return

        self._stop_motion()
        self.policy.limits = limits
        self.linear_speed_var.set(f"{limits.linear:.2f}")
        self.lateral_speed_var.set(f"{limits.lateral:.2f}")
        self.yaw_speed_var.set(f"{limits.yaw:.2f}")
        if self.policy.armed and self.session.walk_ready:
            self.arm_var.set("新速度已应用并停止当前移动；可重新点击方向按钮")

    def _release(self, key: str) -> None:
        self.deadman_keys.release(key)

    # ==================== GUI 心跳、视频和后台事件 ====================

    def _tick(self) -> None:
        """GUI 心跳：处理事件、流程、速度和视频，然后预约下一次调用。"""
        try:
            app_focused = self.root.focus_displayof() is not None
            now = time.monotonic()
            self._update_header_activity(now)
            self._sync_control_states()
            self._sync_motion_button_feedback()
            # 自动流程优先；没有流程时才组合键盘与鼠标锁存方向。
            workflow_velocity = self._workflow_velocity(now=now, focused=app_focused)
            if workflow_velocity is None:
                keys = self.deadman_keys.active(now=now)
                keys.update(self.click_motion.active_keys())
                velocity = self.policy.velocity_for(
                    keys,
                    focused=app_focused and not self._typing_in_field(),
                )
            else:
                velocity = workflow_velocity
            self.session.update_velocity(velocity)
            if velocity != self.last_velocity:
                self.last_velocity = velocity
                self.velocity_var.set(
                    f"x={velocity.forward:+.2f}  y={velocity.lateral:+.2f}  "
                    f"z={velocity.yaw:+.2f}"
                )
            # 摄像头只是附加预览，单帧损坏不能让运动流程的心跳永久停止。
            try:
                self._render_latest_video_frame()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self.camera_var.set(f"摄像头单帧显示失败，已跳过：{message}")
                self._log_issue("摄像头单帧显示失败", message)
        except tk.TclError:
            pass
        except Exception as exc:
            # 流程/输入计算若发生未知异常，按安全原则停止，而不是停在“准备中”。
            self._log_issue("界面心跳错误", f"{type(exc).__name__}: {exc}")
            self.policy.disarm()
            self.deadman_keys.clear()
            self.click_motion.stop()
            self.session.emergency_stop()
            self._cancel_workflow(
                f"自动流程因界面心跳异常而中止：{type(exc).__name__}: {exc}"
            )
        finally:
            # 旧实现把 after 放在 try 末尾：前面任何异常都会令心跳永久消失。
            # finally 保证只要窗口仍存在，下一帧就一定会继续调度。
            try:
                self.root.after(50, self._tick)
            except tk.TclError:
                pass

    def _queue_video_frame(self, frame: VideoFrameData) -> None:
        """后台线程只覆盖最新帧；界面来不及显示时主动丢弃旧帧，防止堆积。"""

        # 识别线程也只接收最新帧，模型慢时不会拖住 WebRTC 或积压内存。
        if self._person_detection_enabled:
            self._person_detection.submit(frame)
        with self._video_lock:
            self._latest_video_frame = frame

    def _render_latest_video_frame(self) -> None:
        """在 GUI 主线程取出最新帧，转成 Tkinter 能显示的 PhotoImage。"""

        with self._video_lock:
            frame = self._latest_video_frame
            self._latest_video_frame = None
        if frame is None:
            return
        snapshot = (
            self._person_detection.snapshot(max_age=0.5)
            if self._person_detection_enabled
            else None
        )
        if snapshot is not None:
            frame = draw_person_boxes(frame, snapshot.boxes, self._person_box_color)
        photo = tk.PhotoImage(data=frame.ppm_bytes(), format="PPM")
        factor = camera_subsample_factor(frame.width, frame.height)
        if factor > 1:
            photo = photo.subsample(factor, factor)
        # 必须把 PhotoImage 保存在实例字段中，否则会被垃圾回收后画面消失。
        self._camera_photo = photo
        self.camera_label.configure(image=self._camera_photo, text="")
        self.camera_var.set("摄像头画面接收正常（不保存）")

    def _queue_person_detection_event(self, kind: str, message: str) -> None:
        """把识别线程状态送回 Tkinter 主线程。"""

        try:
            self.root.after(0, self._handle_person_detection_event, kind, message)
        except tk.TclError:
            pass

    def _handle_person_detection_event(self, kind: str, message: str) -> None:
        """更新识别状态；错误只进入日志，不弹出窗口。"""

        if kind == "loading":
            self.camera_var.set(message)
        elif kind == "ready":
            box_label = "红色框" if self._person_box_color == PERSON_BOX_RED else "绿色框"
            if self._person_detection_enabled and self._camera_requested:
                self.camera_var.set(f"摄像头已开启 · 人员识别已就绪（{box_label}）")
            elif self._person_detection_enabled:
                self.camera_var.set("人员识别模型已就绪，等待摄像头画面")
            else:
                self.camera_var.set(
                    "摄像头已开启 · 人员识别模型已就绪但未启用"
                    if self._camera_requested
                    else "摄像头未开启 · 人员识别模型已就绪但未启用"
                )
        elif kind == "error":
            self.camera_var.set("摄像头继续预览 · 人员识别加载失败，详情见错误日志")
            self._log_issue("人员识别错误", message)

    def _queue_session_event(self, kind: EventKind, message: str) -> None:
        """把后台通信事件排入 Tkinter 主线程，避免跨线程直接改控件。"""

        try:
            self.root.after(0, self._handle_session_event, kind, message)
        except tk.TclError:
            pass

    def _handle_session_event(self, kind: EventKind, message: str) -> None:
        """在 GUI 线程处理连接、模式、摄像头和错误事件。"""

        if kind in {"status", "connected", "disconnected", "error"}:
            self.connection_var.set(message)
        if kind == "connected":
            self.connect_button.configure(state="normal")
            self.aes_var.set("")
            self._set_connection_details_visible(False)
        elif kind == "walk_ready":
            self.walk_mode_var.set(message)
            if self.activation.consume_walk_ready():
                if self.session.connected and self.hardware_ok_var.get():
                    self.policy.arm(hardware_confirmed=True)
                    if self.mode_var.get() == "AP":
                        self._log_diagnostic("[AP] 自动武装完成；现在允许发送非零移动速度")
                    self._workflow_preparing = False
                    if self.workflow_runner.running:
                        self.arm_var.set("自动流程已武装：空格或红色急停立即中止")
                        self.workflow_status_var.set("行走模式已就绪，自动流程即将继续")
                    else:
                        self.arm_var.set("已自动武装：点击按钮持续移动；空格立即停止")
                        # 手动准备完成后直接展示运动区，减少一次额外查找和点击。
                        self.main_tabs.select(1)
                    self.root.focus_set()
                    self.root.after_idle(self._activate_control_input)
                else:
                    self.arm_var.set("自动武装已取消：连接或安全确认状态已变化")
        elif kind == "walk_not_ready":
            self.policy.disarm()
            self.deadman_keys.clear()
            self.click_motion.stop()
            self.walk_mode_var.set(message)
            if self.activation.pending:
                self.arm_var.set("正在准备；模式确认成功后将自动武装")
            else:
                self.arm_var.set("未武装：不会发送移动命令")
        elif kind == "camera":
            self.camera_var.set(message)
        elif kind == "diagnostic":
            self._log_diagnostic(message)
        elif kind == "action_warning":
            # Heart 确认超时不等于机器人明确拒绝；记录证据，但不取消后续步骤。
            self.walk_mode_var.set("比心命令已发送但确认超时；流程继续，移动前会重新准备")
            if self.workflow_runner.running:
                self.workflow_status_var.set("比心已完成或已提交；流程将按设定时长继续")
            self._log_issue("Go2 动作确认超时（流程继续）", message)
        elif kind == "action_error":
            # 单个高层动作失败并不代表 WebRTC 已断开；保留连接，停止自动流程和移动。
            self._cancel_workflow("自动流程因高层动作未执行而中止")
            self.activation.cancel()
            self.policy.disarm()
            self.deadman_keys.clear()
            self.click_motion.stop()
            self.arm_var.set("未武装：高层动作未执行，连接仍保留")
            self.walk_mode_var.set("动作未执行；重新移动前请一键准备并武装")
            self._log_issue("Go2 动作未执行", message)
        elif kind in {"disconnected", "error"}:
            self._cancel_workflow("自动流程因断线或通信错误而中止")
            self.activation.cancel()
            self.input_guard.deactivate()
            self.connect_button.configure(state="normal")
            self.policy.disarm()
            self.deadman_keys.clear()
            self.click_motion.stop()
            self.arm_var.set("未武装：不会发送移动命令")
            self.walk_mode_var.set("行走模式未准备")
            if kind == "disconnected":
                self._stop_camera()
                self._set_connection_details_visible(True)
            if kind == "error":
                self._log_issue("Go2 通信错误", message)
        self._last_control_state_signature = None
        self._sync_control_states()

    def _on_close(self) -> None:
        """窗口关闭时按安全顺序停止流程、恢复输入法、断开网络再销毁 GUI。"""

        self._cancel_workflow("自动流程已因程序关闭而中止")
        self.input_guard.deactivate()
        self.policy.disarm()
        self.deadman_keys.clear()
        self.click_motion.stop()
        self._person_detection.close()
        self.session.shutdown()
        self.root.destroy()


def main() -> None:
    """创建 Tk 根窗口并进入 GUI 事件循环。"""
    root = tk.Tk()
    SafeControlApp(root)
    root.mainloop()
