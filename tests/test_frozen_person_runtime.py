"""交付 EXE 前要真的识别一张站立者图片，而不是只确认模型可导入。"""

import tempfile
import unittest
from pathlib import Path

from main import _check_person_runtime


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "standing_person_cc0.jpg"


class FrozenPersonRuntimeTests(unittest.TestCase):
    def test_smoke_check_detects_standing_person_at_preserved_frame_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "result.txt"
            status = _check_person_runtime(report, FIXTURE)
            self.assertEqual(status, 0, report.read_text(encoding="utf-8"))
            self.assertIn("standing=", report.read_text(encoding="utf-8"))
            self.assertNotIn("standing=0", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
