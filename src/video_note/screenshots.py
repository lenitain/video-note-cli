from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import av
from PIL import Image

from .video_frames import (
    FRAME_MANIFEST_NAME,
    SHOT_CHANGE_MEAN_DIFF,
    VideoFrame,
    mean_picture_diff,
    signature_bytes,
    write_frame_manifest,
)

MAX_FRAME_WIDTH = 1280
# 比对画面的最大步长：切镜 / 换幻灯片最多迟滞这么久被发现。
# 再密就要按帧解码却没有额外收益——一段画面至少要撑过一次采样才能被看见。
SHOT_SCAN_MAX_SECONDS = 3.0


@dataclass(frozen=True)
class FrameSource:
    """一个待抽帧的视频文件，以及它在合并时间轴上的偏移（B 站分 P 用）。"""

    path: Path
    offset: float = 0.0


@dataclass(frozen=True)
class _Sample:
    """某个时刻的画面：起点、缩略图签名、可写盘的图像。"""

    at: float
    signature: bytes
    image: Image.Image


def extract_frames(
    sources: Sequence[FrameSource],
    frames_dir: Path,
    interval: int,
    should_abort: Callable[[], bool] | None = None,
) -> list[VideoFrame]:
    """按顺序从 sources 抽帧：一段画面留一张代表帧，并记下它覆盖的时间段。

    时间戳是「文件内时间 + offset」，也就是合并时间轴上的位置，序号跨文件连续。
    写完 frames.json 与 .complete 才算完成，缓存复用依赖这两个标记。
    """
    if interval <= 0:
        raise ValueError("画面切分的最小间隔必须大于 0 秒")

    frames_dir.mkdir(parents=True, exist_ok=True)
    _clear_frames(frames_dir)

    frames: list[VideoFrame] = []
    for source in sources:
        if should_abort is not None and should_abort():
            raise asyncio.CancelledError("任务已取消")
        frames.extend(
            _extract_shots(
                source, frames_dir, interval, len(frames), should_abort
            )
        )

    if frames:
        write_frame_manifest(frames_dir, frames)
        (frames_dir / ".complete").write_text(
            f"{len(frames)}\n", encoding="utf-8"
        )
    return frames


def _extract_shots(
    source: FrameSource,
    frames_dir: Path,
    interval: int,
    start_index: int,
    should_abort: Callable[[], bool] | None,
) -> list[VideoFrame]:
    """顺序解码一遍，按画面变化切段，每段留一张代表帧。

    两条规则撑起整段逻辑：
    - 只有**连续两次采样**都显示新画面才算真的换了画面，字幕换行、光标移动、一次
      按键高亮骗不过第二关；
    - 两段之间的最短间隔是 ``interval``，比它更短的画面并进下一段（幻灯片放 3 秒
      就切走时，这点内容不值得单独占一张图和一次模型调用）。
    代表帧取该段的最后一帧：逐行出现的代码、逐步展开的动画收尾后内容最完整。
    """
    frames: list[VideoFrame] = []
    scan_step = min(float(interval), SHOT_SCAN_MAX_SECONDS)

    def write(image: Image.Image, start: float, end: float) -> None:
        index = start_index + len(frames)
        seconds = start + source.offset
        path = frames_dir / f"frame_{index:04d}_{int(seconds)}s.jpg"
        image.save(path, format="JPEG", quality=82)
        frames.append(
            VideoFrame(
                index=index,
                seconds=seconds,
                path=path,
                end=end + source.offset,
            )
        )

    with av.open(str(source.path)) as container:
        if not container.streams.video:
            return []
        stream = container.streams.video[0]
        time_base = float(stream.time_base)
        duration = _video_duration(container, stream)
        if duration <= 0:
            return []

        next_sample = 0.0
        shot_start: float | None = None
        shot_signature: bytes | None = None
        shot_image: Image.Image | None = None
        pending: _Sample | None = None

        for frame in container.decode(video=0):
            if should_abort is not None and should_abort():
                raise asyncio.CancelledError("任务已取消")
            at = (
                float(frame.pts) * time_base
                if frame.pts is not None
                else next_sample
            )
            if at < next_sample:
                continue
            next_sample = at + scan_step
            sample = _sample(frame, at)

            if shot_image is None:
                shot_start, shot_signature = sample.at, sample.signature
                shot_image = sample.image
                continue

            assert shot_start is not None and shot_signature is not None
            if pending is None:
                if _changed(sample, shot_signature):
                    pending = sample
                else:
                    shot_image = sample.image
                continue

            if _changed(sample, pending.signature):
                # 画面还在变：可能是动画或转场，把候选推到最新的这一帧。
                if _changed(sample, shot_signature):
                    pending = sample
                else:
                    # 又回到原画面：上一帧只是瞬时干扰。
                    pending = None
                    shot_image = sample.image
                continue

            # 候选稳定下来了，一段画面到此结束。
            if pending.at - shot_start >= interval:
                write(shot_image, shot_start, pending.at)
                shot_start = pending.at
            # 不足 interval 的画面不单独成条：起点不动，画面换成新的那一帧。
            shot_signature = pending.signature
            shot_image = sample.image
            pending = None

        if shot_image is not None and shot_start is not None:
            write(shot_image, shot_start, duration)
    return frames


def _sample(frame: av.VideoFrame, at: float) -> _Sample:
    picture = frame.to_image().convert("RGB")
    picture.thumbnail((MAX_FRAME_WIDTH, MAX_FRAME_WIDTH))
    return _Sample(at=at, signature=signature_bytes(picture), image=picture)


def _changed(sample: _Sample, signature: bytes) -> bool:
    return mean_picture_diff(sample.signature, signature) >= SHOT_CHANGE_MEAN_DIFF


def _video_duration(container: av.container.InputContainer, stream) -> float:
    duration = (container.duration or 0) / av.time_base
    if duration <= 0 and stream.duration:
        duration = float(stream.duration * stream.time_base)
    return float(duration)


def _clear_frames(frames_dir: Path) -> None:
    for old_frame in frames_dir.glob("frame_*.jpg"):
        old_frame.unlink(missing_ok=True)
    (frames_dir / FRAME_MANIFEST_NAME).unlink(missing_ok=True)
    (frames_dir / ".complete").unlink(missing_ok=True)


async def extract_frames_async(
    sources: Sequence[FrameSource],
    frames_dir: Path,
    interval: int,
    should_abort: Callable[[], bool] | None = None,
) -> list[VideoFrame]:
    return await asyncio.to_thread(
        extract_frames, sources, frames_dir, interval, should_abort
    )
