"""Go2 头部 VUI 指示灯请求。灯光只是提示，不能代替实体急停。"""

from __future__ import annotations


# 社区 WebRTC 示例通过 VUI api_id=1007 设置颜色；1003 是音量，不能混用。
LIGHT_SPECS: dict[str, tuple[str, int | None]] = {
    "light_red": ("red", None),
    "light_red_flash": ("red", 1000),
    "light_green": ("green", None),
    "light_white": ("white", None),
}
LIGHT_ACTIONS = frozenset(LIGHT_SPECS)


def light_options(action: str, seconds: int) -> dict[str, object]:
    """生成有期限的灯光命令；到期后不假定机器人恢复成什么颜色。"""

    if action not in LIGHT_SPECS:
        raise ValueError(f"不支持的灯光动作：{action}")
    if not isinstance(seconds, int) or not 1 <= seconds <= 30:
        raise ValueError("灯光时长必须是 1 到 30 的整数秒")
    color, flash_cycle = LIGHT_SPECS[action]
    if flash_cycle is not None and seconds < 2:
        raise ValueError("闪烁灯光至少持续 2 秒")
    parameter: dict[str, object] = {"color": color, "time": seconds}
    if flash_cycle is not None:
        parameter["flash_cycle"] = flash_cycle
    return {"api_id": 1007, "parameter": parameter}


def require_light_success(response: object) -> None:
    """网络有回复不等于指令成功，必须检查机器人返回的状态码。"""

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
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("灯光指令的机器人回复格式无效") from exc
    if code != 0:
        raise RuntimeError(f"灯光指令被机器人拒绝，错误码 {code}")
