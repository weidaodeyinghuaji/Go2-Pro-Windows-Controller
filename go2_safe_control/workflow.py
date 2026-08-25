"""自动流程的数据模型、校验、保存格式和计时器。

本模块只计算“当前应该执行哪一步”，不会直接控制机器人。
界面层读取这里的结果，再通过 RobotSession 发送实际命令。
"""

from __future__ import annotations

import json
import math
# asdict 把 dataclass 对象变成字典；dataclass 自动生成数据类常用方法。
from dataclasses import asdict, dataclass
# Iterable 表示“可以被 for 遍历的对象”，Mapping 表示类似字典的键值结构。
from typing import Iterable, Mapping

from .safety import Velocity


# key 是程序内部稳定名称，value 是界面显示的中文名称。
ACTION_LABELS = {
    "stand_up": "一键站起",
    "balance_stand": "平衡站立",
    "recovery_stand": "跌倒恢复",
    "sit": "坐下",
    "rise_sit": "坐起",
    "hello": "打招呼",
    "stretch": "伸展",
    "heart": "比心",
    "wait": "等待",
    "forward": "前进",
    "backward": "后退",
    "left": "左移",
    "right": "右移",
    "turn_left": "左转",
    "turn_right": "右转",
    "stop": "停止",
    "stand_down": "安全卧趴",
}
# 字典推导式把上面的映射反过来，用中文标签查内部动作名。
LABEL_ACTIONS = {label: action for action, label in ACTION_LABELS.items()}
# 兼容旧版本保存的流程文件；界面显示新名称，但读取“恢复站立”仍能识别。
LABEL_ACTIONS["恢复站立"] = "recovery_stand"
# 集合只关心“是否包含”，适合把动作分成移动动作和高层动作两组。
MOTION_ACTIONS = {
    "forward",
    "backward",
    "left",
    "right",
    "turn_left",
    "turn_right",
}
SPORT_ACTIONS = {
    "stand_up",
    "stand_down",
    "balance_stand",
    "recovery_stand",
    "sit",
    "rise_sit",
    "hello",
    "stretch",
    "heart",
}
MAX_STEPS = 30
MAX_TOTAL_DURATION = 120.0


@dataclass(frozen=True)
class WorkflowStep:
    """一个不可变流程步骤：动作、速度和需要等待的时间。"""

    action: str
    speed: float
    duration: float

    @property
    def label(self) -> str:
        """把内部英文动作名转换为界面中文名称。"""

        return ACTION_LABELS[self.action]

    @property
    def moving(self) -> bool:
        """判断此步骤是否会持续发送非零速度。"""

        return self.action in MOTION_ACTIONS


def default_workflow() -> list[WorkflowStep]:
    """返回第一次打开软件时显示的示例流程。"""

    # 每次调用都创建一份新列表，修改界面步骤不会污染下一次恢复示例。
    return [
        WorkflowStep("stand_up", 0.0, 3.0),
        WorkflowStep("wait", 0.0, 2.0),
        WorkflowStep("forward", 0.30, 3.0),
        WorkflowStep("turn_right", 0.50, 2.0),
        WorkflowStep("forward", 0.30, 2.0),
        WorkflowStep("stop", 0.0, 0.0),
        WorkflowStep("stand_down", 0.0, 3.0),
    ]


