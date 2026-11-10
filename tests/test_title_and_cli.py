import inspect
import runpy
import unittest
from unittest.mock import patch

from go2_safe_control.app import (
    APP_TITLE,
    HEADER_FONT_SIZE,
    HEADER_HEIGHT,
    SafeControlApp,
    window_size_for_screen,
)


class TitleAndCliTests(unittest.TestCase):
    def test_header_uses_requested_single_large_title(self) -> None:
        self.assertEqual(APP_TITLE, "AI故障预警｜升压站智能巡检平台")
        self.assertGreaterEqual(HEADER_FONT_SIZE, 20)
        self.assertLessEqual(HEADER_HEIGHT, 72)
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        self.assertNotIn('text="机器狗安全运动控制器"', ui_source)
        self.assertNotIn("仅提供低速平移和转向", ui_source)

    def test_python_m_package_entry_starts_the_app(self) -> None:
        with patch("go2_safe_control.app.main") as app_main:
            runpy.run_module("go2_safe_control", run_name="__main__")

        app_main.assert_called_once_with()

    def test_window_fits_a_1366_by_768_desktop_and_content_can_scroll(self) -> None:
        width, height = window_size_for_screen(1366, 768)

        self.assertLessEqual(width, 1326)
        self.assertLessEqual(height, 688)
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        self.assertIn("tk.Canvas", ui_source)
        self.assertIn('orient="vertical"', ui_source)

    def test_compact_layout_uses_one_primary_workspace(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)

        self.assertEqual(ui_source.count("ttk.Notebook"), 1)
        self.assertIn('text="安全与姿态"', ui_source)
        self.assertIn('text="运动控制"', ui_source)
        self.assertIn('text="摄像头与识别"', ui_source)
        self.assertIn('text="自动流程"', ui_source)
        self.assertIn('text="错误日志"', ui_source)
        self.assertIn("height=6", ui_source)

    def test_responsive_layout_clamps_scroll_and_resizes_one_workspace(self) -> None:
        resize_source = inspect.getsource(SafeControlApp._resize_page_width)
        tab_resize_source = inspect.getsource(SafeControlApp._resize_active_tab)
        scroll_source = inspect.getsource(SafeControlApp._update_page_scrollregion)

        self.assertIn("_resize_active_tab", resize_source)
        self.assertIn("active_page.winfo_reqheight()", tab_resize_source)
        self.assertIn("self.main_tabs.configure(height=tab_height)", tab_resize_source)
        self.assertNotIn("_apply_responsive_layout", resize_source)
        self.assertIn("yview_moveto(0.0)", scroll_source)
        self.assertIn("max(content_height, viewport_height)", scroll_source)

    def test_connection_details_are_progressively_disclosed(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        toggle_source = inspect.getsource(SafeControlApp._set_connection_details_visible)

        self.assertIn("self.connection_details", ui_source)
        self.assertIn('text="收起连接设置"', ui_source)
        self.assertIn("self.connection_details_host.grid_remove()", toggle_source)
        self.assertIn("self.connection_details_host.grid()", toggle_source)

    def test_control_states_follow_connection_camera_and_arm_state(self) -> None:
        sync_source = inspect.getsource(SafeControlApp._sync_control_states)

        self.assertIn("self.session.connected", sync_source)
        self.assertIn("self.policy.armed", sync_source)
        self.assertIn("self._camera_requested", sync_source)
        self.assertIn("self._person_detection_enabled", sync_source)

    def test_workflow_editor_keeps_duration_and_actions_visible(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)

        self.assertIn('text="时长（秒）"', ui_source)
        self.assertIn("editor.columnconfigure(1, weight=1)", ui_source)
        self.assertIn("editor.columnconfigure(3, weight=1)", ui_source)
        self.assertIn("editor_actions.columnconfigure(column, weight=1)", ui_source)
        self.assertIn("file_actions.columnconfigure(column, weight=1)", ui_source)

    def test_requested_explanatory_text_is_removed(self) -> None:
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        self.assertNotIn("只读预览", ui_source)
        self.assertNotIn("右后腿过热问题", ui_source)
        self.assertNotIn("textvariable=self.speed_status_var", ui_source)
        self.assertNotIn("本控制器允许范围", ui_source)
        self.assertNotIn("电脑断电或进程被强制终止", ui_source)
        self.assertNotIn("textvariable=self.header_activity_var", ui_source)

    def test_professional_light_layout_and_status_feedback_use_local_theme(self) -> None:
        style_source = inspect.getsource(SafeControlApp._configure_styles)
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        animation_source = inspect.getsource(SafeControlApp._update_header_activity)

        self.assertIn("configure_ui_styles", style_source)
        self.assertIn("self.header_canvas", ui_source)
        self.assertIn("self.ui_assets.icon", ui_source)
        self.assertIn('style="Telemetry.TLabel"', ui_source)
        self.assertIn('style="Workspace.TNotebook"', ui_source)
        self.assertIn('connection_slot.pack(fill="x"', ui_source)
        self.assertIn('text="连接与认证"', ui_source)
        self.assertIn("self.workflow_runner.running", animation_source)
        self.assertNotIn("header_activity_var", animation_source)
        self.assertIn("self.activity_progress", animation_source)
        self.assertNotIn("sv_ttk", style_source)

    def test_generated_brand_assets_are_used_without_replacing_button_labels(self) -> None:
        init_source = inspect.getsource(SafeControlApp.__init__)
        ui_source = inspect.getsource(SafeControlApp._build_ui)
        header_source = inspect.getsource(SafeControlApp._resize_header_art)

        self.assertIn("UiAssetLibrary", init_source)
        self.assertIn("root.iconphoto", init_source)
        self.assertIn('image=self.ui_assets.icon("connect")', ui_source)
        self.assertIn('image=self.ui_assets.icon("camera"', ui_source)
        self.assertIn('image=self.ui_assets.icon("workflow"', ui_source)
        self.assertIn("header_background", header_source)
        self.assertIn('text="建立连接"', ui_source)

    def test_micro_interactions_do_not_change_robot_control_timing(self) -> None:
        init_source = inspect.getsource(SafeControlApp.__init__)
        toggle_source = inspect.getsource(SafeControlApp._toggle_connection_details)

        self.assertIn("animate_window_fade_in", init_source)
        self.assertIn("_animate_connection_details", toggle_source)


if __name__ == "__main__":
    unittest.main()
