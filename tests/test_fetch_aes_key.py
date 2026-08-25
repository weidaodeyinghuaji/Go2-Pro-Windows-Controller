import unittest
from pathlib import Path

from fetch_aes_key import (
    install_ca_bundle_fix,
    install_timezone_header_fix,
    prepare_cli_args,
)
from unitree_webrtc_connect.unitree_cloud import UnitreeCloud


class FetchAesKeyTests(unittest.TestCase):
    def test_cloud_headers_are_latin1_safe_on_chinese_windows(self) -> None:
        install_timezone_header_fix()

        headers = UnitreeCloud(region="cn", device_type="Go2")._headers()

        self.assertRegex(headers["AppTimezone"], r"^UTC[+-]\d{2}:\d{2}$")
        for value in headers.values():
            str(value).encode("latin-1")

    def test_cloud_uses_ascii_ca_path_without_disabling_verification(self) -> None:
        install_ca_bundle_fix()

        cloud = UnitreeCloud(region="cn", device_type="Go2")

        verify = cloud._session.verify
        self.assertIsInstance(verify, str)
        str(verify).encode("ascii")
        self.assertTrue(Path(str(verify)).is_file())

    def test_password_prompt_result_is_passed_to_library_cli(self) -> None:
        args = prepare_cli_args(
            ["--email", "user@example.com", "--region", "cn"],
            prompt=lambda _email: "copied-secret",
        )

        self.assertEqual(args[-2:], ["--password", "copied-secret"])


if __name__ == "__main__":
    unittest.main()
