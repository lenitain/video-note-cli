"""D6：抽帧的切镜判据——连续画面才换段，字幕换行不该骗过阈值。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from video_note.screenshots import (
    FrameSource,
    _changed,
    _clear_frames,
    _Sample,
    extract_frames,
)
from video_note.video_frames import signature_bytes


def picture(color: str, box: tuple[int, int, int, int] | None = None) -> Image.Image:
    """64x64 画面；给个 box 就在上面画一块白，模拟字幕/光标这类局部变化。"""
    image = Image.new("RGB", (64, 64), color)
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill="white")
    return image


def sample_of(image: Image.Image) -> _Sample:
    return _Sample(at=0.0, signature=signature_bytes(image), image=image)


def test_cut_is_detected_when_the_whole_frame_changes() -> None:
    previous = sample_of(picture("black"))

    assert _changed(previous, signature_bytes(picture("white"))) is True


def test_local_subtitle_change_is_not_a_cut() -> None:
    previous = sample_of(picture("black"))

    # 一行字幕只占画面不到 2% 的像素，不该被当成切镜
    assert _changed(previous, signature_bytes(picture("black", (0, 60, 31, 60)))) is False


def test_interval_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        extract_frames([FrameSource(tmp_path / "v.mp4")], tmp_path / "frames", 0)


def test_previous_frames_are_cleared_before_writing(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frame_0000_0s.jpg").write_bytes(b"old")
    (frames_dir / "frames.json").write_text("[]", encoding="utf-8")
    (frames_dir / ".complete").write_text("1\n", encoding="utf-8")

    _clear_frames(frames_dir)

    assert list(frames_dir.iterdir()) == []