def _number(value: object, *, label: str, default: float = 0.0) -> float:
    """把界面输入安全转换为有限浮点数，并生成中文错误信息。"""

    # str(...) 兼容文本框字符串和 JSON 数字；strip() 去掉首尾空白。
    text = str(value).strip()
    if not text:
        return default
    try:
        result = float(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字。") from exc
    # NaN 和正负无穷虽然能被 float 接受，但不能作为真实运动参数。
    if not math.isfinite(result):
        raise ValueError(f"{label}必须是有限数字。")
    return result


def parse_workflow(rows: Iterable[Mapping[str, object]]) -> list[WorkflowStep]:
    """把界面/JSON 的原始数据转换成经过安全校验的步骤列表。"""

    # 先转成 list，因为传入值也可能是只能遍历一次的生成器。
    items = list(rows)
    if not items:
        raise ValueError("流程至少需要一个步骤。")
    if len(items) > MAX_STEPS:
        raise ValueError(f"流程最多允许 {MAX_STEPS} 个步骤。")

    steps: list[WorkflowStep] = []
    # enumerate(..., start=1) 同时得到从 1 开始的步骤编号和当前数据。
    for index, row in enumerate(items, start=1):
        raw_action = str(row.get("action", "")).strip()
        # get 的第二个参数是默认值：中文标签能翻译，内部英文名则原样保留。
        action = LABEL_ACTIONS.get(raw_action, raw_action)
        if action not in ACTION_LABELS:
            raise ValueError(f"第 {index} 步动作无效：{raw_action or '空'}。")

        speed = _number(row.get("speed", ""), label=f"第 {index} 步速度")
        duration = _number(row.get("duration", ""), label=f"第 {index} 步持续时间")
        label = ACTION_LABELS[action]

        if action in MOTION_ACTIONS:
            # 不同运动方向采用不同安全速度范围：转向、横移、前后分别处理。
            minimum, maximum = (
                (0.10, 1.00)
                if action in {"turn_left", "turn_right"}
                else ((0.05, 0.50) if action in {"left", "right"} else (0.05, 0.80))
            )
            if not minimum <= speed <= maximum:
                raise ValueError(
                    f"第 {index} 步{label}速度必须在 {minimum:.2f} 到 {maximum:.2f} 之间。"
                )
            if not 0.10 <= duration <= 10.0:
                raise ValueError(f"第 {index} 步{label}持续时间必须在 0.10 到 10.00 秒之间。")
        elif action == "wait":
            # 等待、高层动作和停止都必须强制速度为 0，不能相信文件中的原始速度。
            speed = 0.0
            if not 0.10 <= duration <= 30.0:
                raise ValueError(f"第 {index} 步等待持续时间必须在 0.10 到 30.00 秒之间。")
        elif action in SPORT_ACTIONS:
            speed = 0.0
            if not 1.0 <= duration <= 10.0:
                raise ValueError(f"第 {index} 步{label}等待时间必须在 1.00 到 10.00 秒之间。")
        else:
            speed = 0.0
            duration = 0.0

        # 只有通过本步骤全部校验后，才把它加入最终列表。
        steps.append(WorkflowStep(action, speed, duration))

    # 生成器表达式逐个取 duration 求和，限制整套自动流程的总运行时间。
    total = sum(step.duration for step in steps)
    if total > MAX_TOTAL_DURATION:
        raise ValueError(
            f"流程总时长为 {total:.1f} 秒，不能超过 {MAX_TOTAL_DURATION:.0f} 秒。"
        )
    return steps


def velocity_for_step(step: WorkflowStep) -> Velocity:
    """把六个移动方向转换成带正负号的三轴速度。"""

    speed = step.speed
    # 字典的 value 是对应方向的速度对象；get 的默认值保证非移动动作得到停止。
    return {
        "forward": Velocity(speed, 0.0, 0.0),
        "backward": Velocity(-speed, 0.0, 0.0),
        "left": Velocity(0.0, speed, 0.0),
        "right": Velocity(0.0, -speed, 0.0),
        "turn_left": Velocity(0.0, 0.0, speed),
        "turn_right": Velocity(0.0, 0.0, -speed),
    }.get(step.action, Velocity.zero())


def workflow_to_json(steps: Iterable[WorkflowStep]) -> str:
    """重新校验步骤，再转换成方便人阅读的 UTF-8 JSON 文本。"""

    # asdict(step) 会得到 {"action": ..., "speed": ..., "duration": ...}。
    validated = parse_workflow(asdict(step) for step in steps)
    # ensure_ascii=False 让中文直接保留；indent=2 让文件带有两空格缩进。
    return json.dumps([asdict(step) for step in validated], ensure_ascii=False, indent=2)


def workflow_from_json(text: str) -> list[WorkflowStep]:
    """读取 JSON 文本，并把不可信文件内容交给统一校验器处理。"""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"流程文件不是有效 JSON：{exc.msg}。") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("流程文件必须是步骤列表。")
    return parse_workflow(payload)


class WorkflowRunner:
    """按时间推进流程的有限状态机，不执行任何网络操作。"""

    def __init__(self) -> None:
        # tuple 不可变，流程启动后不能在执行过程中被界面偷偷改掉。
        self._steps: tuple[WorkflowStep, ...] = ()
        # -1 表示当前没有任何步骤；0 表示第一步。
        self._index = -1
        # None 表示当前步骤尚未正式开始计时。
        self._started_at: float | None = None
        self.running = False
        self.finished = False

    @property
    def current_step(self) -> WorkflowStep | None:
        """返回正在执行的步骤；未运行或索引无效时返回 None。"""

        if not self.running or not 0 <= self._index < len(self._steps):
            return None
        return self._steps[self._index]

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def awaiting_begin(self) -> bool:
        return self.running and self._started_at is None

    def start(self, steps: Iterable[WorkflowStep]) -> None:
        """重新校验并装载流程，但第一步要等界面调用 begin_current 才计时。"""

        validated = parse_workflow(asdict(step) for step in steps)
        self._steps = tuple(validated)
        self._index = 0
        self._started_at = None
        self.running = True
        self.finished = False

    def begin_current(self, *, now: float) -> None:
        """记录当前步骤的开始时刻；重复调用不会重置计时。"""

        if not self.running or self.current_step is None:
            raise RuntimeError("没有可开始的流程步骤。")
        if self._started_at is None:
            self._started_at = now

    def tick(self, *, now: float) -> bool:
        """检查当前步骤是否到时；发生步骤切换时返回 True。"""

        step = self.current_step
        if step is None or self._started_at is None:
            return False
        if now - self._started_at < step.duration:
            return False

        # 当前步骤到时：索引加一，并让下一步重新等待 begin_current。
        self._index += 1
        self._started_at = None
        if self._index >= len(self._steps):
            self.running = False
            self.finished = True
        return True

    def remaining_seconds(self, *, now: float) -> float:
        """计算当前步骤还剩多少秒，结果永远不会小于 0。"""

        step = self.current_step
        if step is None:
            return 0.0
        if self._started_at is None:
            return step.duration
        return max(0.0, step.duration - (now - self._started_at))

    def cancel(self) -> None:
        """清空状态；急停、断线、失焦和人工停止流程都会调用。"""

        self._steps = ()
        self._index = -1
        self._started_at = None
        self.running = False
        self.finished = False
