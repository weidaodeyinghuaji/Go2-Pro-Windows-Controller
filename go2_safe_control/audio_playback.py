"""把本地 MP3/WAV 转成 Go2 AudioHub 可接收的 WAV，并检查机器人回复。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import av
from unitree_webrtc_connect.constants import AUDIO_API


AUDIO_TOPIC = "rt/api/audiohub/request"
MAX_WAV_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 4096  # 与 unitree_webrtc_connect 2.1.2 的 AudioHub 上传协议一致。


def prepare_wav(path: Path) -> bytes:
    """在工作线程解码音频；不在原文件旁生成临时文件。"""

    if path.suffix.lower() not in {".mp3", ".wav"} or not path.is_file():
        raise ValueError("请选择存在的 MP3 或 WAV 文件")
    output = io.BytesIO()
    with av.open(str(path)) as container, wave.open(output, "wb") as target:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise ValueError("文件中没有可解码的音轨")
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(44100)
        converter = av.AudioResampler(format="s16", layout="mono", rate=44100)
        for frame in container.decode(stream):
            for converted in converter.resample(frame):
                # PyAV 的 plane 缓冲区有对齐填充，不能把填充字节当作声音样本。
                target.writeframes(bytes(converted.planes[0])[: converted.samples * 2])
                if output.tell() > MAX_WAV_BYTES:
                    raise ValueError("音频过长；转换后 WAV 不能超过 10 MB")
        for converted in converter.resample(None):
            target.writeframes(bytes(converted.planes[0])[: converted.samples * 2])
            if output.tell() > MAX_WAV_BYTES:
                raise ValueError("音频过长；转换后 WAV 不能超过 10 MB")
    return output.getvalue()


def audio_options(api_name: str, parameters: dict[str, object]) -> dict[str, object]:
    return {"api_id": AUDIO_API[api_name], "parameter": json.dumps(parameters, ensure_ascii=True)}


def require_audio_success(response: object, operation: str) -> dict[str, Any]:
    """收到 WebRTC 回复还不够；必须检查机器人业务状态码。"""

    try:
        assert isinstance(response, dict)
        data = response["data"]
        assert isinstance(data, dict)
        header = data["header"]
        assert isinstance(header, dict)
        status = header["status"]
        assert isinstance(status, dict)
        code = int(status["code"])
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{operation}：机器人回复格式无效") from exc
    if code != 0:
        raise RuntimeError(f"{operation}：机器人拒绝，错误码 {code}")
    return data


def audio_entries(response: object) -> list[dict[str, Any]]:
    data = require_audio_success(response, "读取音频列表")
    payload = data.get("data", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("audio_list"), list):
        raise RuntimeError("读取音频列表：机器人回复缺少 audio_list")
    return [entry for entry in payload["audio_list"] if isinstance(entry, dict)]


def find_uploaded_uuid(entries: list[dict[str, Any]], name: str) -> str:
    for entry in entries:
        entry_name = entry.get("CUSTOM_NAME") or entry.get("custom_name") or entry.get("name")
        if entry_name in {name, name + ".wav"}:
            unique_id = entry.get("UNIQUE_ID") or entry.get("unique_id") or entry.get("uuid")
            if unique_id:
                return str(unique_id)
    raise RuntimeError("文件已上传，但机器人音频列表中未找到它；未发送播放指令")


def synced_audio_ids(entries: list[dict[str, Any]]) -> dict[str, str]:
    """从机器狗实时列表恢复声音映射；不依赖本机旧连接的 UUID。"""

    found: dict[str, str] = {}
    for entry in entries:
        name = str(entry.get("CUSTOM_NAME") or entry.get("custom_name") or entry.get("name") or "")
        if name.endswith(".wav"):
            name = name[:-4]
        token = name[3:] if name.startswith("yf_") else ""
        unique_id = entry.get("UNIQUE_ID") or entry.get("unique_id") or entry.get("uuid")
        if len(token) == 24 and all(char in "0123456789abcdef" for char in token) and unique_id:
            found[token] = str(unique_id)
    return found


def upload_blocks(wav_data: bytes, file_name: str):
    """逐块产生协议参数；不在日志中打印音频内容。"""

    encoded = base64.b64encode(wav_data).decode("ascii")
    total = (len(encoded) + CHUNK_SIZE - 1) // CHUNK_SIZE
    digest = hashlib.md5(wav_data).hexdigest()
    timestamp = int(time.time() * 1000)
    for index in range(total):
        chunk = encoded[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
        yield {
            "file_name": file_name,
            "file_type": "wav",
            "file_size": len(wav_data),
            "current_block_index": index + 1,
            "total_block_number": total,
            "block_content": chunk,
            "current_block_size": len(chunk),
            "file_md5": digest,
            "create_time": timestamp,
        }


def upload_name() -> str:
    return f"yufeng_{uuid.uuid4().hex[:16]}"
