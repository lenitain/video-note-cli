from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .transcript import (
    TranscriptSegment,
    format_timestamp,
    parse_subtitle_file,
    segments_to_prompt,
    segments_to_srt,
)

TRANSCRIPT_SOURCE_LABELS = {
    "bilibili_ai_subtitle": "B 站 AI 字幕",
    "platform_subtitle": "平台字幕",
    "platform_auto_caption": "平台自动字幕",
    "faster_whisper": "本地 Whisper 转写",
    "paraformer": "本地 Paraformer 转写",
    "bilibili_multi_page": "B 站分 P 合并",
    "imported_subtitle": "导入字幕",
    "transcript_file": "转录文件",
}

SUBTITLE_SUFFIXES = {".srt", ".vtt", ".json3"}


@dataclass(frozen=True)
class LoadedTranscript:
    segments: list[TranscriptSegment]
    title: str = ""
    language: str = "zh"
    source: str = "transcript_file"
    quality: dict[str, Any] | None = None


def transcript_source_label(source: str) -> str:
    return TRANSCRIPT_SOURCE_LABELS.get(source, source or "未知来源")


def transcript_markdown(
    title: str,
    segments: Sequence[TranscriptSegment],
    *,
    language: str = "",
    source: str = "",
    quality: dict[str, Any] | None = None,
    duration: float = 0.0,
) -> str:
    """Render the human-readable transcript that is saved next to the JSON/SRT."""
    effective_duration = duration or (segments[-1].end if segments else 0.0)
    details = [f"时长：{format_timestamp(effective_duration)}"]
    if language:
        details.append(f"语言：{language}")
    if source:
        details.append(f"来源：{transcript_source_label(source)}")
    if quality:
        characters = int(quality.get("characters") or 0)
        coverage = float(quality.get("speech_coverage") or 0.0)
        details.append(f"文字：{characters} 字")
        details.append(f"语音覆盖：{coverage:.0%}")
    return (
        f"# 带时间戳转录：{title}\n\n"
        f"> {' · '.join(details)}\n\n"
        f"{segments_to_prompt(segments)}\n"
    )


def transcript_json_payload(
    title: str,
    segments: Sequence[TranscriptSegment],
    *,
    language: str = "",
    source: str = "",
    quality: dict[str, Any] | None = None,
    duration: float = 0.0,
) -> dict[str, Any]:
    return {
        "title": title,
        "duration": round(
            duration or (segments[-1].end if segments else 0.0), 3
        ),
        "language": language,
        "source": source,
        "quality": quality,
        "segments": [segment.to_dict() for segment in segments],
    }


def write_transcript_artifacts(
    directory: Path,
    stem: str,
    segments: Sequence[TranscriptSegment],
    *,
    title: str,
    language: str = "",
    source: str = "",
    quality: dict[str, Any] | None = None,
    duration: float = 0.0,
) -> dict[str, Path]:
    """Write <stem>.json, <stem>.md and <stem>.srt; return the three paths."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    srt_path = directory / f"{stem}.srt"

    payload = transcript_json_payload(
        title,
        segments,
        language=language,
        source=source,
        quality=quality,
        duration=duration,
    )
    _write_text_atomic(
        json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    _write_text_atomic(
        markdown_path,
        transcript_markdown(
            title,
            segments,
            language=language,
            source=source,
            quality=quality,
            duration=duration,
        ),
    )
    _write_text_atomic(srt_path, segments_to_srt(segments), newline="")
    return {"json": json_path, "markdown": markdown_path, "srt": srt_path}


def load_transcript_artifact(path: Path) -> LoadedTranscript:
    """Read our JSON/Markdown/SRT output, or any .srt/.vtt/.json3 subtitle file."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"转录文件不存在：{path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json_transcript(path)
    if suffix in SUBTITLE_SUFFIXES:
        segments = parse_subtitle_file(path)
        if not segments:
            raise ValueError(f"字幕文件没有可读的时间轴内容：{path}")
        return LoadedTranscript(
            segments=segments,
            title=path.stem.removesuffix(".transcript"),
            source="imported_subtitle",
        )
    if suffix in {".md", ".txt"}:
        return _load_timestamped_text(path)
    raise ValueError(
        f"不支持的转录格式：{path.suffix or '无扩展名'}；"
        "支持 .json / .srt / .vtt / .json3 / .md / .txt"
    )


def _load_json_transcript(path: Path) -> LoadedTranscript:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON 转录 {path}：{exc}") from exc

    if isinstance(payload, list):
        data: dict[str, Any] = {"segments": payload}
    elif isinstance(payload, dict):
        data = payload
    else:
        raise ValueError(f"JSON 转录格式不对：{path}")

    segments = _coerce_segments(data.get("segments") or [])
    if not segments:
        raise ValueError(f"JSON 转录里没有 segments：{path}")
    quality = data.get("quality")
    return LoadedTranscript(
        segments=segments,
        title=str(data.get("title") or path.stem.removesuffix(".transcript")),
        language=str(data.get("language") or "zh"),
        source=str(data.get("source") or "transcript_file"),
        quality=quality if isinstance(quality, dict) else None,
    )


_TIMESTAMPED_LINE_RE = re.compile(
    r"^\[?(?P<start>\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*[-–—]\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(?P<text>.+?)\s*$"
)
_HEADING_TITLE_RE = re.compile(r"^#\s*(?:带时间戳转录[:：]\s*)?(?P<title>.+?)\s*$")


def _load_timestamped_text(path: Path) -> LoadedTranscript:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"无法读取转录 {path}：{exc}") from exc

    title = path.stem.removesuffix(".transcript")
    segments: list[TranscriptSegment] = []
    for line in lines:
        if not segments and (heading := _HEADING_TITLE_RE.match(line.strip())):
            candidate = heading.group("title")
            if candidate != "带时间戳转录":
                title = candidate
            continue
        match = _TIMESTAMPED_LINE_RE.match(line.strip())
        if not match:
            continue
        text = match.group("text").strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                _parse_clock(match.group("start")),
                _parse_clock(match.group("end")),
                text,
            )
        )
    if not segments:
        raise ValueError(
            f"转录 {path} 里没有 [MM:SS-MM:SS] 格式的时间轴行"
        )
    return LoadedTranscript(segments=segments, title=title)


def _coerce_segments(items: Sequence[Any]) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for item in items:
        if isinstance(item, TranscriptSegment):
            segment = item
        elif isinstance(item, dict):
            start = float(item.get("start") or 0.0)
            end = float(item.get("end") or start)
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            segment = TranscriptSegment(start, max(start, end), text)
        else:
            continue
        if segment.text.strip():
            segments.append(segment)
    return segments


def _parse_clock(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def unique_note_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.md"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{index}.md"
        index += 1
    return candidate


def unique_transcript_stem(directory: Path, stem: str) -> str:
    candidate = stem
    index = 2
    while any((directory / f"{candidate}{suffix}").exists() for suffix in (".json", ".md", ".srt")):
        candidate = f"{stem}-{index}"
        index += 1
    return candidate


def _write_text_atomic(path: Path, text: str, *, newline: str | None = None) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline=newline) as file:
        file.write(text)
    temporary.replace(path)
