from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image

from .transcript import format_timestamp

# 文件名由 screenshots.py 写入：frame_0000_0s.jpg（序号从 0 开始，时间是秒）。
FRAME_FILE_RE = re.compile(r"^frame_(\d{4})_(\d+)s\.jpg$")
# 画面段落清单：文件名只装得下起点，装不下“这段画面持续到什么时候”，
# 而配图要按区间选，所以区间单独存成 frames.json。
FRAME_MANIFEST_NAME = "frames.json"
# 送给视觉模型前的最长边与质量：幻灯片上的小字要可读，但不必原图直传。
VISION_MAX_SIDE = 1024
VISION_JPEG_QUALITY = 80
# 判“换画面”的平均色差阈值：64x64 RGB 缩略图逐像素平均差。
# 实测口径：同一张幻灯片上字幕换行、光标移动、小段动画是 1–3，真的换幻灯片在 15 以上；
# 按块取最大值的旧写法会把字幕换行（只动底部几块）当成换画面，动画类视频会碎成一地。
SHOT_CHANGE_MEAN_DIFF = 4.0
SIGNATURE_SIDE = 64


@dataclass(frozen=True)
class VideoFrame:
    """一段画面的代表帧：序号、起点、磁盘上的 JPEG、这段画面的终点。

    ``end`` 是下一段画面开始的时刻。旧缓存里只有文件名、没有清单，读回来是
    ``None``，此时按时间点判断归属（见 :meth:`covers`）。
    """

    index: int
    seconds: float
    path: Path
    end: float | None = None

    @property
    def frame_id(self) -> str:
        """提示词与占位符里用的编号，和文件名前缀一致（0001）。"""
        return f"{self.index:04d}"

    @property
    def timestamp(self) -> str:
        """画面在视频里的时间点，形如 01:30。"""
        return format_timestamp(self.seconds)

    @property
    def span_end(self) -> float:
        """画面持续到的时刻；没有区间时退化成起点本身。"""
        if self.end is not None and self.end > self.seconds:
            return self.end
        return self.seconds

    @property
    def span_label(self) -> str:
        """画面占用的时间段，形如 01:30–04:10；没有区间时只要起点。"""
        if self.span_end <= self.seconds:
            return self.timestamp
        return f"{self.timestamp}–{format_timestamp(self.span_end)}"

    @property
    def filename(self) -> str:
        return self.path.name

    def covers(self, start: float, end: float) -> bool:
        """这段画面是否出现在 [start, end] 里。

        有区间时按半开区间相交算（下一段画面正好从小节末尾那个时点开始，就不属于这一节）；
        旧缓存没有区间，退化成时间点判断。
        """
        if self.span_end <= self.seconds:
            return start <= self.seconds <= end
        return self.seconds < end and start < self.span_end


def parse_frame_file(path: Path) -> VideoFrame:
    """从 frame_0001_30s.jpg 反推编号与时间点（没有区间清单时的退路）。"""
    match = FRAME_FILE_RE.match(path.name)
    if match is None:
        raise ValueError(f"不是截图文件名：{path.name}")
    return VideoFrame(
        index=int(match.group(1)), seconds=float(match.group(2)), path=path
    )


def load_frames(directory: Path) -> list[VideoFrame]:
    """读目录里已抽好的截图：优先用区间清单，没有清单就只按文件名。"""
    frames = _load_manifest(directory)
    if frames is not None:
        return frames
    frames = []
    for path in directory.glob("frame_*.jpg"):
        try:
            frames.append(parse_frame_file(path))
        except ValueError:
            continue
    return sorted(frames, key=lambda frame: frame.index)


def write_frame_manifest(
    directory: Path, frames: Sequence[VideoFrame]
) -> None:
    """把画面区间写成清单，缓存复用时才拿得到“这段幻灯片持续到哪”。"""
    payload = [
        {
            "index": frame.index,
            "seconds": round(frame.seconds, 3),
            "end": round(frame.span_end, 3),
            "file": frame.filename,
        }
        for frame in frames
    ]
    (directory / FRAME_MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_manifest(directory: Path) -> list[VideoFrame] | None:
    path = directory / FRAME_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    frames: list[VideoFrame] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        picture = directory / str(item.get("file") or "")
        if not picture.is_file():
            return None
        try:
            frames.append(
                VideoFrame(
                    index=int(item["index"]),
                    seconds=float(item["seconds"]),
                    path=picture,
                    end=float(item["end"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    return sorted(frames, key=lambda frame: frame.index) or None


def frames_in_span(
    frames: Sequence[VideoFrame], start: float, end: float
) -> list[VideoFrame]:
    """取给某段正文配图用的画面。

    起点落在这段区间内的画面最贴题，优先全给；一张都没有时，退回“画面本身覆盖
    这段区间”的那些——幻灯片在段前就出现、一直讲到段中，正是等间隔抽样漏掉的
    情形。
    """
    inside = [frame for frame in frames if start <= frame.seconds <= end]
    if inside:
        return inside
    return [frame for frame in frames if frame.covers(start, end)]


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


def frame_data_url(frame: VideoFrame) -> str:
    """把截图缩到视觉接口友好的尺寸，编成 data:image/jpeg;base64,..."""
    with Image.open(frame.path) as image:
        picture = image.convert("RGB")
        picture.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=VISION_JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def signature_bytes(picture: Image.Image) -> bytes:
    """64x64 RGB 缩略图的原始像素，用于比较两张画面像不像。"""
    return picture.convert("RGB").resize(
        (SIGNATURE_SIDE, SIGNATURE_SIDE)
    ).tobytes()


def mean_picture_diff(left: bytes, right: bytes) -> float:
    """两张缩略图的平均色差；比较整幅画面，不受局部字幕/光标的影响。"""
    total = 0
    pixels = SIGNATURE_SIDE * SIGNATURE_SIDE
    for offset in range(0, pixels * 3, 3):
        total += abs(left[offset] - right[offset])
        total += abs(left[offset + 1] - right[offset + 1])
        total += abs(left[offset + 2] - right[offset + 2])
    return total / (pixels * 3)
