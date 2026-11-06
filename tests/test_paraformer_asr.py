"""D3：默认镜像失败后追加官方源；用户指定 HF_ENDPOINT 时不改它的意图。"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from video_note import paraformer_asr


def make_failing_download(attempted: list[str]) -> Callable[..., tuple[bool, Exception]]:
    """记录尝试过的地址，一律失败，逼出提示语里的端点清单。"""

    def download(url: str, *_args: object, **_kwargs: object) -> tuple[bool, Exception]:
        attempted.append(url)
        return False, RuntimeError("boom")

    return download


@pytest.fixture
def attempted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(paraformer_asr, "download_to_file", make_failing_download(calls))
    return calls


def test_default_mirror_falls_back_to_official_endpoint(
    tmp_path: Path, attempted: list[str]
) -> None:
    transcriber = paraformer_asr.ParaformerTranscriber(tmp_path)

    with pytest.raises(RuntimeError) as failure:
        transcriber._download_component("vad", None)

    assert attempted[0].startswith("https://hf-mirror.com/")
    assert attempted[1].startswith("https://huggingface.co/")
    assert "已尝试 https://hf-mirror.com、https://huggingface.co" in str(failure.value)


def test_custom_endpoint_is_not_extended(
    tmp_path: Path, attempted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.internal/")
    transcriber = paraformer_asr.ParaformerTranscriber(tmp_path)

    with pytest.raises(RuntimeError):
        transcriber._download_component("vad", None)

    assert {url.startswith("https://mirror.internal/") for url in attempted} == {True}
    assert not any("huggingface.co" in url for url in attempted)


def test_cache_status_reports_missing_before_download(tmp_path: Path) -> None:
    transcriber = paraformer_asr.ParaformerTranscriber(tmp_path)

    assert transcriber.cache_status() == "missing"
