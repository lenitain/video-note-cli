from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Sequence

from .transcript import format_timestamp
from .video_frames import VideoFrame

SAFE_NAME_LIMIT = 80
API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
# 模型在正文里写的插图标记：[[图片 0003：说明文字]]，编号对应 frame_0003_*.jpg。
FRAME_PLACEHOLDER_RE = re.compile(
    r"\[\[\s*图片\s*(\d{1,4})\s*(?:[：:|｜]\s*([^\]\n]*?))?\s*\]\]"
)
MAX_FRAME_CAPTION_CHARACTERS = 40


def format_video_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    return format_timestamp(float(seconds))


def format_publish_time(info: dict[str, Any]) -> str:
    raw_date = str(info.get("upload_date") or info.get("release_date") or "").strip()
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    raw_timestamp = info.get("timestamp") or info.get("release_timestamp")
    try:
        timestamp = float(raw_timestamp)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def build_note_header(summary: str, title: str, info: dict[str, Any]) -> str:
    """Put deterministic, verifiable video metadata under the model's heading."""
    value = summary.strip()
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).lstrip()
    else:
        heading = f"# 视频笔记：《{title}》"
        body = value

    body = _strip_model_metadata_block(body)

    metadata: list[str] = []
    owner = str(info.get("owner") or "").strip()
    published_at = format_publish_time(info)
    duration = format_video_duration(float(info.get("duration") or 0))
    view_count = int(info.get("view_count") or 0)
    like_count = int(info.get("like_count") or 0)
    if owner:
        metadata.append(f"UP主：{owner}")
    if published_at:
        metadata.append(f"发布时间：{published_at}")
    if duration != "未知":
        metadata.append(f"时长：{duration}")
    if view_count > 0:
        metadata.append(f"播放：{view_count:,}")
    if like_count > 0:
        metadata.append(f"点赞：{like_count:,}")
    if not metadata:
        return value or heading
    metadata_block = "> " + " · ".join(metadata)
    return f"{heading}\n\n{metadata_block}" + (f"\n\n{body}" if body else "")


def _strip_model_metadata_block(body: str) -> str:
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    metadata_labels = ("作者", "UP主", "发布时间", "时长", "播放", "点赞", "文字来源")
    removed = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("> ")
        label_count = sum(
            f"{label}：" in line or f"{label}:" in line for label in metadata_labels
        )
        if label_count < 2:
            break
        removed += 1
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    if not removed:
        return body
    return "\n".join(lines[index:]).lstrip()


def summary_style_label(style: str) -> str:
    return {
        "detailed": "详细笔记 + 点评分析",
        "faithful": "详细复原（仅视频内容）",
        "concise": "精简摘要",
    }.get(style, style)


def append_screenshot_section(
    summary: str,
    frames_dir_name: str,
    frames: Sequence[VideoFrame],
) -> str:
    """Append a Markdown gallery for notes where the model used no inline frames."""
    lines = ["## 视频截图", ""]
    for frame in frames:
        label = f"截图 {frame.timestamp}"
        lines.append(
            f"![{label}](<./{frames_dir_name}/{frame.filename}>)"
        )
        lines.append("")
    return summary.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"


def resolve_frame_placeholders(
    text: str,
    frames_dir_name: str,
    frames: Sequence[VideoFrame],
) -> tuple[str, set[str]]:
    """Turn ``[[图片 0003：说明]]`` markers into inline Markdown images.

    Unknown ids and repeated uses of the same frame are dropped, so the note only
    points at images that exist and each picture appears once. Returns the new
    text and the ids that were actually placed.
    """
    by_id = {frame.frame_id: frame for frame in frames}
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        frame = by_id.get(f"{int(match.group(1)):04d}")
        if frame is None or frame.frame_id in used:
            return ""
        used.add(frame.frame_id)
        caption = _clean_frame_caption(match.group(2)) or f"画面 {frame.timestamp}"
        return f"![{caption}](<./{frames_dir_name}/{frame.filename}>)"

    resolved = FRAME_PLACEHOLDER_RE.sub(replace, text)
    return resolved, used


