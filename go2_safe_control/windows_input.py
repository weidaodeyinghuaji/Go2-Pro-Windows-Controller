"""Windows 输入法保护。

武装期间临时关闭当前控制窗口的 IME 并切换英文键盘，避免中文输入法
截获 W/A/S/D/Q/E。解除武装时恢复用户原来的输入环境。
"""

from __future__ import annotations

# ctypes 让 Python 调用 Windows 原生 DLL；本模块没有安装额外第三方库。
import ctypes
# os.name 用来判断当前是不是 Windows，避免其他系统误调用 Win32 API。
import os
# wintypes 提供 HWND、HANDLE、DWORD 等与 Windows C 接口匹配的类型。
from ctypes import wintypes


class WindowsControlInputGuard:
    """只在控制器窗口武装期间临时管理 Windows IME。"""

    # 这是 Windows API 规定的标志位，表示加载键盘布局后立即激活它。
    KLF_ACTIVATE = 0x00000001

    def __init__(self) -> None:
        # 下划线开头表示“类内部使用”，调用者通常不应直接修改。
        self._active = False
        # hwnd 是窗口句柄，可以理解成 Windows 为每个窗口分配的数字身份证。
        self._hwnd = 0
        # 以下两个字段保存用户原来的输入环境，解除武装时用于恢复。
        self._previous_context: int | None = None
        self._previous_layout: int | None = None

    @property
    def active(self) -> bool:
        """告诉界面输入保护当前是否已启用。"""

        return self._active

    def activate(self, hwnd: int) -> None:
        """暂时关闭指定窗口的中文 IME，并激活英文键盘布局。"""

        # 非 Windows 不执行；已经启用时也不重复覆盖“原来的输入环境”。
        if os.name != "nt" or self._active:
            return

        # WinDLL 会加载系统 DLL。use_last_error=True 允许调试 Windows 错误码。
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)

        # argtypes/restype 是在告诉 ctypes：C 函数接收什么、返回什么。
        # 如果不声明，64 位句柄可能被错误地当成普通 32 位整数处理。
        user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        user32.GetKeyboardLayout.restype = wintypes.HANDLE
        user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
        user32.ActivateKeyboardLayout.argtypes = [wintypes.HANDLE, wintypes.UINT]
        user32.ActivateKeyboardLayout.restype = wintypes.HANDLE
        imm32.ImmAssociateContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmAssociateContext.restype = wintypes.HANDLE

        self._hwnd = hwnd
        # 参数 0 表示取得当前线程的键盘布局，并保存起来供恢复使用。
        layout = user32.GetKeyboardLayout(0)
        self._previous_layout = int(layout) if layout else None
        # 把 None 传给 ImmAssociateContext 会解除这个窗口的 IME，并返回旧上下文。
        previous_context = imm32.ImmAssociateContext(hwnd, None)
        self._previous_context = int(previous_context) if previous_context else None
        # 00000409 是 Windows 的英语（美国）键盘布局标识。
        user32.LoadKeyboardLayoutW("00000409", self.KLF_ACTIVATE)
        self._active = True

    def deactivate(self) -> None:
        """恢复 activate() 之前保存的 IME 上下文和键盘布局。"""

        if os.name != "nt" or not self._active:
            return

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        imm32 = ctypes.WinDLL("imm32", use_last_error=True)
        user32.ActivateKeyboardLayout.argtypes = [wintypes.HANDLE, wintypes.UINT]
        user32.ActivateKeyboardLayout.restype = wintypes.HANDLE
        imm32.ImmAssociateContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        imm32.ImmAssociateContext.restype = wintypes.HANDLE

        if self._previous_context is not None:
            imm32.ImmAssociateContext(self._hwnd, self._previous_context)
        if self._previous_layout is not None:
            user32.ActivateKeyboardLayout(self._previous_layout, 0)

        # 恢复完成后清空状态，让下一次武装可以重新保存当时的输入环境。
        self._active = False
        self._hwnd = 0
        self._previous_context = None
        self._previous_layout = None
