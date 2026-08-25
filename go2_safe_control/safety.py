"""与机器人安全移动有关的纯 Python 逻辑。

本模块不连接机器人，因此最适合初学者先阅读和运行测试。
它负责描述速度、限制速度、处理按键超时和决定当前能否移动。
"""

# 让类型注解可以引用“稍后才定义”的类，并减少运行时解析类型的负担。
# 这是现代 Python 项目常见的兼容写法；它不会改变下面函数的实际执行逻辑。
from __future__ import annotations

# dataclass 会自动生成 __init__、__repr__、__eq__ 等重复代码。
# 因此 Velocity(0.3, 0.0, 0.0) 能直接创建一个速度对象。
from dataclasses import dataclass


# frozen=True 表示对象创建后不能再修改字段。
# 速度命令不可变，可以避免某个线程在另一个线程使用时偷偷改值。
@dataclass(frozen=True)
class Velocity:
    """机器人速度：前后、左右和原地转向三个方向。"""

    # 冒号后的 float 是“类型注解”：提示这三个字段应当是浮点数。
    # 它主要帮助读者、PyCharm 和类型检查器理解代码，不会自动限制赋值。
    forward: float
    lateral: float
    yaw: float

    # classmethod 把类本身放进 cls，而不是把某个对象放进 self。
    # 因此可以直接写 Velocity.zero()，不用先创建 Velocity 对象。
    @classmethod
    def zero(cls) -> "Velocity":
        """创建一个三个方向都为 0 的速度对象，也就是“停止”。"""

        # cls(...) 等价于这里的 Velocity(...)；使用 cls 便于将来继承这个类。
        return cls(0.0, 0.0, 0.0)

    # property 让方法像普通字段一样读取：velocity.moving，而不是 moving()。
    @property
    def moving(self) -> bool:
        """只要任意一个方向不是 0，就认为机器人正在移动。"""

        # any(...) 会检查序列里是否至少有一个“真值”。数字 0.0 是假，非零数是真。
        return any((self.forward, self.lateral, self.yaw))


@dataclass(frozen=True)
class ControlLimits:
    """本次运行使用的速度上限；单位分别为 m/s、m/s、rad/s。"""

    # 等号右侧是默认值，所以 ControlLimits() 会得到 0.30、0.20、0.50。
    # 用户在界面应用新速度时，会创建另一个 ControlLimits 对象替换它。
    linear: float = 0.30
    lateral: float = 0.20
    yaw: float = 0.50


class DeadmanKeys:
    """键盘“失联保护”：按键事件长时间不更新，就当作已经松开。"""

    def __init__(self, keyboard_timeout: float = 0.45) -> None:
        # self 表示“当前这个 DeadmanKeys 对象”。以下字段是它保存的状态。
        self.keyboard_timeout = keyboard_timeout
        # set 不保存重复元素，适合记录当前有哪些按键被按住。
        self._held: set[str] = set()
        # dict 保存“按键 -> 失效时刻”，例如 {"w": 123.45}。
        self._deadlines: dict[str, float] = {}

    def press(self, key: str, *, now: float, requires_repeat: bool) -> None:
        """记录一次按下事件，并按需设置自动失效时间。"""

        # 参数列表中的 * 表示 now 和 requires_repeat 必须写出参数名，减少传错顺序。
        self._held.add(key)
        if requires_repeat:
            # 键盘长按会不断产生重复事件，每次都把截止时间向后顺延。
            self._deadlines[key] = now + self.keyboard_timeout
        else:
            # 鼠标点击采用“锁存”方式，不依赖键盘重复事件，所以移除截止时间。
            self._deadlines.pop(key, None)

    def release(self, key: str) -> None:
        """记录松键；即使 key 原本不存在也不会报错。"""

        # discard 与 remove 的区别：元素不存在时 discard 不会抛出异常。
        self._held.discard(key)
        # pop(key, None) 同理：找不到 key 时返回 None，不报错。
        self._deadlines.pop(key, None)

    def clear(self) -> None:
        """清空所有按键状态，急停、失焦和断线都会调用它。"""

        self._held.clear()
        self._deadlines.clear()

    def active(self, *, now: float) -> set[str]:
        """删除已经超时的键，并返回仍然有效的按键副本。"""

        # 这是集合推导式：遍历所有截止时间，只收集已经过期的 key。
        expired = {key for key, deadline in self._deadlines.items() if now > deadline}
        for key in expired:
            self.release(key)
        # 返回副本而不是内部 _held，防止调用者意外修改对象内部状态。
        return set(self._held)


def watchdog_velocity(
    desired: Velocity,
    *,
    last_update: float,
    now: float,
    timeout: float,
) -> Velocity:
    """后台看门狗：控制数据过期时强制返回零速度。"""

    # now 和 last_update 都来自 time.monotonic()，相减得到经过的秒数。
    if now - last_update > timeout:
        return Velocity.zero()
    # 数据仍在有效期内，原样返回用户想要的速度。
    return desired


class SafetyPolicy:
    """移动门禁：只有已武装且窗口仍有焦点时才产生非零速度。"""

    def __init__(self, limits: ControlLimits | None = None) -> None:
        # “A | None”表示参数既可以是 A，也可以不传（None）。
        # 未传时创建默认速度上限；传入时使用用户设置的上限。
        self.limits = limits or ControlLimits()
        # 启动时永远处于未武装状态，避免打开程序就能移动。
        self.armed = False

    def arm(self, *, hardware_confirmed: bool) -> bool:
        """只有用户完成硬件安全确认时才允许武装。"""

        # bool(...) 把输入明确转换成 True 或 False。
        self.armed = bool(hardware_confirmed)
        return self.armed

    def disarm(self) -> None:
        """解除武装；解除后 velocity_for 只能返回零速度。"""

        self.armed = False

    def velocity_for(self, keys: set[str], *, focused: bool) -> Velocity:
        """把有效按键转换成速度；任何安全条件不满足都返回停止。"""

        # or 表示任一条件成立就进入分支：未武装或窗口失焦都禁止移动。
        if not self.armed or not focused:
            return Velocity.zero()

        # 表达式 "w" in keys 的结果是 bool；int(False)=0，int(True)=1。
        # 因此 W-S 会得到：只按 W 为 1，只按 S 为 -1，同时按或都不按为 0。
        forward = self.limits.linear * (int("w" in keys) - int("s" in keys))
        lateral = self.limits.lateral * (int("a" in keys) - int("d" in keys))
        yaw = self.limits.yaw * (int("q" in keys) - int("e" in keys))
        # 使用关键字参数写清三个数分别对应哪个方向，避免顺序混淆。
        return Velocity(forward=forward, lateral=lateral, yaw=yaw)
