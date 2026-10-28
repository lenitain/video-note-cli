from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

import av

MAX_FRAME_WIDTH = 1280


def extract_video_frames(
    video_path: Path,
    frames_dir: Path,
    interval: int,
    should_abort: Callable[[], bool] | None = None,
) -> list[Path]:
    """Extract one JPEG frame every `interval` seconds from a video file.

    Writes frames as frame_0000_0s.jpg, frame_0001_30s.jpg, ... and drops a
    .complete marker only when at least one frame was written. The caller can
    safely reuse a directory with that marker.
    """
    if interval <= 0:
        raise ValueError("抽帧间隔必须大于 0 秒")
    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "抽帧需要 Pillow；请运行 uv sync 安装依赖"
        ) from exc

    frames_dir.mkdir(parents=True, exist_ok=True)
    for old_frame in frames_dir.glob("frame_*.jpg"):
        old_frame.unlink(missing_ok=True)
    (frames_dir / ".complete").unlink(missing_ok=True)

    frames: list[Path] = []
    with av.open(str(video_path)) as container:
        if not container.streams.video:
            return []
        duration = (container.duration or 0) / av.time_base
        if duration <= 0:
            video_stream = container.streams.video[0]
            if video_stream.duration:
                duration = float(video_stream.duration * video_stream.time_base)
        if duration <= 0:
            return []
        for index, timestamp in enumerate(
            range(0, int(duration) + 1, interval)
        ):
            if should_abort is not None and should_abort():
                raise asyncio.CancelledError("任务已取消")
            container.seek(int(timestamp * av.time_base), backward=True)
            frame = next(container.decode(video=0), None)
            if frame is None:
                continue
            path = frames_dir / f"frame_{index:04d}_{timestamp}s.jpg"
            try:
                image = frame.to_image().convert("RGB")
                image.thumbnail((MAX_FRAME_WIDTH, MAX_FRAME_WIDTH))
            except Exception as exc:
                raise RuntimeError(f"视频帧转换成图片失败：{exc}") from exc
            image.save(path, format="JPEG", quality=82)
            frames.append(path)

    if frames:
        (frames_dir / ".complete").write_text(
            f"{len(frames)}\n", encoding="utf-8"
        )
    return frames


async def extract_video_frames_async(
    video_path: Path,
    frames_dir: Path,
    interval: int,
    should_abort: Callable[[], bool] | None = None,
) -> list[Path]:
    return await asyncio.to_thread(
        extract_video_frames, video_path, frames_dir, interval, should_abort
    )
