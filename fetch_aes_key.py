"""从 Unitree 云端获取当前账号绑定设备的 AES key。

这是独立辅助工具，不参与运动控制。它处理中文 Windows 的 HTTP header、
CA 证书路径和可粘贴密码窗口；密码不会写入文件或命令行历史。
"""

from __future__ import annotations

# sys.argv 保存命令行参数，sys.exit 把返回码交给 .bat/cmd。
import sys
import os
# shutil.copyfile 用于复制 CA 证书，不改动证书内容。
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

import certifi
from unitree_webrtc_connect.unitree_cloud import UnitreeCloud


class PasswordEntryCancelled(RuntimeError):
    """用户主动关闭密码窗口；用独立异常区分“取消”和真正程序错误。"""

    pass


def ascii_utc_offset() -> str:
    """生成只含 ASCII 的时区字符串，例如 UTC+08:00。"""

    # astimezone() 使用 Windows 当前时区，utcoffset() 得到与 UTC 的时间差。
    offset = datetime.now().astimezone().utcoffset()
    total_minutes = 0 if offset is None else int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    # divmod(a, 60) 一次得到“整小时”和“剩余分钟”。
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def install_timezone_header_fix() -> None:
    """让中文 Windows 生成的 HTTP header 仍可被 curl_cffi 编码。"""

    # getattr 的第三个参数 False 是属性不存在时的默认值，用它避免重复打补丁。
    if getattr(UnitreeCloud, "_go2_ascii_timezone_patched", False):
        return

    # 先保存依赖库原方法，包装函数仍然要调用它取得完整 headers。
    original_headers: Callable[[UnitreeCloud], dict] = UnitreeCloud._headers

    def latin1_safe_headers(self: UnitreeCloud) -> dict:
        """只在原时区无法编码时替换 AppTimezone，其他 header 原样保留。"""

        headers = original_headers(self)
        timezone = str(headers.get("AppTimezone", ""))
        try:
            timezone.encode("latin-1")
        except UnicodeEncodeError:
            headers["AppTimezone"] = ascii_utc_offset()
        return headers

    # 运行期替换类方法称为 monkey patch；范围仅限这个 Python 进程。
    UnitreeCloud._headers = latin1_safe_headers  # type: ignore[method-assign]
    UnitreeCloud._go2_ascii_timezone_patched = True  # type: ignore[attr-defined]


def ensure_ascii_ca_bundle() -> str:
    """把 certifi CA 文件复制到固定英文路径，并返回该路径。"""

    source = Path(certifi.where())
    # 环境变量不存在时退回 Windows 临时目录；正常桌面 Windows 都有 LOCALAPPDATA。
    local_app_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Windows\Temp"))
    cache_dir = local_app_data / "Go2WindowsControl"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "certifi-cacert.pem"
    # 主动编码一次是断言：如果将来路径中出现非 ASCII 字符，这里立即报错。
    str(target).encode("ascii")

    # 只有目标不存在或内容变化时才复制，避免每次启动都写磁盘。
    if not target.is_file() or source.read_bytes() != target.read_bytes():
        temporary = cache_dir / "certifi-cacert.tmp"
        shutil.copyfile(source, temporary)
        # 先写临时文件再 replace，可避免中途失败留下半个证书文件。
        temporary.replace(target)
    return str(target)


def install_ca_bundle_fix() -> None:
    """保持 TLS 验证开启，同时避开 curl 对中文 CA 路径的兼容问题。"""
    if getattr(UnitreeCloud, "_go2_ascii_ca_patched", False):
        return

    ca_bundle = ensure_ascii_ca_bundle()
    original_init = UnitreeCloud.__init__

    def init_with_ascii_ca(self: UnitreeCloud, *args: object, **kwargs: object) -> None:
        """先运行依赖库原构造函数，再只替换它使用的 CA 文件路径。"""

        # *args 收集位置参数，**kwargs 收集关键字参数，原样转交给原构造函数。
        original_init(self, *args, **kwargs)
        self._session.verify = ca_bundle

    UnitreeCloud.__init__ = init_with_ascii_ca  # type: ignore[method-assign]
    UnitreeCloud._go2_ascii_ca_patched = True  # type: ignore[attr-defined]


