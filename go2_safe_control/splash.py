"""程序启动时显示的品牌欢迎页。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageTk


SPLASH_DURATION_MS = 1800
SPLASH_BACKGROUND = "#eef3f8"
SPLASH_MAX_WIDTH = 760
SPLASH_SCREEN_MARGIN = 80
SPLASH_BRAND = "AI故障预警"
SPLASH_SUBTITLE = "升压站智能巡检平台"
SPLASH_FEATURES = "运动控制  ·  视觉识别  ·  自动流程  ·  设备调试"
SPLASH_STATUS = "正在初始化系统…"
SPLASH_TEXT = "#14243a"
SPLASH_MUTED_TEXT = "#60748d"
SPLASH_PRIMARY = "#2f6fed"


def splash_image_path() -> Path:
    """返回源码运行和 PyInstaller onedir 均可使用的图片路径。"""

    return Path(__file__).resolve().parents[1] / "assets" / "splash.png"


def splash_window_geometry(
    screen_width: int,
    screen_height: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """计算类似 PyCharm 的小型居中启动窗口尺寸与位置。"""

    if min(screen_width, screen_height, image_width, image_height) <= 0:
        raise ValueError("屏幕和启动图片尺寸必须为正数。")
    available_width = max(1, screen_width - SPLASH_SCREEN_MARGIN)
    available_height = max(1, screen_height - SPLASH_SCREEN_MARGIN)
    scale = min(
        1.0,
        SPLASH_MAX_WIDTH / image_width,
        available_width / image_width,
        available_height / image_height,
    )
    width = max(1, round(image_width * scale))
    height = max(1, round(image_height * scale))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y


class SplashScreen:
    """在同一个 Tk 根窗口中显示图片，并在准备完成后切换到主界面。"""

    def __init__(
        self,
        root: tk.Tk,
        on_complete: Callable[[], None],
        *,
        duration_ms: int = SPLASH_DURATION_MS,
    ) -> None:
        self.root = root
        self._on_complete = on_complete
        self._finished = False

        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        root.withdraw()
        root.overrideredirect(True)
        root.configure(background=SPLASH_BACKGROUND)

        with Image.open(splash_image_path()) as source:
            width, height, x, y = splash_window_geometry(
                screen_width,
                screen_height,
                source.width,
                source.height,
            )
            background = source.convert("RGB").resize(
                (width, height),
                Image.Resampling.LANCZOS,
            )
        root.geometry(f"{width}x{height}+{x}+{y}")
        self._photo = ImageTk.PhotoImage(background, master=root)
        self._canvas = tk.Canvas(
            root,
            width=width,
            height=height,
            background=SPLASH_BACKGROUND,
            borderwidth=0,
            highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.create_image(0, 0, image=self._photo, anchor="nw")

        # 背景图只负责机器人和工程氛围，品牌文字由程序绘制。这样可以避免
        # 生图产生错字，也方便以后只改名称而不用重新制作整张图片。
        text_center_x = round(width * 0.28)
        self._canvas.create_text(
            text_center_x,
            round(height * 0.34),
            text=SPLASH_BRAND,
            fill=SPLASH_TEXT,
            font=("Microsoft YaHei UI", max(24, round(width * 0.043)), "bold"),
            anchor="center",
        )
        self._canvas.create_text(
            text_center_x,
            round(height * 0.45),
            text=SPLASH_SUBTITLE,
            fill=SPLASH_MUTED_TEXT,
            font=("Microsoft YaHei UI", max(11, round(width * 0.018))),
            anchor="center",
        )
        self._canvas.create_line(
            round(width * 0.10),
            round(height * 0.54),
            round(width * 0.46),
            round(height * 0.54),
            fill="#c9d5e3",
            width=1,
        )
        self._canvas.create_text(
            text_center_x,
            round(height * 0.64),
            text=SPLASH_FEATURES,
            fill=SPLASH_MUTED_TEXT,
            font=("Microsoft YaHei UI", max(9, round(width * 0.013))),
            anchor="center",
        )
        self._canvas.create_text(
            text_center_x,
            round(height * 0.78),
            text=SPLASH_STATUS,
            fill=SPLASH_MUTED_TEXT,
            font=("Microsoft YaHei UI", max(9, round(width * 0.014))),
            anchor="center",
        )

        self._progress_start_x = round(width * 0.18)
        self._progress_end_x = round(width * 0.38)
        progress_y = round(height * 0.85)
        self._canvas.create_line(
            self._progress_start_x,
            progress_y,
            self._progress_end_x,
            progress_y,
            fill="#cbd7e5",
            width=3,
        )
        self._progress_line = self._canvas.create_line(
            self._progress_start_x,
            progress_y,
            self._progress_start_x,
            progress_y,
            fill=SPLASH_PRIMARY,
            width=3,
        )
        self._progress_y = progress_y
        self._progress_value = 0.0
        self._progress_after_id: str | None = None
        self._animate_progress()

        self._escape_binding = root.bind("<Escape>", self._close, add="+")
        root.deiconify()
        root.lift()
        root.after(duration_ms, self.finish)

    def _animate_progress(self) -> None:
        """让启动进度线平缓推进；它只影响显示，不控制真实初始化流程。"""

        if self._finished or not self.root.winfo_exists():
            return
        self._progress_value = min(0.94, self._progress_value + 0.028)
        current_x = self._progress_start_x + round(
            (self._progress_end_x - self._progress_start_x) * self._progress_value
        )
        self._canvas.coords(
            self._progress_line,
            self._progress_start_x,
            self._progress_y,
            current_x,
            self._progress_y,
        )
        self._progress_after_id = self.root.after(45, self._animate_progress)

    def _close(self, _event: object = None) -> str:
        """允许用户在启动阶段按 Esc 直接关闭程序。"""

        self._finished = True
        self.root.destroy()
        return "break"

    def finish(self) -> None:
        """清理欢迎页，并在当前根窗口中创建控制台。"""

        if self._finished or not self.root.winfo_exists():
            return
        self._finished = True
        if self._progress_after_id:
            self.root.after_cancel(self._progress_after_id)
            self._progress_after_id = None
        if self._escape_binding:
            self.root.unbind("<Escape>", self._escape_binding)
        self._canvas.destroy()
        self.root.overrideredirect(False)
        self.root.resizable(True, True)
        self._on_complete()
        self.root.deiconify()
        self.root.lift()


def show_splash(
    root: tk.Tk,
    on_complete: Callable[[], None],
    *,
    duration_ms: int = SPLASH_DURATION_MS,
) -> SplashScreen:
    """创建欢迎页；返回实例以便测试或调用方保留引用。"""

    return SplashScreen(root, on_complete, duration_ms=duration_ms)
