from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "scripts" / "install.ps1"
PACKAGER = PROJECT_ROOT / "scripts" / "build_share.ps1"


class PortableDistributionTests(unittest.TestCase):
    def test_installer_keeps_a_working_venv_created_in_the_same_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(
                [str(Path(sys._base_executable)), "-m", "venv", str(root / ".venv")],
                check=True,
                capture_output=True,
                timeout=60,
            )
            sentinel = root / ".venv" / "keep-me.txt"
            sentinel.write_text("local environment\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALLER),
                    "-ProjectRoot",
                    str(root),
                    "-SkipDependencies",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(sentinel.is_file(), "a valid local venv was unexpectedly rebuilt")

    def test_installer_replaces_a_copied_broken_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied_installer = root / "scripts" / "install.ps1"
            copied_installer.parent.mkdir()
            shutil.copy2(INSTALLER, copied_installer)
            scripts = root / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(Path(sys.executable), scripts / "python.exe")
            (root / ".venv" / "pyvenv.cfg").write_text(
                "home = C:\\Users\\original-user\\AppData\\Local\\Programs\\Python\\Python311\n"
                "include-system-site-packages = false\n"
                "version = 3.11.9\n"
                "executable = C:\\Users\\original-user\\AppData\\Local\\Programs\\Python\\Python311\\python.exe\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(copied_installer),
                    "-PythonExecutable",
                    str(Path(sys._base_executable)),
                    "-SkipDependencies",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            python = root / ".venv" / "Scripts" / "python.exe"
            probe = subprocess.run(
                [str(python), "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            config = (root / ".venv" / "pyvenv.cfg").read_text(
                encoding="utf-8", errors="replace"
            )
            self.assertIn(str(root), config)
            self.assertTrue((root / ".venv" / ".go2-setup.json").is_file())

    def test_share_zip_excludes_machine_specific_and_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            copied_packager = root / "scripts" / "build_share.ps1"
            copied_packager.parent.mkdir()
            shutil.copy2(PACKAGER, copied_packager)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "01_安装环境_双击.bat").write_text("install\n", encoding="utf-8")
            (root / ".venv" / "Scripts").mkdir(parents=True)
            (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"local")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "main.pyc").write_bytes(b"cache")
            (root / "debug.log").write_text("secret-ish log\n", encoding="utf-8")
            output = Path(temporary) / "share.zip"

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(copied_packager),
                    "-OutputPath",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("/main.py") for name in names))
            self.assertFalse(any("/.venv/" in name for name in names))
            self.assertFalse(any("/__pycache__/" in name for name in names))
            self.assertFalse(any(name.endswith(".log") for name in names))


if __name__ == "__main__":
    unittest.main()
