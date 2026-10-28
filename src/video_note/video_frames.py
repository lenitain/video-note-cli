from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image

from .transcript import format_timestamp

# 文件名由 screenshots.py 写入：frame_0000_0s.jpg（序号从 0 开始，时间是秒）。
FRAME_FILE_RE = re.compile(r"^frame_(\d{4})_(\d+)s\.jpg$")
# 送给视觉模型前的最长边与质量：幻灯片上的小字要可读，但不必原图直传。
VISION_MAX_SIDE = 1024
VISION_JPEG_QUALITY = 80
# 相邻或最近几张里出现过的画面不再重复保留：幻灯片、静态板书只留第一张。
# 判据是 64x64 RGB 缩略图切 8x8 块后“最不一样的一块”的平均差：整张缩略图的均值
# 会被大片同色背景稀释，连“换了一张幻灯片”都看不出来；按块比较时，新出现的一行
# 字就能把所在块抬到 7-10，而同一帧的编码噪声只有 1 左右。
DUPLICATE_BLOCK_DIFF = 3.0
# 往回比对的保留帧数：讲者切回同一张幻灯片很常见，只看上一张会漏掉这种情况。
DUPLICATE_LOOKBACK = 8
SIGNATURE_SIDE = 64
SIGNATURE_GRID = 8


@dataclass(frozen=True)
class VideoFrame:
    """一帧已抽出的画面：序号、时间点（秒）与磁盘上的 JPEG。"""

    index: int
    seconds: float
    path: Path

    @property
    def frame_id(self) -> str:
        """提示词与占位符里用的编号，和文件名前缀一致（0001）。"""
        return f"{self.index:04d}"

    @property
    def timestamp(self) -> str:
        """画面在视频里的时间点，形如 01:30。"""
        return format_timestamp(self.seconds)

    @property
    def filename(self) -> str:
        return self.path.name


def parse_frame_file(path: Path) -> VideoFrame:
    """从 frame_0001_30s.jpg 反推编号与时间点。"""
    match = FRAME_FILE_RE.match(path.name)
    if match is None:
        raise ValueError(f"不是截图文件名：{path.name}")
    return VideoFrame(
        index=int(match.group(1)), seconds=float(match.group(2)), path=path
    )


def load_frames(directory: Path) -> list[VideoFrame]:
    """按序号读出目录里已抽好的截图（复用缓存时用）。"""
    frames = []
    for path in directory.glob("frame_*.jpg"):
        try:
            frames.append(parse_frame_file(path))
        except ValueError:
            continue
    return sorted(frames, key=lambda frame: frame.index)


def frames_in_span(
    frames: Sequence[VideoFrame], start: float, end: float
) -> list[VideoFrame]:
    """取时间点落在 [start, end] 内的截图，用于给某段正文配图。"""
    return [frame for frame in frames if start <= frame.seconds <= end]


def evenly_sample_frames(
    frames: Sequence[VideoFrame], limit: int
) -> list[VideoFrame]:
    """超过 limit 张时均匀抽稀（保留首尾），控制单次请求的图片数量。"""
    if limit <= 0:
        return []
    if len(frames) <= limit:
        return list(frames)
    if limit == 1:
        return [frames[0]]
    last = len(frames) - 1
    picked = {round(index * last / (limit - 1)) for index in range(limit)}
    return [frames[index] for index in sorted(picked)]


def drop_duplicate_frames(
    frames: Sequence[VideoFrame], threshold: float = DUPLICATE_BLOCK_DIFF
) -> list[VideoFrame]:
    """丢掉与最近几张保留帧几乎相同的画面，避免同一张幻灯片重复占图和占 token。"""
    kept: list[VideoFrame] = []
    recent: list[bytes] = []
    for frame in frames:
        try:
            signature = _frame_signature(frame.path)
        except OSError:
            kept.append(frame)
            continue
        if any(
            _max_block_diff(signature, previous) < threshold
            for previous in recent
        ):
            continue
        kept.append(frame)
        recent.append(signature)
        if len(recent) > DUPLICATE_LOOKBACK:
            recent.pop(0)
    return kept


def frame_data_url(frame: VideoFrame) -> str:
    """把截图缩到视觉接口友好的尺寸，编成 data:image/jpeg;base64,..."""
    with Image.open(frame.path) as image:
        picture = image.convert("RGB")
        picture.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=VISION_JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _frame_signature(path: Path) -> bytes:
    with Image.open(path) as image:
        return image.convert("RGB").resize(
            (SIGNATURE_SIDE, SIGNATURE_SIDE)
        ).tobytes()


def _max_block_diff(left: bytes, right: bytes) -> float:
    """最不一样的那块的平均色差；整张图的均值会被同色背景稀释成 0.x。"""
    block = SIGNATURE_SIDE // SIGNATURE_GRID
    worst = 0.0
    for row in range(SIGNATURE_GRID):
        for column in range(SIGNATURE_GRID):
            total = 0
            for y in range(row * block, (row + 1) * block):
                base = y * SIGNATURE_SIDE * 3
                for x in range(column * block, (column + 1) * block):
                    offset = base + x * 3
                    total += abs(left[offset] - right[offset])
                    total += abs(left[offset + 1] - right[offset + 1])
                    total += abs(left[offset + 2] - right[offset + 2])
            worst = max(worst, total / (block * block * 3))
    return worst
