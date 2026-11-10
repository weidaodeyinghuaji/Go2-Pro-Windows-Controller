"""本机声音库：导入一次，流程只保存内容 ID，不保存易失效的绝对路径。"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from matplotlib.cbook import safe_masked_invalid

from .audio_playback import MAX_WAV_BYTES, prepare_wav


MAX_SOUNDS = 30


@dataclass(frozen=True)
class Sound:
    """声音的内容编号、显示名称、时长和本地文件位置。"""

    asset_id: str
    name: str
    duration: float
    path: Path


def robot_audio_name(asset_id: str) -> str:
    """名字由音频内容决定；重启程序或换电脑也能认出同一份音频。"""
    return "yf_" + asset_id[:24]

def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            if reader.getframerate() <= 0 or reader.getnframes() <= 0:
                raise ValueError("音频没有可播放的声音样本")
            return reader.getnframes() / reader.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError("声音素材不是有效 WAV 文件") from exc


def _built_in_tone() -> bytes:
    """生成无版权依赖的短提示音，免安装版无需再下载音频文件。"""

    sample_rate = 44100
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        samples = bytearray()
        for index in range(int(sample_rate * 0.45)):
            second = index / sample_rate
            envelope = min(1.0, second * 20) * min(1.0, (0.45 - second) * 15)
            value = int(7500 * envelope * math.sin(2 * math.pi * 660 * second))
            samples.extend(value.to_bytes(2, "little", signed=True))
        target.writeframes(samples)
    return output.getvalue()

class SoundLibrary:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._index_path = directory / "index.json"
        self._names: dict[str, str] = {}
        if self._index_path.exists():
            try:
                raw = json.loads(self._index_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._names = {
                        key: value for key, value in raw.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
            except (OSError, ValueError):
                # 仍可按文件内容恢复；损坏的索引不能导致整个控制器无法启动。
                self._names = {}
        self._store(_built_in_tone(), "内置提示音")

    @staticmethod
    def valid_id(asset_id: str) -> bool:
        return len(asset_id) == 64 and all(char in "0123456789abcdef" for char in asset_id)

    def _save_index(self) -> None:
        data = json.dumps(self._names, ensure_ascii=False, indent=2).encode("utf-8")
        self._write_atomic(self._index_path, data)

    @staticmethod
    def _write_atomic(destination: Path, data: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".sound-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _store(self, wav_data: bytes, name: str) -> Sound:
        if len(wav_data) > MAX_WAV_BYTES:
            raise ValueError("转换后 WAV 不能超过 10 MB")
        duration = wav_duration(wav_data)
        asset_id = hashlib.sha256(wav_data).hexdigest()
        destination = self.directory / f"{asset_id}.wav"
        if not destination.exists():
            if len(self._names) >= MAX_SOUNDS:
                raise ValueError(f"声音库最多保存 {MAX_SOUNDS} 个声音")
            self._write_atomic(destination, wav_data)
        elif hashlib.sha256(destination.read_bytes()).hexdigest() != asset_id:
            self._write_atomic(destination, wav_data)
        self._names[asset_id] = name[:80]
        self._save_index()
        return Sound(asset_id, self._names[asset_id], duration, destination)

    def import_file(self, path: Path) -> Sound:
        return self._store(prepare_wav(path), path.stem)

    def get(self, asset_id: str) -> Sound:
        if not self.valid_id(asset_id):
            raise ValueError("声音 ID 无效")
        path = self.directory / f"{asset_id}.wav"
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"声音素材缺失：{asset_id[:12]}") from exc
        if hashlib.sha256(data).hexdigest() != asset_id:
            raise ValueError(f"声音素材已损坏：{asset_id[:12]}")
        return Sound(asset_id, self._names.get(asset_id, f"声音 {asset_id[:8]}"), wav_duration(data), path)

    def list_sounds(self) -> list[Sound]:
        sounds = []
        for asset_id in list(self._names):
            try:
                sounds.append(self.get(asset_id))
            except ValueError:
                continue
        return sounds

    def export_for_workflow(self, ids: set[str], folder: Path) -> None:
        if not ids:
            return
        folder.mkdir(parents=True, exist_ok=True)
        names = {}
        for asset_id in sorted(ids):
            sound = self.get(asset_id)
            self._write_atomic(folder / f"{asset_id}.wav", sound.path.read_bytes())
            names[asset_id] = sound.name
        self._write_atomic(
            folder / "index.json", json.dumps(names, ensure_ascii=False, indent=2).encode("utf-8")
        )

    def import_workflow_assets(self, ids: set[str], folder: Path) -> None:
        if not ids:
            return
        names = {}
        index = folder / "index.json"
        if index.is_file():
            try:
                raw = json.loads(index.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    names = raw
            except (OSError, ValueError):
                pass
        for asset_id in ids:
            if not self.valid_id(asset_id):
                raise ValueError("流程引用的声音 ID 无效")
            source = folder / f"{asset_id}.wav"
            if source.is_file():
                data = source.read_bytes()
                if hashlib.sha256(data).hexdigest() != asset_id:
                    raise ValueError(f"流程声音素材校验失败：{asset_id[:12]}")
                name = names.get(asset_id, f"声音 {asset_id[:8]}")
                self._store(data, name if isinstance(name, str) else f"声音 {asset_id[:8]}")
            else:
                self.get(asset_id)  # 本机已有素材时仍允许读取旧流程。
