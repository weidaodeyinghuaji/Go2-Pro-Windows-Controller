"""控制台的视觉令牌、Ttk 样式和轻量界面动画。"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class UiColors:
    """统一保存界面颜色，避免控件中散落难以维护的色值。"""

    background: str = "#F3F5F7"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#F8FAFC"
    header: str = "#172033"
    text: str = "#1F2937"
    muted: str = "#667085"
    border: str = "#D8DEE6"
    primary: str = "#2563EB"
    primary_hover: str = "#1D4ED8"
    primary_soft: str = "#EAF1FF"
    danger: str = "#C62828"
    danger_hover: str = "#A91F1F"
    warning: str = "#E99A19"
    warning_hover: str = "#D4860C"
    success: str = "#15803D"
    disabled: str = "#98A2B3"
    camera: str = "#111827"


COLORS = UiColors()
UI_FONT = "Microsoft YaHei UI"
UI_MONO_FONT = "Consolas"
MOTION_DURATION_MS = 210


def ease_out_cubic(progress: float) -> float:
    """返回自然减速曲线；输入会被限制在 0 到 1。"""

    clamped = min(1.0, max(0.0, progress))
    return 1.0 - (1.0 - clamped) ** 3


def configure_ui_styles(root: tk.Tk, *, header_font_size: int) -> ttk.Style:
    """为所有 Ttk 控件建立统一、克制的 Windows 浅色视觉系统。"""

    root.configure(background=COLORS.background)
    style = ttk.Style(root)
    # clam 是 Tk 自带且可完整着色的主题，不引入运行依赖，也不受各电脑主题差异影响。
    if "clam" in style.theme_names():
        style.theme_use("clam")
        root.update_idletasks()

    style.configure(
        ".",
        font=(UI_FONT, 10),
        background=COLORS.surface,
        foreground=COLORS.text,
        bordercolor=COLORS.border,
        lightcolor=COLORS.border,
        darkcolor=COLORS.border,
        troughcolor=COLORS.surface_subtle,
        focuscolor=COLORS.primary,
    )
    style.configure("App.TFrame", background=COLORS.background)
    style.configure("Surface.TFrame", background=COLORS.surface)
    style.configure("Header.TFrame", background=COLORS.header)
    style.configure("Raised.TFrame", background=COLORS.surface_subtle)
    style.configure("Accent.TFrame", background=COLORS.primary)

    style.configure(
        "Panel.TLabelframe",
        background=COLORS.surface,
        bordercolor=COLORS.border,
        lightcolor=COLORS.border,
        darkcolor=COLORS.border,
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "Panel.TLabelframe.Label",
        background=COLORS.surface,
        foreground=COLORS.text,
        font=(UI_FONT, 11, "bold"),
        padding=(5, 0),
    )
    style.configure("TLabel", background=COLORS.surface, foreground=COLORS.text)
    style.configure("Muted.TLabel", background=COLORS.surface, foreground=COLORS.muted)
    style.configure(
        "HeaderTitle.TLabel",
        background=COLORS.header,
        foreground="#FFFFFF",
        font=(UI_FONT, header_font_size, "bold"),
    )
    style.configure(
        "HeaderMeta.TLabel",
        background=COLORS.header,
        foreground="#B8C2D4",
        font=(UI_FONT, 9),
    )
    style.configure(
        "Telemetry.TLabel",
        background=COLORS.primary_soft,
        foreground=COLORS.primary_hover,
        font=(UI_MONO_FONT, 12, "bold"),
        padding=(12, 8),
        relief="flat",
        borderwidth=1,
    )
    style.configure(
        "Status.TLabel",
        background=COLORS.surface_subtle,
        foreground=COLORS.text,
        padding=(10, 8),
        relief="flat",
        borderwidth=1,
    )
    style.configure(
        "Camera.TLabel",
        background=COLORS.camera,
        foreground="#DDE5F0",
        padding=8,
        relief="flat",
        anchor="center",
    )
    style.configure("DangerText.TLabel", background=COLORS.surface, foreground=COLORS.danger)
    style.configure("Path.TLabel", background=COLORS.surface, foreground=COLORS.muted)

    style.configure("TEntry", fieldbackground=COLORS.surface, padding=7, relief="flat")
    style.configure("TCombobox", fieldbackground=COLORS.surface, padding=6)
    style.configure("TSpinbox", fieldbackground=COLORS.surface, padding=5)
    style.configure("TCheckbutton", background=COLORS.surface, padding=6)
    style.map(
        "TCheckbutton",
        background=[("active", COLORS.surface)],
        foreground=[("disabled", COLORS.disabled)],
    )

    common_button = {
        "padding": (14, 9),
        "font": (UI_FONT, 10, "bold"),
        "relief": "flat",
        "borderwidth": 1,
    }
    style.configure(
        "Secondary.TButton",
        background=COLORS.surface,
        foreground=COLORS.text,
        **common_button,
    )
    style.map(
        "Secondary.TButton",
        background=[
            ("disabled", COLORS.surface_subtle),
            ("pressed", "#E4E9F0"),
            ("active", "#EDF1F5"),
        ],
        foreground=[("disabled", COLORS.disabled)],
    )
    style.configure(
        "Toolbar.TButton",
        background=COLORS.surface_subtle,
        foreground=COLORS.muted,
        padding=(11, 7),
        relief="flat",
        borderwidth=1,
    )
    style.map(
        "Toolbar.TButton",
        background=[("pressed", "#E4E9F0"), ("active", "#EDF1F5")],
        foreground=[("active", COLORS.text), ("disabled", COLORS.disabled)],
    )
    style.configure(
        "Primary.TButton",
        background=COLORS.primary,
        foreground="#FFFFFF",
        **common_button,
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", "#A8BDE8"),
            ("pressed", "#1B46A3"),
            ("active", COLORS.primary_hover),
        ],
        foreground=[("disabled", "#EEF3FF")],
    )
    style.configure(
        "Motion.TButton",
        background=COLORS.surface,
        foreground=COLORS.text,
        padding=(16, 12),
        font=(UI_FONT, 10, "bold"),
        relief="flat",
        borderwidth=1,
    )
    style.map(
        "Motion.TButton",
        background=[
            ("disabled", COLORS.surface_subtle),
            ("pressed", COLORS.primary_soft),
            ("active", "#F0F5FF"),
        ],
        foreground=[("pressed", COLORS.primary_hover), ("disabled", COLORS.disabled)],
    )
    style.configure(
        "Warning.TButton",
        background=COLORS.warning,
        foreground="#2F2108",
        **common_button,
    )
    style.map(
        "Warning.TButton",
        background=[
            ("disabled", "#E8D2AD"),
            ("pressed", "#C77808"),
            ("active", COLORS.warning_hover),
        ],
    )
    style.configure(
        "Danger.TButton",
        background=COLORS.danger,
        foreground="#FFFFFF",
        **common_button,
    )
    style.map(
        "Danger.TButton",
        background=[
            ("disabled", "#D8A4A4"),
            ("pressed", "#8E1818"),
            ("active", COLORS.danger_hover),
        ],
        foreground=[("disabled", "#FFF5F5")],
    )

    style.configure(
        "Workspace.TNotebook",
        background=COLORS.background,
        borderwidth=0,
        tabmargins=(0, 0, 0, 0),
    )
    style.configure(
        "Workspace.TNotebook.Tab",
        background=COLORS.background,
        foreground=COLORS.muted,
        padding=(16, 10),
        font=(UI_FONT, 10, "bold"),
        borderwidth=0,
    )
    style.map(
        "Workspace.TNotebook.Tab",
        background=[("selected", COLORS.surface), ("active", "#E9EDF2")],
        foreground=[("selected", COLORS.primary_hover), ("active", COLORS.text)],
        padding=[("selected", (16, 10))],
    )

    style.configure(
        "Treeview",
        background=COLORS.surface,
        fieldbackground=COLORS.surface,
        foreground=COLORS.text,
        bordercolor=COLORS.border,
        rowheight=30,
        relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS.primary_soft)],
        foreground=[("selected", COLORS.primary_hover)],
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS.surface_subtle,
        foreground=COLORS.text,
        font=(UI_FONT, 10, "bold"),
        padding=(7, 8),
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", "#EDF1F5")])
    style.configure(
        "Header.Horizontal.TProgressbar",
        background="#78A7FF",
        troughcolor="#29344A",
        bordercolor="#29344A",
        lightcolor="#78A7FF",
        darkcolor="#78A7FF",
        thickness=6,
    )
    return style


def animate_window_fade_in(
    root: tk.Tk,
    *,
    duration_ms: int = MOTION_DURATION_MS,
    frames: int = 12,
) -> None:
    """让主窗口轻柔显现；不阻塞事件循环，也不影响机器人控制心跳。"""

    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        return

    frame_delay = max(1, duration_ms // max(1, frames))
    pending_after: dict[str, str | None] = {"id": None}

    def cancel_pending(event: tk.Event[tk.Misc]) -> None:
        if event.widget is not root or pending_after["id"] is None:
            return
        try:
            root.after_cancel(pending_after["id"])
        except tk.TclError:
            pass
        pending_after["id"] = None

    destroy_binding = root.bind("<Destroy>", cancel_pending, add="+")

    def render(frame: int) -> None:
        try:
            progress = ease_out_cubic(frame / max(1, frames))
            root.attributes("-alpha", progress)
        except tk.TclError:
            return
        if frame < frames:
            pending_after["id"] = root.after(frame_delay, render, frame + 1)
            return
        pending_after["id"] = None
        if destroy_binding:
            root.unbind("<Destroy>", destroy_binding)

    render(1)
