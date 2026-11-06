"""D5：Provider 归一、端点身份、参数兼容判定与插图数量分配。"""
from __future__ import annotations

import pytest

from video_note import llm_summarizer as summarizer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MiniMax-CN", "minimax_cn"),
        ("  OpenAI ", "openai"),
        ("deepseek", "deepseek"),
        ("", ""),
    ],
)
def test_normalize_provider(raw: str, expected: str) -> None:
    assert summarizer.normalize_provider(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 已保存的 Key 以 host+path 为身份：大小写、尾斜杠都不该造出第二个身份
        ("https://API.DeepSeek.com/", "api.deepseek.com"),
        ("api.deepseek.com", "api.deepseek.com"),
        ("https://api.openai.com/v1", "api.openai.com/v1"),
    ],
)
def test_normalize_endpoint_host(raw: str, expected: str) -> None:
    assert summarizer.normalize_endpoint_host(raw, "deepseek") == expected


def test_normalize_endpoint_host_uses_provider_default_when_blank() -> None:
    assert summarizer.normalize_endpoint_host("", "deepseek") == "api.deepseek.com"
    assert summarizer.normalize_endpoint_host("", "custom") == ""


@pytest.mark.parametrize(
    ("seconds", "cap", "expected"),
    [
        (10.0, 6, 1),
        (240.0, 6, 2),
        (600.0, 6, 3),
        (3600.0, 6, 6),
    ],
)
def test_inline_frame_limit_scales_with_duration(seconds: float, cap: int, expected: int) -> None:
    assert summarizer.LLMSummarizer.inline_frame_limit(seconds, cap) == expected


def test_inline_frame_limit_never_zero() -> None:
    assert summarizer.LLMSummarizer.inline_frame_limit(0.0, 4) == 1


def test_vision_rejection_is_recognised() -> None:
    assert summarizer.looks_like_vision_rejection(
        Exception("image_url is not supported by this model")
    )


def test_unrelated_failure_is_not_a_vision_rejection() -> None:
    assert not summarizer.looks_like_vision_rejection(Exception("rate limited, retry later"))


def test_unsupported_param_is_named_from_server_body() -> None:
    error = Exception('"thinking" is not supported with this model, use "reasoning_effort"')

    assert summarizer._unsupported_param_names(error) == {"thinking"}


def test_unsupported_param_ignores_unrelated_errors() -> None:
    assert summarizer._unsupported_param_names(Exception("boom")) == set()
