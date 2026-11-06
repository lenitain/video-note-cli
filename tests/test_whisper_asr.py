"""D2：命令行措辞与「模型下载随转写按需触发」的入口收缩。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from video_note import whisper_asr
from video_note.whisper_asr import WhisperTranscriber


def test_gpu_hint_targets_the_command_line_not_a_gui() -> None:
    assert "重新运行命令" in whisper_asr.GPU_UNAVAILABLE_NOTE
    assert "重启程序" not in whisper_asr.GPU_UNAVAILABLE_NOTE


def test_download_entry_point_is_gone() -> None:
    """上游把下载做成独立任务（带进度回调）；本仓没有那个任务，入口随之移除。"""
    assert not hasattr(WhisperTranscriber, "download_model")


def test_single_file_download_records_last_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def failing(url: str, *_args: Any, **_kwargs: Any) -> tuple[bool, Exception]:
        calls.append(url)
        return False, RuntimeError("下载不完整")

    monkeypatch.setattr(whisper_asr, "download_to_file", failing)
    transcriber = WhisperTranscriber()

    ok = transcriber._download_one_file("https://host/f.bin", tmp_path / "f.bin", {})

    assert ok is False
    assert calls == ["https://host/f.bin"]
    assert "下载不完整" in str(transcriber._last_download_error)
