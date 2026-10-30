from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Sequence

from .transcript import format_timestamp, parse_timestamp
from .video_frames import VideoFrame

SAFE_NAME_LIMIT = 80
# 目录名的长度上限是字节数（ext4 单个路径分量 255 字节），不是字符数：
# 一个汉字占 3 字节，80 个汉字再加时间戳就超了。
RUN_DIR_BYTE_LIMIT = 255
RUN_DIR_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
# 模型在正文里写的插图标记：[[图片 0003：说明文字]]，编号对应 frame_0003_*.jpg。
FRAME_PLACEHOLDER_RE = re.compile(
    r"\[\[\s*图片\s*(\d{1,4})\s*(?:[：:|｜]\s*([^\]\n]*?))?\s*\]\]"
)
# 正文插图整行：![说明](./<笔记>.frames/xxx.jpg)。Markdown 成品要去掉这些行。
FRAME_IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\(<?\./[^)>]+>?\)$")
# 模型一张图都没引用时末尾补的图廊标题；图去掉后这个标题也要一起清掉。
GALLERY_HEADING = "## 视频截图"
# 小标题自带的时间区间：## 标题 [01:30-04:00]，插图归属按它判断。
NOTE_BLOCK_SPAN_RE = re.compile(
    r"\[(\d{1,3}(?::\d{2}){1,2})\s*[-–~]\s*(\d{1,3}(?::\d{2}){1,2})\]"
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


def frame_directory_name(note_stem: str) -> str:
    """插图目录名：空格换成连字符，去掉链接里有歧义的字符。

    目录名带空格时，Markdown 里的图片只能写成 ``![](<./带 空格/a.jpg>)`` 这种尖括号
    形式，不少渲染器（终端预览、部分编辑器）认不出来，图片于是一张都不显示。
    """
    cleaned = "".join(
        "-" if char.isspace() else char
        for char in note_stem
        if char not in '()[]<>#?%&"\\'
    )
    return f"{cleaned or 'video'}.frames"


def image_markdown(caption: str, frames_dir_name: str, filename: str) -> str:
    """一张正文插图：路径里没有空格与括号，任何 Markdown 渲染器都能直接解析。"""
    return f"![{caption}](./{frames_dir_name}/{filename})"


def without_images(text: str) -> str:
    """去掉正文插图，得到纯文字 Markdown。

    图片仍留在 ``.frames/`` 与同名 HTML 里：HTML 是看图的那份，Markdown 只留文字，
    适合 grep、归档和再加工。
    """
    lines = text.splitlines()
    kept: list[str] = []
    for index, line in enumerate(lines):
        if FRAME_IMAGE_LINE_RE.match(line.strip()):
            continue
        if line.strip() == GALLERY_HEADING and _blank_until_footer(lines, index + 1):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip() + "\n"


def _blank_until_footer(lines: Sequence[str], start: int) -> bool:
    """这个标题后面是不是只剩空行（到页脚或文件结尾）：是就说明它已经是空节。"""
    for line in lines[start:]:
        stripped = line.strip()
        if stripped:
            return stripped == "---"
    return True


def append_screenshot_section(
    summary: str,
    frames_dir_name: str,
    frames: Sequence[VideoFrame],
) -> str:
    """Append a Markdown gallery for notes where the model used no inline frames."""
    lines = ["## 视频截图", ""]
    for frame in frames:
        label = f"截图 {frame.span_label}"
        lines.append(
            image_markdown(label, frames_dir_name, frame.filename)
        )
        lines.append("")
    return summary.rstrip() + "\n\n" + "\n".join(lines).rstrip() + "\n"


def resolve_frame_placeholders(
    text: str,
    frames_dir_name: str,
    frames: Sequence[VideoFrame],
) -> tuple[str, set[str], list[str]]:
    """把 ``[[图片 0003：说明]]`` 换成正文插图，并核对图是否落在所在小节的时间区间内。

    编号不存在、重复引用、越界这几种情况都会记进第三个返回值（issues），不再静默
    消失；越界的图先换成本小节里第一张没用过的画面，换不到才删。返回新文本、
    实际用到的编号、以及需要报告的问题。
    """
    by_id = {frame.frame_id: frame for frame in frames}
    used: set[str] = set()
    issues: list[str] = []
    section_count = 0
    active_section: int | None = None
    section_spans: dict[int, tuple[float, float]] = {}
    section_images: dict[int, int] = {}
    section_lines: dict[int, int] = {}
    block_span: tuple[float, float] | None = None

    def pick(span: tuple[float, float]) -> VideoFrame | None:
        """取这段区间里最能代表它的一张画面：起点落在段内的优先，其次才看区间覆盖。"""
        inside = [
            frame
            for frame in frames
            if frame.frame_id not in used and span[0] <= frame.seconds <= span[1]
        ]
        if inside:
            return inside[0]
        return next(
            (
                frame
                for frame in frames
                if frame.frame_id not in used and frame.covers(*span)
            ),
            None,
        )

    def replace(match: re.Match[str]) -> str:
        frame = by_id.get(f"{int(match.group(1)):04d}")
        caption = _clean_frame_caption(match.group(2))
        if frame is None:
            issues.append(
                f"笔记引用了不存在的截图 {match.group(1)}，已删除该处插图"
            )
            return ""
        if frame.frame_id in used:
            issues.append(f"截图 {frame.frame_id} 被重复引用，只保留第一次")
            return ""
        if block_span is not None and not frame.covers(*block_span):
            alternative = pick(block_span)
            where = (
                f"第 {active_section} 个小节 {_span_label(block_span)} 的插图"
                f" {frame.frame_id}（{frame.span_label}）越界"
            )
            if alternative is None:
                issues.append(f"{where}，且本小节没有可用截图，已删除")
                return ""
            issues.append(
                f"{where}，已改用 {alternative.frame_id}"
                f"（{alternative.span_label}）"
                + (f"，原说明“{caption}”不再适用" if caption else "")
            )
            frame = alternative
            # 换图后原说明描述的是另一张画面，改用这张图自身的时间区间。
            caption = ""
        used.add(frame.frame_id)
        if active_section is not None:
            section_images[active_section] = (
                section_images.get(active_section, 0) + 1
            )
        caption = caption or f"画面 {frame.span_label}"
        return image_markdown(caption, frames_dir_name, frame.filename)

    lines: list[str] = []
    for line in text.splitlines():
        heading = line.lstrip()
        if heading.startswith("#"):
            span = NOTE_BLOCK_SPAN_RE.search(heading)
            block_span = (
                (parse_timestamp(span.group(1)), parse_timestamp(span.group(2)))
                if span
                else None
            )
            if block_span is None:
                active_section = None
            else:
                section_count += 1
                active_section = section_count
                section_spans[section_count] = block_span
                section_lines[section_count] = len(lines)
        lines.append(FRAME_PLACEHOLDER_RE.sub(replace, line))
    filled: list[int] = []
    for index in sorted(section_spans, reverse=True):
        if section_images.get(index):
            continue
        span = section_spans[index]
        frame = pick(span)
        if frame is None:
            continue
        used.add(frame.frame_id)
        section_images[index] = 1
        anchor = section_lines[index] + 1
        lines[anchor:anchor] = [
            "",
            image_markdown(
                f"画面 {frame.span_label}", frames_dir_name, frame.filename
            ),
        ]
        filled.append(index)
    if filled:
        labels = "、".join(
            f"第 {index} 个（{_span_label(section_spans[index])}）"
            for index in sorted(filled)
        )
        issues.append("以下小节模型没写插图，已自动补上该段第一张画面：" + labels)
    return "\n".join(lines), used, issues


def _span_label(span: tuple[float, float]) -> str:
    return f"[{format_timestamp(span[0])}-{format_timestamp(span[1])}]"


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


def run_directory_name(title: str, started_at: datetime) -> str:
    """一次运行一个目录：``<标题>_<开始提取时间>``。

    时间戳是这次开始提取的时刻，不是落盘时刻——下载、转写、写笔记都要花时间。
    目录名按字节截断：单个路径分量上限 255 字节，一个汉字就占 3 字节。
    """
    stamp = started_at.strftime(RUN_DIR_TIMESTAMP_FORMAT)
    base = safe_filename(str(title or "").strip() or "video-note")
    # 留给时间戳（15 字节）、分隔符，还有重名时加的序号（"-100"）。
    base = _truncate_bytes(base, RUN_DIR_BYTE_LIMIT - len(stamp) - 1 - 4)
    return f"{base}_{stamp}"


def _truncate_bytes(value: str, limit: int) -> str:
    """按 UTF-8 字节截断，砍掉半个字时整字丢掉。"""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    truncated = encoded[:limit].decode("utf-8", errors="ignore")
    return truncated.strip() or "video-note"


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
