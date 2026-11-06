"""D4：B 站链接收敛、输入识别与本仓删掉的上游入口。"""
from __future__ import annotations

import pytest

from video_note.video_processor import (
    VideoProcessor,
    VideoSource,
    canonical_bilibili_url,
    normalize_video_input,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 追踪参数全部丢掉，同一个视频的不同分享链接落到同一个缓存键
        (
            "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.999&vd_source=abc",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        # 分 P 选择必须保留
        (
            "https://www.bilibili.com/video/BV1xx411c7mD/?p=3&share_source=copy_web",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=3",
        ),
        ("https://m.bilibili.com/video/av12345?p=0&spm=1", "https://www.bilibili.com/video/av12345"),
        ("https://www.bilibili.com/video/BV1xx411c7mD?p=2", "https://www.bilibili.com/video/BV1xx411c7mD?p=2"),
        # 解析不出的链接原样返回
        ("https://b23.tv/abcd123", "https://b23.tv/abcd123"),
        ("https://space.bilibili.com/12345", "https://space.bilibili.com/12345"),
        ("https://www.youtube.com/watch?v=abc&t=10", "https://www.youtube.com/watch?v=abc&t=10"),
    ],
)
def test_canonical_bilibili_url(raw: str, expected: str) -> None:
    assert canonical_bilibili_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "看看这个 https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=1 还不错",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        ("BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD"),
        ("https://youtu.be/xyz", "https://youtu.be/xyz"),
        ("   ", None),
    ],
)
def test_normalize_video_input(raw: str, expected: str | None) -> None:
    assert normalize_video_input(raw) == expected


def test_detect_source_covers_the_supported_sites() -> None:
    assert VideoProcessor.detect_source("https://www.bilibili.com/video/BV1xx411c7mD") is VideoSource.BILIBILI
    assert VideoProcessor.detect_source("https://b23.tv/abcd123") is VideoSource.BILIBILI
    assert VideoProcessor.detect_source("https://www.douyin.com/video/123") is VideoSource.DOUYIN
    assert VideoProcessor.detect_source("https://youtu.be/xyz") is VideoSource.YOUTUBE
    assert VideoProcessor.detect_source("https://example.com/v.mp4") is VideoSource.OTHER


def test_upstream_task_lifecycle_entry_points_are_gone() -> None:
    """上游给 Web 层用的抽取/清理入口在本仓没有对应动作（D4）。"""
    for name in ("extract_audio_track", "copy_local_media", "extract_frames", "cleanup"):
        assert not hasattr(VideoProcessor, name), f"{name} 是上游 Web 流程的入口，本仓应当已移除"
