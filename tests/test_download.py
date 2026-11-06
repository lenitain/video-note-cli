"""D1：断点续传、完整性与「绝不把不完整文件改名成正式文件」。

全部走伪造的 ``urlopen``，不碰网络。
"""
from __future__ import annotations

import asyncio
import threading
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

import pytest

from video_note import download


class FakeResponse:
    """够用的 urlopen 返回值：状态、头、按块读取。"""

    def __init__(self, body: bytes, *, status: int = 200, content_length: int | None = None) -> None:
        self._body = body
        self._offset = 0
        self.status = status
        self.headers: dict[str, str] = (
            {} if content_length is None else {"Content-Length": str(content_length)}
        )

    def read(self, size: int = -1) -> bytes:
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试之间的等待对测试没有意义。"""
    monkeypatch.setattr(download.time, "sleep", lambda _seconds: None)


def install_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[int, Any], FakeResponse],
) -> list[Any]:
    """装一个记录请求的 urlopen，返回收到的请求列表。"""
    calls: list[Any] = []

    def urlopen(request: Any, timeout: int | None = None) -> FakeResponse:
        calls.append(request)
        return handler(len(calls), request)

    monkeypatch.setattr(download.urllib.request, "urlopen", urlopen)
    return calls


def test_truncated_body_keeps_part_and_never_renames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_urlopen(
        monkeypatch,
        lambda _call, _request: FakeResponse(b"x" * 50, content_length=100),
    )
    target = tmp_path / "model.bin"

    ok, error = download.download_to_file("https://host/model.bin", target, {}, attempts=1)

    assert ok is False
    assert isinstance(error, RuntimeError)
    assert "下载不完整" in str(error)
    assert not target.exists()
    assert (tmp_path / "model.bin.part").read_bytes() == b"x" * 50


def test_404_stops_without_retrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(_call: int, request: Any) -> FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    calls = install_urlopen(monkeypatch, handler)

    ok, error = download.download_to_file(
        "https://host/missing.bin", tmp_path / "missing.bin", {}, attempts=4
    )

    assert ok is False
    assert isinstance(error, urllib.error.HTTPError)
    assert len(calls) == 1


def test_416_discards_part_and_downloads_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "model.bin"
    part = tmp_path / "model.bin.part"
    part.write_bytes(b"stale")

    def handler(call: int, request: Any) -> FakeResponse:
        if call == 1:
            raise urllib.error.HTTPError(request.full_url, 416, "Range Not Satisfiable", {}, None)
        return FakeResponse(b"f" * 10, content_length=10)

    calls = install_urlopen(monkeypatch, handler)

    ok, _error = download.download_to_file("https://host/model.bin", target, {})

    assert ok is True
    assert target.read_bytes() == b"f" * 10
    assert not part.exists()
    assert len(calls) == 2
    assert calls[1].get_header("Range") is None, "416 之后必须从头下载，不能再带断点"


def test_resume_sends_range_and_appends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "model.bin"
    (tmp_path / "model.bin.part").write_bytes(b"head")

    calls = install_urlopen(
        monkeypatch,
        lambda _call, _request: FakeResponse(b"tail!!", status=206, content_length=6),
    )

    ok, _error = download.download_to_file("https://host/model.bin", target, {})

    assert ok is True
    assert target.read_bytes() == b"headtail!!"
    assert calls[0].get_header("Range") == "bytes=4-"


def test_server_ignoring_range_restarts_from_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "model.bin"
    (tmp_path / "model.bin.part").write_bytes(b"wronghead")

    install_urlopen(
        monkeypatch,
        lambda _call, _request: FakeResponse(b"0123456789", status=200, content_length=10),
    )

    ok, _error = download.download_to_file("https://host/model.bin", target, {})

    assert ok is True
    assert target.read_bytes() == b"0123456789", "服务端忽略 Range 时必须丢弃旧的 .part"


def test_cancelled_before_start_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = install_urlopen(
        monkeypatch, lambda _call, _request: pytest.fail("取消后不应发起请求")
    )
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(asyncio.CancelledError):
        download.download_to_file("https://host/x", tmp_path / "x", {}, cancel)

    assert calls == []