def _clean_frame_caption(value: str | None) -> str:
    text = re.sub(r"\s+", " ", (value or "").replace("[", "").replace("]", ""))
    return text.strip()[:MAX_FRAME_CAPTION_CHARACTERS]


def append_note_footer(
    summary: str,
    source_label: str,
    provider: str,
    model: str | None,
    style: str,
) -> str:
    footer = "\n".join(
        [
            "---",
            f"来源：{source_label or '未知'}",
            f"生成模型：{provider} · {model or '未指定'}",
            f"笔记风格：{summary_style_label(style)}",
        ]
    )
    return f"{summary.rstrip()}\n\n{footer}\n"


def safe_filename(value: str) -> str:
    """Remove characters that are illegal on common filesystems, keep Chinese punctuation."""
    invalid = set('\\/:*?"<>|')
    cleaned = "".join(
        char for char in value if char not in invalid and ord(char) >= 32
    )
    return (cleaned.strip() or "video-note")[:SAFE_NAME_LIMIT]


def artifact_stem(title: str, info: dict[str, Any], output: str = "note") -> str:
    """Build the output filename stem, including Bilibili page numbers and the transcript marker."""
    base = safe_filename(str(title or "").strip() or "video-note")
    suffix = ""
    pages = [
        page for page in (info.get("bili_pages") or []) if isinstance(page, dict)
    ]
    total = int(info.get("bili_total_pages") or 0)
    if total > 1 and pages:
        numbers = sorted(
            {int(page["page"]) for page in pages if page.get("page")}
        )
        if numbers:
            suffix = "-P" + ",".join(str(number) for number in numbers)
    if output == "transcript":
        suffix += ".transcript"
    if len(base) + len(suffix) > SAFE_NAME_LIMIT:
        base = base[: max(1, SAFE_NAME_LIMIT - len(suffix))]
    return safe_filename(f"{base}{suffix}")


_CONTEXT_LIMIT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "prompt is too long",
    "num_ctx",
    "maximum capacity of",
)


def friendly_llm_error_message(message: str) -> str:
    """Turn common OpenAI-compatible gateway errors into actionable Chinese hints."""
    lowered = message.lower()
    if any(marker in lowered for marker in _CONTEXT_LIMIT_MARKERS):
        return (
            "模型的上下文窗口装不下本次请求。本地模型（ollama / LM Studio / llama.cpp）"
            "请把上下文调到 16K 以上再重试；在线通道可换上下文更大的模型，"
            "或改用 --style concise 减少单次送入的材料。"
        )
    if "insufficient_quota" in lowered or "quota" in lowered or "余额" in message:
        return "模型通道的额度或余额不足；请更换通道或续额后重试。"
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return "模型通道限流；稍后重试，或改用付费通道/精简摘要。"
    if "401" in lowered or "invalid_api_key" in lowered or "incorrect api key" in lowered:
        return "模型接口拒绝了 API Key（401）；请检查配置里的 api_key。"
    if "403" in lowered or "permission" in lowered or "forbidden" in lowered:
        return "模型接口拒绝了本次请求（403）；请检查 Key 权限、Base URL 和模型名。"
    if "404" in lowered or "not found" in lowered or "does not exist" in lowered:
        return "模型接口找不到该模型（404）；请检查 model 与 base_url。"
    if "timeout" in lowered or "timed out" in lowered or "connection" in lowered:
        return "与模型接口的连接中断；可以直接重试，或更换通道。"
    return message


def redact_secrets(text: str, secret: str = "") -> str:
    cleaned = API_KEY_PATTERN.sub("sk-****", str(text))
    if secret and len(secret) >= 8:
        cleaned = cleaned.replace(secret, "****")
    return cleaned
