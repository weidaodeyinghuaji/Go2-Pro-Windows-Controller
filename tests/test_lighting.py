import unittest

from go2_safe_control.lighting import light_options, require_light_success


class LightingTests(unittest.TestCase):
    def test_vui_red_flash_and_green_white_use_color_api_not_volume_api(self) -> None:
        self.assertEqual(
            light_options("light_red_flash", 8),
            {"api_id": 1007, "parameter": {"color": "red", "time": 8, "flash_cycle": 1000}},
        )
        self.assertEqual(light_options("light_green", 3)["parameter"]["color"], "green")
        self.assertEqual(light_options("light_white", 2)["parameter"]["color"], "white")

    def test_rejected_reply_is_not_treated_as_lamp_success(self) -> None:
        require_light_success({"data": {"header": {"status": {"code": 0}}}})
        with self.assertRaisesRegex(RuntimeError, "错误码 3203"):
            require_light_success({"data": {"header": {"status": {"code": 3203}}}})
        with self.assertRaisesRegex(RuntimeError, "回复格式无效"):
            require_light_success({})


if __name__ == "__main__":
    unittest.main()
