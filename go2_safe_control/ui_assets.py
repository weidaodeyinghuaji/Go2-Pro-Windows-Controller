"""主界面图片与图标资源的统一加载、缩放和缓存。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps, ImageTk


ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "ui"
ICON_ROOT = ASSET_ROOT / "icons"

ICON_FILES = {
    "camera": "camera.png",
    "connect": "connect.png",
    "disconnect": "disconnect.png",
    "emergency": "emergency.png",
    "error_log": "error_log.png",
    "lie_down": "lie_down.png",
    "motion": "motion.png",
    "person": "person.png",
    "record_start": "record_start.png",
    "record_stop": "record_stop.png",
    "refresh": "refresh.png",
    "save": "save.png",
    "settings": "settings.png",
    "stand": "stand.png",
    "workflow": "workflow.png",
}


def ui_asset_path(name: str) -> Path:
    """返回源码运行与 PyInstaller onedir 均可使用的 UI 资源路径。"""

    return ASSET_ROOT / name

class UiAssetLibrary:
    """按需生成 Tk 图片并保持引用，防止 PhotoImage 被垃圾回收。"""

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self._photos: dict[tuple[object, ...], ImageTk.PhotoImage] = {}
        self._main_background_keys: list[tuple[object, ...]] = []

    def icon(self, name: str, *, size: int = 20) -> ImageTk.PhotoImage:
        """读取一个透明功能图标，并缩放到统一尺寸。"""

        if name not in ICON_FILES:
            raise KeyError(f"未知界面图标：{name}")
        key = ("icon", name, size)
        if key not in self._photos:
            with Image.open(ICON_ROOT / ICON_FILES[name]) as source:
                image = ImageOps.contain(
                    source.convert("RGBA"),
                    (size, size),
                    Image.Resampling.LANCZOS,
                )
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            canvas.alpha_composite(
                image,
                ((size - image.width) // 2, (size - image.height) // 2),
            )
            self._photos[key] = ImageTk.PhotoImage(canvas, master=self.master)
        return self._photos[key]

    def app_icon(self, *, size: int = 64) -> ImageTk.PhotoImage:
        """返回应用窗口图标。"""

        key = ("app_icon", size)
        if key not in self._photos:
            with Image.open(ui_asset_path("app_icon.png")) as source:
                image = ImageOps.contain(
                    source.convert("RGBA"),
                    (size, size),
                    Image.Resampling.LANCZOS,
                )
            self._photos[key] = ImageTk.PhotoImage(image, master=self.master)
        return self._photos[key]

    def main_background(self, width: int, height: int) -> ImageTk.PhotoImage:
        """把主界面背景按当前可见区域缩放，完整覆盖且不改变控件层级。"""

        width = max(1, width)
        height = max(1, height)
        # 拖动窗口时按 24px 档位缓存，避免每变化一个像素就重新生成图片。
        cached_width = max(24, round(width / 24) * 24)
        cached_height = max(24, round(height / 24) * 24)
        key = ("main_background", cached_width, cached_height)
        if key not in self._photos:
            with Image.open(ui_asset_path("main_background.png")) as source:
                image = ImageOps.fit(
                    source.convert("RGBA"),
                    (cached_width, cached_height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
            self._photos[key] = ImageTk.PhotoImage(image, master=self.master)
            self._main_background_keys.append(key)
            # 只保留最近的尺寸；拖动窗口不会让大尺寸 PhotoImage 无限累积。
            while len(self._main_background_keys) > 12:
                expired_key = self._main_background_keys.pop(0)
                self._photos.pop(expired_key, None)
        return self._photos[key]

    def header_background(self, width: int, height: int) -> ImageTk.PhotoImage:
        """将主背景与透明机械线稿组合成适合窄标题栏的品牌横幅。"""

        width = max(1, width)
        height = max(1, height)
        # 宽度按 16px 档位缓存，窗口拖动时不会为每个像素重复生成大图。
        cached_width = max(16, round(width / 16) * 16)
        key = ("header", cached_width, height)
        if key in self._photos:
            return self._photos[key]

        with Image.open(ui_asset_path("main_background.png")) as source:
            background = ImageOps.fit(
                source.convert("RGB"),
                (cached_width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.70, 0.59),
            ).convert("RGBA")
        background = ImageEnhance.Contrast(background).enhance(0.90)

        # 标题一侧覆盖柔和白雾，保证黑色标题在不同屏幕上仍清晰。
        veil = Image.new("RGBA", background.size, (255, 255, 255, 0))
        veil_pixels = veil.load()
        fade_end = max(1, int(cached_width * 0.72))
        for x in range(fade_end):
            alpha = round(222 * (1.0 - x / fade_end) + 54 * (x / fade_end))
            for y in range(height):
                veil_pixels[x, y] = (255, 255, 255, alpha)
        background = Image.alpha_composite(background, veil)

        self._photos[key] = ImageTk.PhotoImage(background, master=self.master)
        return self._photos[key]
