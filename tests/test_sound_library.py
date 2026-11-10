import tempfile
import unittest
import wave
from pathlib import Path

from go2_safe_control.sound_library import SoundLibrary, robot_audio_name


class SoundLibraryTests(unittest.TestCase):
    def test_builtin_sound_is_available_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            library = SoundLibrary(Path(directory) / "audio")
            sounds = library.list_sounds()
            self.assertEqual(len(sounds), 1)
            self.assertEqual(sounds[0].name, "内置提示音")
            self.assertGreater(sounds[0].duration, 0)
            self.assertEqual(library.get(sounds[0].asset_id), sounds[0])

    def test_import_deduplicates_and_saved_flow_assets_are_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "提示.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(bytes(22050 * 2))
            first = SoundLibrary(root / "first")
            sound = first.import_file(source)
            same = first.import_file(source)
            self.assertEqual(sound.asset_id, same.asset_id)
            self.assertEqual(len(robot_audio_name(sound.asset_id)), 27)
            assets = root / "flow_声音素材"
            first.export_for_workflow({sound.asset_id}, assets)
            second = SoundLibrary(root / "second")
            second.import_workflow_assets({sound.asset_id}, assets)
            self.assertEqual(second.get(sound.asset_id).name, "提示")

    def test_missing_or_corrupted_sound_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            library = SoundLibrary(Path(directory))
            sound = library.list_sounds()[0]
            sound.path.write_bytes(b"damaged")
            with self.assertRaisesRegex(ValueError, "损坏"):
                library.get(sound.asset_id)
            with self.assertRaises(ValueError):
                library.import_workflow_assets({"x" * 64}, Path(directory) / "missing")
