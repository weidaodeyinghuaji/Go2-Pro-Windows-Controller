import asyncio
import base64
import io
import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from go2_safe_control.audio_playback import (
    audio_entries,
    find_uploaded_uuid,
    prepare_wav,
    require_audio_success,
    upload_blocks,
)
from go2_safe_control.session import RobotSession
from go2_safe_control.sound_library import SoundLibrary, robot_audio_name


def reply(payload=None, code=0):
    return {"data": {"header": {"status": {"code": code}}, "data": payload or {}}}


class AudioFileTests(unittest.TestCase):
    def test_wav_is_normalized_without_changing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(bytes(22050 * 2))
            original = source.read_bytes()
            converted = prepare_wav(source)
            with wave.open(io.BytesIO(converted), "rb") as result:
                self.assertEqual(result.getframerate(), 44100)
                self.assertEqual(result.getnchannels(), 1)
                self.assertAlmostEqual(result.getnframes(), 44100, delta=64)
            self.assertEqual(source.read_bytes(), original)

    def test_invalid_extension_and_robot_error_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_wav(Path("not_audio.txt"))
        with self.assertRaisesRegex(RuntimeError, "3203"):
            require_audio_success(reply(code=3203), "播放")

    def test_chunks_reassemble_and_uploaded_record_is_identified(self):
        data = b"RIFF" + bytes(10000)
        chunks = list(upload_blocks(data, "yufeng_test"))
        self.assertEqual(
            base64.b64decode("".join(chunk["block_content"] for chunk in chunks)),
            data,
        )
        self.assertEqual(chunks[-1]["current_block_index"], len(chunks))
        listing = reply(json.dumps({"audio_list": [
            {"CUSTOM_NAME": "other", "UNIQUE_ID": "old"},
            {"CUSTOM_NAME": "yufeng_test", "UNIQUE_ID": "new"},
        ]}))
        self.assertEqual(find_uploaded_uuid(audio_entries(listing), "yufeng_test"), "new")


class AudioSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_robot_sound_is_reused_without_upload_after_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            sound = SoundLibrary(Path(directory)).list_sounds()[0]
            requests = []
            events = []

            async def publish(_topic, options):
                requests.append(options)
                if options["api_id"] == 1001:
                    return reply({"audio_list": [
                        {"CUSTOM_NAME": robot_audio_name(sound.asset_id), "UNIQUE_ID": "robot-uuid"}
                    ]})
                return reply()

            session = object.__new__(RobotSession)
            session._connected = True
            session._state_lock = threading.Lock()
            session._audio_task = None
            session._audio_remote = {}
            session._audio_catalog_ready = False
            session._conn = SimpleNamespace(datachannel=SimpleNamespace(
                pub_sub=SimpleNamespace(publish_request_new=publish)
            ))
            session._command_lock = asyncio.Lock()
            session._on_event = lambda kind, message: events.append((kind, message))
            await session._sync_sounds([sound])
            await session._play_synced_audio(sound.asset_id)
            self.assertEqual([item["api_id"] for item in requests], [1001, 1007, 1002])
            self.assertTrue(session.has_synced_audio(sound.asset_id))
            self.assertIn(("audio_started", sound.asset_id), events)

    async def test_missing_robot_sound_uploads_once_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            sound = SoundLibrary(Path(directory)).list_sounds()[0]
            requests = []
            remote = []

            async def publish(_topic, options):
                requests.append(options)
                if options["api_id"] == 2001:
                    block = json.loads(options["parameter"])
                    if block["current_block_index"] == block["total_block_number"]:
                        remote.append({
                            "CUSTOM_NAME": block["file_name"], "UNIQUE_ID": "fresh-uuid"
                        })
                if options["api_id"] == 1001:
                    return reply({"audio_list": list(remote)})
                return reply()

            session = object.__new__(RobotSession)
            session._connected = True
            session._state_lock = threading.Lock()
            session._audio_task = None
            session._audio_remote = {}
            session._audio_catalog_ready = False
            session._conn = SimpleNamespace(datachannel=SimpleNamespace(
                pub_sub=SimpleNamespace(publish_request_new=publish)
            ))
            session._command_lock = asyncio.Lock()
            session._on_event = lambda _kind, _message: None
            await session._sync_sounds([sound])
            uploads = sum(item["api_id"] == 2001 for item in requests)
            self.assertGreater(uploads, 0)
            await session._sync_sounds([sound])
            self.assertEqual(sum(item["api_id"] == 2001 for item in requests), uploads)

    async def test_upload_then_play_and_cache_without_reupload(self):
        requests = []
        events = []

        async def publish(_topic, options):
            requests.append(options)
            api = options["api_id"]
            if api == 1001:
                name = json.loads(requests[0]["parameter"])["file_name"]
                return reply({"audio_list": [{"CUSTOM_NAME": name, "UNIQUE_ID": "robot-id"}]})
            return reply()

        session = object.__new__(RobotSession)
        session._connected = True
        session._state_lock = threading.Lock()
        session._audio_task = None
        session._audio_cache = {}
        session._conn = SimpleNamespace(datachannel=SimpleNamespace(
            pub_sub=SimpleNamespace(publish_request_new=publish)
        ))
        session._command_lock = asyncio.Lock()
        session._on_event = lambda kind, message: events.append((kind, message))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.mp3"
            source.write_bytes(b"test fixture")
            with patch("go2_safe_control.session.prepare_wav", return_value=b"RIFFtest"):
                await session._play_audio(source)
                await session._play_audio(source)
        api_ids = [item["api_id"] for item in requests]
        self.assertEqual(api_ids, [2001, 1001, 1007, 1002, 1007, 1002])
        self.assertEqual(requests[-1]["parameter"], '{"unique_id": "robot-id"}')
        self.assertTrue(any(kind == "audio" and "正在播放" in message for kind, message in events))

    async def test_stop_sends_pause(self):
        requests = []

        async def publish(_topic, options):
            requests.append(options)
            return reply()

        session = object.__new__(RobotSession)
        session._audio_task = None
        session._conn = SimpleNamespace(datachannel=SimpleNamespace(
            pub_sub=SimpleNamespace(publish_request_new=publish)
        ))
        session._command_lock = asyncio.Lock()
        session._on_event = lambda _kind, _message: None
        await session._stop_audio()
        self.assertEqual(requests[0]["api_id"], 1003)
