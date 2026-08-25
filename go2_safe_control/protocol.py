"""把程序内部的动作翻译为 Go2 WebRTC 协议消息。

小白理解：界面只知道“前进”或“坐下”，机器人只认识 API 编号和参数字典。
本模块就是两者之间的“翻译表”，不负责联网，也不负责创建界面。
"""

from __future__ import annotations

import json
# Any 表示“这里可能是任意类型”；协议字典中既有数字，也有嵌套字典。
from typing import Any

# 相对导入前面的点表示“从当前 go2_safe_control 包中导入”。
from .safety import Velocity

# 这些数字来自 Go2 Sport API。集中放在这里，避免散落在界面代码中。
SPORT_API_STOP_MOVE = 1003
SPORT_API_STAND_UP = 1004
SPORT_API_STAND_DOWN = 1005
SPORT_API_MOVE = 1008
# 字典把程序内部动作名映射为 (API 编号, 英文显示名) 二元组。
# 以后增加动作时，优先只改这一张表，避免在各个界面函数里复制数字。
SPORT_ACTION_APIS = {
    "balance_stand": (1002, "BalanceStand"),
    "stand_up": (SPORT_API_STAND_UP, "StandUp"),
    "stand_down": (SPORT_API_STAND_DOWN, "StandDown"),
    "recovery_stand": (1006, "RecoveryStand"),
    "sit": (1009, "Sit"),
    "rise_sit": (1010, "RiseSit"),
    "hello": (1016, "Hello"),
    "stretch": (1017, "Stretch"),
    "heart": (1036, "Heart"),
}
MOTION_SWITCHER_API_GET = 1001
MOTION_SWITCHER_API_SET = 1002


class SportActionResponseError(RuntimeError):
    """Sport 动作已发出，但机器人没有返回可接受的成功响应。"""


def move_options(velocity: Velocity) -> dict[str, Any]:
    """把三轴速度对象转换成 Move(1008) 所需的请求字典。"""

    # WebRTC 库会继续把这个 Python 字典编码成机器人能识别的消息。
    # x/y/z 分别对应前后、横移和转向，和 Velocity 的字段逐一对应。
    return {
        "api_id": SPORT_API_MOVE,
        "parameter": {
            "x": velocity.forward,
            "y": velocity.lateral,
            "z": velocity.yaw,
        },
    }


def stop_options() -> dict[str, int]:
    """生成 StopMove 请求；停止动作没有额外参数。"""

    return {"api_id": SPORT_API_STOP_MOVE}


def stand_down_options() -> dict[str, int]:
    """生成卧趴请求；实际编号从统一动作表读取。"""

    return sport_action_options("stand_down")


def stand_up_options() -> dict[str, int]:
    """生成站起请求；保留这个函数能让调用处更容易读懂。"""

    return sport_action_options("stand_up")


def sport_action_options(action: str) -> dict[str, int]:
    """根据内部动作名生成高层动作请求；未知动作会立即报错。"""

    try:
        # 字典索引在 action 不存在时会抛出 KeyError。
        api_id, _name = SPORT_ACTION_APIS[action]
    except KeyError as exc:
        # from exc 会保留原始错误原因，调试时能看到完整异常链。
        raise ValueError(f"不支持的 Sport 动作：{action}") from exc
    return {"api_id": api_id}


def sport_action_name(action: str) -> str:
    """取得动作的官方英文名称，主要用于界面状态提示。"""

    try:
        _api_id, name = SPORT_ACTION_APIS[action]
    except KeyError as exc:
        raise ValueError(f"不支持的 Sport 动作：{action}") from exc
    return name


def require_sport_action_success(response: object, action_name: str) -> None:
    """检查高层动作 RPC 的状态码；失败时保留机器人给出的错误证据。

    小白理解：网络“能收到回复”不等于动作“执行成功”。机器人会在
    ``status.code`` 中说明是否接受命令，因此这里必须再检查一次。
    """

    try:
        if not isinstance(response, dict):
            raise TypeError
        data = response["data"]
        if not isinstance(data, dict):
            raise TypeError
        header = data["header"]
        if not isinstance(header, dict):
            raise TypeError
        status = header["status"]
        if not isinstance(status, dict):
            raise TypeError
        code = int(status["code"])
        detail = data.get("data", "")
    except (KeyError, TypeError, ValueError) as exc:
        raise SportActionResponseError(
            f"{action_name} 的机器人回复格式无效"
        ) from exc

    if code == 0:
        return

    if code == 3203:
        reason = "当前固件或当前运动模式不支持这个 API"
    else:
        reason = "机器人拒绝执行"
    detail_text = str(detail).strip()
    detail_suffix = f"；机器人信息：{detail_text[:160]}" if detail_text else ""
    raise SportActionResponseError(
        f"{action_name} 失败，错误码 {code}：{reason}{detail_suffix}"
    )


def motion_mode_options() -> dict[str, int]:
    """生成查询当前运动模式的 motion_switcher 请求。"""

    return {"api_id": MOTION_SWITCHER_API_GET}


def normal_motion_mode_options() -> dict[str, Any]:
    """生成把运动模式切换为 normal 的请求。"""

    return {
        "api_id": MOTION_SWITCHER_API_SET,
        "parameter": {"name": "normal"},
    }


def parse_motion_mode(response: object) -> str:
    """从 motion_switcher 的嵌套响应中安全取出模式名。

    外部数据不能假设一定正确，所以每一层都检查类型和错误码。
    """
    if not isinstance(response, dict):
        raise RuntimeError("motion_switcher 返回格式无效")
    try:
        # 外部响应是一层层嵌套的字典。每取出一层，都先验证它的类型。
        data = response["data"]
        if not isinstance(data, dict):
            raise TypeError
        header = data["header"]
        if not isinstance(header, dict):
            raise TypeError
        status = header["status"]
        if not isinstance(status, dict):
            raise TypeError
        code = int(status["code"])
        if code != 0:
            raise RuntimeError(f"motion_switcher 返回错误码 {code}")
        payload = data["data"]
        # 有些固件把内部 data 返回为 JSON 字符串，有些直接返回字典；两种都兼容。
        decoded = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(decoded, dict) or not isinstance(decoded.get("name"), str):
            raise TypeError
        return decoded["name"]
    except RuntimeError:
        # 已经带有明确业务含义的 RuntimeError 不再改写，直接交给上层显示。
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        # 其余解析错误统一包装成用户能理解的消息，同时保留原始异常链。
        raise RuntimeError("motion_switcher 返回格式无效") from exc