def prompt_password_gui(email: str) -> str | None:
    """显示可粘贴的本地密码窗口；确认返回密码，取消返回 None。"""

    # 放在函数内导入，只有真正需要密码窗口时才加载 Tkinter。
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Go2 AES 密钥 - 输入 Unitree 密码")
    root.geometry("500x250")
    root.resizable(False, False)

    password_var = tk.StringVar()
    show_var = tk.BooleanVar(value=False)
    # 内部回调需要把结果带到 mainloop() 结束之后；用列表充当可变容器。
    result: list[str] = []

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"账号：{email}").pack(anchor="w")
    ttk.Label(frame, text="可按 Ctrl+V 或点击“粘贴”；圆点数量表示已经输入。")\
        .pack(anchor="w", pady=(8, 6))
    entry = ttk.Entry(frame, textvariable=password_var, show="●", width=48)
    entry.pack(fill="x")
    status = ttk.Label(frame, text="尚未输入密码")
    status.pack(anchor="w", pady=(6, 8))

    def update_status(*_args: object) -> None:
        """输入内容变化时只显示字符数，不把密码显示到日志。"""

        length = len(password_var.get())
        status.configure(text=f"已输入 {length} 个字符" if length else "尚未输入密码")

    def toggle_visibility() -> None:
        """根据复选框决定显示原文还是圆点。"""

        entry.configure(show="" if show_var.get() else "●")

    def paste_password() -> None:
        """从 Windows 剪贴板粘贴，并把光标移动到末尾。"""

        try:
            password_var.set(root.clipboard_get())
        except tk.TclError:
            messagebox.showwarning("无法粘贴", "剪贴板里没有可用文本。", parent=root)
        entry.focus_set()
        entry.icursor("end")

    def accept() -> None:
        """非空时保存到当前进程内存并关闭窗口。"""

        password = password_var.get()
        if not password:
            messagebox.showwarning("密码为空", "请先输入或粘贴密码。", parent=root)
            return
        result.append(password)
        root.destroy()

    def cancel() -> None:
        root.destroy()

    # trace_add 让 StringVar 每次被输入或粘贴修改后自动调用 update_status。
    password_var.trace_add("write", update_status)
    options = ttk.Frame(frame)
    options.pack(fill="x")
    ttk.Checkbutton(
        options,
        text="显示密码",
        variable=show_var,
        command=toggle_visibility,
    ).pack(side="left")
    ttk.Button(options, text="粘贴", command=paste_password).pack(side="right")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(18, 0))
    ttk.Button(buttons, text="取消", command=cancel).pack(side="right")
    ttk.Button(buttons, text="确认", command=accept).pack(side="right", padx=(0, 8))

    root.bind("<Return>", lambda _event: accept())
    root.bind("<Escape>", lambda _event: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    entry.focus_set()
    # mainloop 阻塞在此处理窗口事件，直到 accept/cancel 调用 root.destroy()。
    root.mainloop()
    return result[0] if result else None


def prepare_cli_args(
    argv: list[str],
    *,
    prompt: Callable[[str], str | None] = prompt_password_gui,
) -> list[str]:
    """需要邮箱密码且命令行未提供时，安全地补上密码窗口结果。"""

    # 复制列表，避免函数意外修改调用者持有的 argv。
    args = list(argv)
    has_password = "--password" in args or any(
        item.startswith("--password=") for item in args
    )
    if "--email" not in args or "--token" in args or has_password:
        return args

    email_index = args.index("--email") + 1
    if email_index >= len(args):
        return args
    password = prompt(args[email_index])
    if password is None:
        raise PasswordEntryCancelled
    # [*args, ...] 创建新列表，不会把密码写回原 argv 对象。
    return [*args, "--password", password]


def main(argv: list[str] | None = None) -> int:
    """安装两个 Windows 兼容补丁，再把参数交给依赖库官方 CLI。"""

    install_timezone_header_fix()
    install_ca_bundle_fix()
    from unitree_webrtc_connect._cli import main as library_main

    # sys.argv[0] 是脚本名，所以真正参数从下标 1 开始。
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        prepared_args = prepare_cli_args(raw_args)
    except PasswordEntryCancelled:
        print("已取消密码输入。")
        return 130
    if "--password" in prepared_args and "--password" not in raw_args:
        print("已从密码窗口接收输入；密码不会显示或保存。")
    return library_main(prepared_args)


if __name__ == "__main__":
    # sys.exit 会把 main 返回的整数变成进程退出码，供批处理判断成功或失败。
    sys.exit(main())
