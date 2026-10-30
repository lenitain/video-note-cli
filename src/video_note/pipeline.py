from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .bili_cookies import resolve_bilibili_cookies
from .html_export import write_note_html
from .llm_summarizer import (
    LLMSummarizer,
    MAX_FRAMES_PER_REQUEST,
    PROVIDER_DEFAULTS,
    default_base_url,
    looks_like_vision_rejection,
    normalize_provider,
)
from .paraformer_asr import ParaformerTranscriber
from .render import (
    append_note_footer,
    append_screenshot_section,
    artifact_stem,
    build_note_header,
    format_publish_time,
    frame_directory_name,
    friendly_llm_error_message,
    redact_secrets,
    resolve_frame_placeholders,
    without_images,
)
from .screenshots import FrameSource, extract_frames_async
from .settings import (
    ASRSettings,
    LLMSettings,
    PROVIDER_API_KEY_ENV,
    UserSettings,
    model_cache_root,
    resolve_credentials,
    runs_root,
)
from .transcript import TranscriptSegment, format_timestamp, transcript_quality
from .transcript_files import (
    LoadedTranscript,
    load_transcript_artifact,
    note_output_paths,
    transcript_source_label,
    unique_note_stem,
    unique_transcript_stem,
    write_transcript_artifacts,
)
from .video_frames import VideoFrame, evenly_sample_frames
from .video_processor import (
    BiliPage,
    VideoProcessor,
    VideoSource,
    bilibili_page_url,
    merge_bilibili_pages,
    normalize_video_input,
)
from .whisper_asr import WHISPER_MODEL_REPOS, WhisperTranscriber


class PipelineError(Exception):
    """A user-facing failure with an actionable message."""


# 模型一张图都没引用时，末尾附图最多发这么多张：够用，也不把输出目录塞满。
MAX_GALLERY_FRAMES = 12


class CancelledRun(Exception):
    """Raised when the user presses Ctrl-C and the pipeline stops cleanly."""


@dataclass
class RunOptions:
    input_value: str
    output_dir: Path
    style: str = "detailed"
    effort: str = "auto"
    transcript_only: bool = False
    save_transcript: bool = False
    prefer_subtitles: bool = True
    pages: tuple[int, ...] = ()
    from_transcript: Path | None = None
    reuse: bool = True
    clean_media: bool = False
    screenshots: bool = False
    screenshot_interval: int = 30
    write_markdown_text: bool = True
    write_markdown_images: bool = False
    write_html: bool = True
    cookie_file: Path | None = None


@dataclass
class ResolvedInput:
    kind: str
    source: str
    source_label: str
    title: str
    info: dict[str, Any]
    media_path: Path | None = None
    loaded: LoadedTranscript | None = None


@dataclass(frozen=True)
class TranscriptBundle:
    segments: list[TranscriptSegment]
    language: str
    source: str
    quality: dict[str, Any]
    duration: float


@dataclass(frozen=True)
class RunResult:
    title: str
    note_files: list[Path]
    transcript_paths: dict[str, Path]
    segments: int
    source: str
    duration: float


class Cancellation:
    """SIGINT handler shared by downloads, ASR and LLM calls."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self._previous: Any = None

    def install(self) -> None:
        try:
            self._previous = signal.signal(signal.SIGINT, self._handle)
        except ValueError:
            self._previous = None

    def restore(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    def _handle(self, _signum: int, _frame: Any) -> None:
        if self.event.is_set():
            raise KeyboardInterrupt
        self.event.set()
        log("收到 Ctrl-C，正在停止；再按一次强制退出")

    def check(self) -> None:
        if self.event.is_set():
            raise CancelledRun("任务已取消")

    def is_set(self) -> bool:
        return self.event.is_set()


class AsrRunner:
    """Lazily create one ASR engine per process so multi-page runs reuse the model."""

    def __init__(self, settings: ASRSettings, cancel: Cancellation) -> None:
        self.settings = settings
        self.cancel = cancel
        self.download_root = model_cache_root()
        self._whisper: WhisperTranscriber | None = None
        self._paraformer: ParaformerTranscriber | None = None

    async def transcribe(self, media_path: Path, title: str) -> dict[str, Any]:
        self.cancel.check()
        if self.settings.model == "paraformer-zh":
            if self._paraformer is None:
                log("加载 Paraformer 中文模型（首次使用会下载模型）")
                self._paraformer = ParaformerTranscriber(self.download_root)
            result = await self._paraformer.transcribe(
                media_path,
                model_name=self.settings.model,
                use_gpu=self.settings.device == "cuda",
                cancel_event=self.cancel.event,
            )
        else:
            if self._whisper is None:
                log(
                    f"加载 faster-whisper 模型 {self.settings.model}"
                    "（未缓存时会下载）"
                )
                self._whisper = WhisperTranscriber(self.download_root)
            result = await self._whisper.transcribe(
                media_path,
                model_name=self.settings.model,
                use_gpu=self.settings.device == "cuda",
                initial_prompt=title,
                cancel_event=self.cancel.event,
                progress_callback=self._report_progress,
            )
        self.cancel.check()
        return result

    @staticmethod
    def _report_progress(done: float, total: float) -> None:
        if total > 0:
            log(
                f"转写进度 {done / total:.0%}"
                f"（{format_timestamp(done)} / {format_timestamp(total)}）"
            )
        else:
            log(f"转写进度 {format_timestamp(done)}")


def log(message: str) -> None:
    print(f"[video-note] {message}", file=sys.stderr, flush=True)


async def run_video_note_pipeline(
    options: RunOptions,
    settings: UserSettings,
    cancel: Cancellation | None = None,
) -> RunResult:
    """Run the whole video -> transcript -> note pipeline for one input."""
    cancel = cancel or Cancellation()
    _validate_options(options, settings)
    # Key 的来源定案（命令行 --provider 也在内，所以放在这一步）
    settings = resolve_credentials(settings)

    summarizer: LLMSummarizer | None = None
    if not options.transcript_only:
        summarizer = build_llm_summarizer(settings.llm)

    video_processor = VideoProcessor(runs_root())
    cookies = resolve_bilibili_cookies(settings.bilibili, options.cookie_file)
    asr = AsrRunner(settings.asr, cancel)

    try:
        resolved = await _resolve_input(
            options, video_processor, cookies, cancel
        )
        log(f"标题：{resolved.title}")
        if resolved.info.get("duration"):
            log(f"时长：{format_timestamp(float(resolved.info['duration']))}")

        bundle = await _obtain_transcript(
            resolved,
            options,
            settings,
            video_processor,
            asr,
            cookies,
            cancel,
        )
        _check_quality(bundle, options)

        # 笔记主干先定下来：插图要直接抽到产物目录里的 <笔记>.frames，不再经过缓存。
        # 几件成品共用同一个主干，各自的名字因此互不影响。
        note_stem = (
            None
            if options.transcript_only
            else unique_note_stem(
                options.output_dir,
                artifact_stem(resolved.title, resolved.info, "note"),
            )
        )

        frames: list[VideoFrame] = []
        if options.screenshots:
            if options.transcript_only:
                log("仅转录模式不生成笔记，跳过截图")
            elif resolved.kind in {"subtitle", "transcript"}:
                log("当前输入没有视频画面，跳过截图")
            else:
                assert note_stem is not None
                frames = await _prepare_screenshots(
                    resolved,
                    options,
                    settings,
                    video_processor,
                    cookies,
                    cancel,
                    bundle.segments,
                    _frames_directory(options.output_dir, note_stem),
                )

        if options.transcript_only:
            transcript_paths = _write_transcript_output(
                resolved, bundle, options, stem=None
            )
            log(
                f"转录完成：{len(bundle.segments)} 段，"
                f"{bundle.quality.get('characters', 0)} 字"
            )
            _clean_run_cache(resolved, options, settings)
            return RunResult(
                title=resolved.title,
                note_files=[],
                transcript_paths=transcript_paths,
                segments=len(bundle.segments),
                source=bundle.source,
                duration=bundle.duration,
            )

        assert summarizer is not None
        assert note_stem is not None
        note_files, transcript_paths = await _generate_note(
            note_stem,
            resolved,
            bundle,
            options,
            settings,
            summarizer,
            cancel,
            frames,
        )
        log("笔记本完成：" + "，".join(str(path) for path in note_files))
        _clean_run_cache(resolved, options, settings)
        return RunResult(
            title=resolved.title,
            note_files=note_files,
            transcript_paths=transcript_paths,
            segments=len(bundle.segments),
            source=bundle.source,
            duration=bundle.duration,
        )
    except asyncio.CancelledError as exc:
        raise CancelledRun("任务已取消") from exc


def build_llm_summarizer(settings: LLMSettings) -> LLMSummarizer:
    """Validate LLM settings early, before a long download or transcription."""
    provider = normalize_provider(settings.provider or "deepseek")
    base_url = settings.base_url.strip() or default_base_url(provider)
    model = settings.model.strip() or None

    if provider == "custom":
        if not base_url or not model:
            raise PipelineError(
                "provider=custom 需要同时设置 base_url 和 model；"
                "请在配置文件的 [llm] 段填写，或使用 --base-url/--model"
            )
    elif provider not in PROVIDER_DEFAULTS:
        if base_url and model:
            provider = "custom"
        else:
            supported = "、".join(sorted(PROVIDER_DEFAULTS))
            raise PipelineError(
                f"未知 provider：{settings.provider}；可用：{supported}，"
                "或设置 base_url + model 走自定义接口"
            )

    api_key = settings.api_key.strip()
    if not api_key:
        if _is_local_endpoint(base_url):
            api_key = "local"
        else:
            hint = ""
            names = PROVIDER_API_KEY_ENV.get(provider, ())
            if names:
                hint = f"（该 provider 惯用 {names[0]}）"
            raise PipelineError(
                "生成笔记需要 API Key。请在 ~/.config/video-note/config.toml 的 "
                "[llm] 段填写 api_key，或用 api_key_env 指定环境变量"
                f"{hint}；"
                "只要带时间轴的转录稿时加 --transcript-only，不需要 Key。"
            )

    try:
        return LLMSummarizer(
            provider,
            api_key,
            base_url=base_url or None,
            model=model,
        )
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc


async def _resolve_input(
    options: RunOptions,
    video_processor: VideoProcessor,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
) -> ResolvedInput:
    cancel.check()
    if options.from_transcript is not None:
        loaded = load_transcript_artifact(options.from_transcript)
        title = _strip_transcript_suffix(
            loaded.title or options.from_transcript.stem
        )
        return ResolvedInput(
            kind="transcript",
            source=str(options.from_transcript),
            source_label=f"转录文件：{options.from_transcript}",
            title=title,
            info=_info_from_segments(title, loaded.segments),
            loaded=loaded,
        )

    value = (options.input_value or "").strip()
    path = Path(value).expanduser()
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in {".srt", ".vtt", ".json", ".json3"}:
            loaded = load_transcript_artifact(path)
            title = _strip_transcript_suffix(loaded.title or path.stem)
            return ResolvedInput(
                kind="subtitle",
                source=str(path.resolve()),
                source_label=f"字幕文件：{path.resolve()}",
                title=title,
                info=_info_from_segments(title, loaded.segments),
                loaded=loaded,
            )
        resolved_path = path.resolve()
        return ResolvedInput(
            kind="local_media",
            source=str(resolved_path),
            source_label=f"本地文件：{resolved_path}",
            title=path.stem,
            info={
                "title": path.stem,
                "source": "local",
                "duration": 0,
                "owner": "",
                "upload_date": "",
                "view_count": 0,
                "like_count": 0,
                "description": "",
            },
            media_path=resolved_path,
        )

    normalized = normalize_video_input(value)
    if not normalized:
        raise PipelineError(
            "无法识别输入：请提供视频链接、本地音视频文件，"
            "或 .srt/.vtt/.json 字幕文件"
        )
    try:
        source_kind = video_processor.detect_source(normalized)
    except ValueError as exc:
        raise PipelineError(str(exc)) from exc
    if source_kind == VideoSource.BILIBILI:
        resolved_url = video_processor.resolve_bilibili_short_url(normalized)
        if resolved_url and resolved_url != normalized:
            log(f"展开短链：{resolved_url}")
            normalized = resolved_url

    log(f"读取视频信息：{normalized}")
    try:
        info = await video_processor.get_video_info(
            normalized, cookies, allow_local=False
        )
    except Exception as exc:
        raise PipelineError(_friendly_source_error(exc, source_kind)) from exc
    title = str(info.get("title") or "video")
    return ResolvedInput(
        kind="url",
        source=normalized,
        source_label=normalized,
        title=title,
        info=info,
    )


async def _obtain_transcript(
    resolved: ResolvedInput,
    options: RunOptions,
    settings: UserSettings,
    video_processor: VideoProcessor,
    asr: AsrRunner,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
) -> TranscriptBundle:
    if resolved.loaded is not None:
        log(
            f"使用{transcript_source_label(resolved.loaded.source)}："
            f"{len(resolved.loaded.segments)} 段"
        )
        return _bundle_from_loaded(resolved.loaded)

    run_dir = _run_directory(resolved, options, settings)
    if options.reuse:
        cached = _load_cached_transcript(run_dir)
        if cached is not None:
            log(f"复用已有转录：{run_dir / 'transcript.json'}")
            _restore_run_metadata(run_dir, resolved)
            return cached

    if resolved.kind == "url":
        bundle = await _transcribe_url(
            resolved,
            options,
            video_processor,
            asr,
            cookies,
            cancel,
            run_dir.name,
        )
    else:
        assert resolved.media_path is not None
        log(f"转写本地文件：{resolved.media_path}")
        result = await asr.transcribe(resolved.media_path, resolved.title)
        bundle = _bundle_from_asr(result, resolved.info)

    _write_cached_transcript(run_dir, resolved, bundle)
    return bundle


async def _transcribe_url(
    resolved: ResolvedInput,
    options: RunOptions,
    video_processor: VideoProcessor,
    asr: AsrRunner,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    run_key: str,
) -> TranscriptBundle:
    url = resolved.source
    if options.prefer_subtitles:
        log("查找平台字幕")
        try:
            subtitle = await video_processor.fetch_subtitles(
                url, resolved.info, cookies
            )
        except Exception as exc:
            log(f"读取平台字幕失败，继续尝试其他来源：{redact_secrets(str(exc))}")
            subtitle = None
        if subtitle is not None:
            log(
                f"使用平台字幕：{subtitle.language}，"
                f"{len(subtitle.segments)} 段"
            )
            return _bundle(
                subtitle.segments,
                subtitle.language,
                subtitle.source,
                resolved.info,
            )

    if video_processor.detect_source(url) == VideoSource.BILIBILI:
        return await _transcribe_bilibili(
            resolved,
            options,
            video_processor,
            asr,
            cookies,
            cancel,
            run_key,
        )

    return await _transcribe_url_audio(
        resolved, video_processor, asr, cookies, cancel, run_key
    )


async def _transcribe_bilibili(
    resolved: ResolvedInput,
    options: RunOptions,
    video_processor: VideoProcessor,
    asr: AsrRunner,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    run_key: str,
) -> TranscriptBundle:
    url = resolved.source
    log("查找 B 站 AI 字幕")
    try:
        outcome = await video_processor.fetch_bilibili_subtitles(
            url, cookies, only_pages=options.pages or None
        )
    except Exception as exc:
        log(f"读取 B 站 AI 字幕失败，改用语音转写：{redact_secrets(str(exc))}")
        return await _transcribe_url_audio(
            resolved, video_processor, asr, cookies, cancel, run_key
        )

    if outcome.title:
        resolved.title = outcome.title
        resolved.info["title"] = outcome.title
    if outcome.pages:
        resolved.info["bili_pages"] = [
            {
                "page": page.page,
                "part": page.part,
                "duration": page.duration,
            }
            for page in outcome.pages
        ]
        resolved.info["bili_total_pages"] = outcome.total_pages
        resolved.info["duration"] = (
            sum(page.duration for page in outcome.pages)
            or resolved.info.get("duration")
        )
        if outcome.total_pages > 1:
            log(
                f"B 站分 P：共 {outcome.total_pages} 个，本次处理 "
                f"{_format_page_numbers(outcome.pages)}"
                "（可用 --pages 1,3 限定）"
            )

    if outcome.result is not None:
        log(
            f"使用 B 站 AI 字幕：{outcome.result.language}，"
            f"{len(outcome.result.segments)} 段"
        )
        whisper_by_page: dict[int, dict[str, Any]] = {}
        if outcome.pages_to_transcribe:
            log(
                f"分 P {_format_page_numbers(outcome.pages_to_transcribe)} "
                "没有 AI 字幕，转写补齐"
            )
            whisper_by_page = await _transcribe_bilibili_pages(
                resolved,
                outcome.pages_to_transcribe,
                video_processor,
                asr,
                cookies,
                cancel,
                run_key,
            )
        if whisper_by_page:
            segments, language = merge_bilibili_pages(
                outcome.pages,
                dict(outcome.subtitle_by_page),
                whisper_by_page,
            )
            return _bundle(
                segments, language, "bilibili_multi_page", resolved.info
            )
        return _bundle(
            outcome.result.segments,
            outcome.result.language,
            outcome.result.source,
            resolved.info,
        )

    if outcome.pages_to_transcribe:
        if outcome.reason == "credentials_missing":
            log(
                "未配置 B 站 Cookie，无法读取 AI 字幕；"
                "可在 [bilibili] 填 sessdata，或用 --cookie-file 传入 cookies.txt"
            )
        elif outcome.reason == "no_track":
            log("当前视频没有可用的 B 站 AI 字幕轨")
        elif outcome.reason == "error":
            log(f"读取 B 站 AI 字幕出错：{outcome.detail[:200]}")
        log("改用语音转写")
        whisper_by_page = await _transcribe_bilibili_pages(
            resolved,
            outcome.pages_to_transcribe,
            video_processor,
            asr,
            cookies,
            cancel,
            run_key,
        )
        if outcome.pages:
            segments, language = merge_bilibili_pages(
                outcome.pages, {}, whisper_by_page
            )
        else:
            segments = [
                segment
                for result in whisper_by_page.values()
                for segment in result.get("segments") or []
            ]
            language = next(
                (
                    str(result.get("language") or "zh")
                    for result in whisper_by_page.values()
                ),
                "zh",
            )
        if len(whisper_by_page) == 1:
            source = _asr_source_from_result(next(iter(whisper_by_page.values())))
        else:
            source = "bilibili_multi_page"
        return _bundle(segments, language, source, resolved.info)

    if outcome.reason == "credentials_missing":
        log(
            "未配置 B 站 Cookie，无法读取 AI 字幕；可直接语音转写，"
            "或用 --cookie-file 传入浏览器导出的 cookies.txt"
        )
    elif outcome.reason == "no_track":
        log("当前视频没有可用的 B 站 AI 字幕轨")
    elif outcome.reason == "error":
        log(f"读取 B 站 AI 字幕出错：{outcome.detail[:200]}")
    return await _transcribe_url_audio(
        resolved, video_processor, asr, cookies, cancel, run_key
    )


async def _transcribe_bilibili_pages(
    resolved: ResolvedInput,
    pages: Sequence[BiliPage],
    video_processor: VideoProcessor,
    asr: AsrRunner,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    run_key: str,
) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for page in pages:
        cancel.check()
        log(f"分 P {page.page}（{page.part}）：准备音频")
        media_path = await _download_audio(
            video_processor,
            bilibili_page_url(resolved.source, page.page),
            run_key,
            cookies,
            media_name=f"audio_p{page.page}",
            cancel=cancel,
        )
        log(f"分 P {page.page}：语音转写")
        result = await asr.transcribe(
            media_path, f"{resolved.title} P{page.page} {page.part}"
        )
        results[page.page] = result
        log(
            f"分 P {page.page} 转写完成："
            f"{len(result.get('segments') or [])} 段"
        )
    return results


async def _transcribe_url_audio(
    resolved: ResolvedInput,
    video_processor: VideoProcessor,
    asr: AsrRunner,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    run_key: str,
) -> TranscriptBundle:
    media_path = await _download_audio(
        video_processor,
        resolved.source,
        run_key,
        cookies,
        media_name="audio",
        cancel=cancel,
    )
    log("语音转写")
    result = await asr.transcribe(media_path, resolved.title)
    return _bundle_from_asr(result, resolved.info)


async def _download_audio(
    video_processor: VideoProcessor,
    url: str,
    run_key: str,
    cookies: dict[str, str] | None,
    *,
    media_name: str,
    cancel: Cancellation,
) -> Path:
    run_dir = runs_root() / run_key
    cached = _find_cached_media(run_dir, media_name)
    if cached is not None:
        log(f"复用已下载音频：{cached.name}")
        return cached
    log(f"下载音频：{url}")
    try:
        return await video_processor.download_audio(
            url,
            run_key,
            cookies,
            media_name=media_name,
            should_abort=cancel.is_set,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise PipelineError(_friendly_download_error(exc, url)) from exc


def _asr_source_from_result(result: dict[str, Any]) -> str:
    requested = str(result.get("requested_model") or "")
    actual = str(result.get("model") or "")
    return (
        "paraformer"
        if "paraformer" in requested or "paraformer" in actual
        else "faster_whisper"
    )


def _bundle_from_asr(
    result: dict[str, Any], info: dict[str, Any]
) -> TranscriptBundle:
    segments = list(result.get("segments") or [])
    if not segments:
        raise PipelineError(
            "没有从音频里识别出文字。可能是纯音乐、静音视频，或模型不支持该语言。"
        )
    requested_model = str(result.get("requested_model") or "")
    actual_model = str(result.get("model") or "")
    source = _asr_source_from_result(result)
    language = str(result.get("language") or "zh")
    duration = float(
        result.get("duration")
        or (segments[-1].end if segments else 0.0)
    )
    if not info.get("duration"):
        info["duration"] = duration
    if result.get("fallback_note"):
        log(str(result["fallback_note"]))
    if actual_model and requested_model and actual_model != requested_model:
        log(
            f"所选模型 {requested_model} 未缓存或无法加载，"
            f"已降级为 {actual_model}"
        )
    log(
        f"转写完成：{len(segments)} 段，语言 {language}，"
        f"设备 {result.get('device', '未知')}，耗时 "
        f"{result.get('transcribe_seconds', '?')} 秒"
    )
    return _bundle(segments, language, source, info)


def _bundle(
    segments: Sequence[TranscriptSegment],
    language: str,
    source: str,
    info: dict[str, Any],
) -> TranscriptBundle:
    segments = list(segments)
    if not segments:
        raise PipelineError("没有获得可用的字幕或转录文字")
    duration = float(
        info.get("duration")
        or (segments[-1].end if segments else 0.0)
    )
    if not info.get("duration"):
        info["duration"] = duration
    quality = transcript_quality(segments, duration)
    return TranscriptBundle(
        segments=segments,
        language=language or "zh",
        source=source,
        quality=quality,
        duration=duration,
    )


def _bundle_from_loaded(loaded: LoadedTranscript) -> TranscriptBundle:
    duration = loaded.segments[-1].end if loaded.segments else 0.0
    quality = loaded.quality or transcript_quality(loaded.segments, duration)
    return TranscriptBundle(
        segments=list(loaded.segments),
        language=loaded.language or "zh",
        source=loaded.source or "transcript_file",
        quality=quality,
        duration=duration,
    )


def _check_quality(bundle: TranscriptBundle, options: RunOptions) -> None:
    characters = int(bundle.quality.get("characters") or 0)
    coverage = float(bundle.quality.get("speech_coverage") or 0.0)
    log(f"文字质量：{characters} 字，语音覆盖 {coverage:.0%}")
    if (
        bundle.source in {"faster_whisper", "paraformer"}
        and bundle.quality.get("insufficient")
    ):
        stage = "返回转录" if options.transcript_only else "调用大模型"
        raise PipelineError(
            f"可识别语音覆盖过低，已在{stage}前停止。"
            "源视频可能被静音、替换、受版权限制，或主要内容并非语音；"
            "请先检查原视频。"
        )


def _run_directory(
    resolved: ResolvedInput, options: RunOptions, settings: UserSettings
) -> Path:
    identity = _cache_identity(resolved, options, settings)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return runs_root() / key


def _cache_identity(
    resolved: ResolvedInput, options: RunOptions, settings: UserSettings
) -> str:
    pages = ",".join(str(page) for page in options.pages)
    common = (
        f"asr={settings.asr.model}|device={settings.asr.device}"
        f"|subtitles={int(options.prefer_subtitles)}|pages={pages}"
    )
    if resolved.kind == "url":
        return f"url:{resolved.source}|{common}"
    if resolved.media_path is not None:
        stat = resolved.media_path.stat()
        return (
            f"file:{resolved.media_path}|size={stat.st_size}"
            f"|mtime={stat.st_mtime_ns}|{common}"
        )
    return f"input:{resolved.source}|{common}"


def _load_cached_transcript(run_dir: Path) -> TranscriptBundle | None:
    path = run_dir / "transcript.json"
    if not path.is_file():
        return None
    try:
        loaded = load_transcript_artifact(path)
    except (OSError, ValueError):
        return None
    return _bundle_from_loaded(loaded)


def _write_cached_transcript(
    run_dir: Path, resolved: ResolvedInput, bundle: TranscriptBundle
) -> None:
    write_transcript_artifacts(
        run_dir,
        "transcript",
        bundle.segments,
        title=resolved.title,
        language=bundle.language,
        source=bundle.source,
        quality=bundle.quality,
        duration=bundle.duration,
    )
    metadata = {
        "title": resolved.title,
        "source": resolved.source,
        "duration": bundle.duration,
        "bili_pages": resolved.info.get("bili_pages") or [],
        "bili_total_pages": resolved.info.get("bili_total_pages") or 0,
    }
    (run_dir / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _restore_run_metadata(run_dir: Path, resolved: ResolvedInput) -> None:
    path = run_dir / "run.json"
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    pages = payload.get("bili_pages")
    if not isinstance(pages, list) or not pages:
        return
    resolved.info["bili_pages"] = pages
    total = int(payload.get("bili_total_pages") or 0)
    if total:
        resolved.info["bili_total_pages"] = total
    duration = payload.get("duration")
    try:
        if duration:
            resolved.info["duration"] = float(duration)
    except (TypeError, ValueError):
        pass


def _write_transcript_output(
    resolved: ResolvedInput,
    bundle: TranscriptBundle,
    options: RunOptions,
    *,
    stem: str | None,
) -> dict[str, Path]:
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    base_stem = stem or artifact_stem(
        resolved.title, resolved.info, "transcript"
    )
    unique_stem = unique_transcript_stem(output_dir, base_stem)
    paths = write_transcript_artifacts(
        output_dir,
        unique_stem,
        bundle.segments,
        title=resolved.title,
        language=bundle.language,
        source=bundle.source,
        quality=bundle.quality,
        duration=bundle.duration,
    )
    for path in paths.values():
        log(f"转录文件：{path}")
    return paths


async def _generate_note(
    note_stem: str,
    resolved: ResolvedInput,
    bundle: TranscriptBundle,
    options: RunOptions,
    settings: UserSettings,
    summarizer: LLMSummarizer,
    cancel: Cancellation,
    frames: Sequence[VideoFrame],
) -> tuple[list[Path], dict[str, Path]]:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    note_paths = note_output_paths(options.output_dir, note_stem)

    resolved.info["duration"] = (
        resolved.info.get("duration") or bundle.duration
    )
    metadata = {
        "owner": resolved.info.get("owner"),
        "published_at": format_publish_time(resolved.info),
        "duration_text": format_timestamp(bundle.duration),
        "view_count": resolved.info.get("view_count") or 0,
        "like_count": resolved.info.get("like_count") or 0,
        "transcript_source": bundle.source,
        "description": (resolved.info.get("description") or "")[:600],
    }

    async def report_progress(progress: int, message: str) -> None:
        if cancel.is_set():
            raise asyncio.CancelledError("任务已取消")
        log(f"笔记生成 {progress:>3}%：{message}")

    source = settings.llm.api_key_source
    log(
        f"调用模型：provider={summarizer.model_type}，"
        f"model={summarizer.model}，base_url={summarizer.base_url}"
        + (f"，Key 来自{source}" if source else "")
    )
    log(f"推理设置：{summarizer.describe_effort(options.effort, options.style)}")
    if frames:
        log(
            f"可用截图：{len(frames)} 张"
            f"（按段落分发，单次请求最多 {MAX_FRAMES_PER_REQUEST} 张）"
        )
    try:
        summary = await summarizer.generate_summary(
            resolved.title,
            bundle.segments,
            metadata,
            style=options.style,
            reasoning_effort=options.effort,
            progress_callback=report_progress,
            should_abort=cancel.is_set,
            frames=frames,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if frames and looks_like_vision_rejection(exc):
            raise PipelineError(
                "当前模型不接受图片输入，而图文笔记需要视觉模型。请换用支持图片的 "
                "provider/模型（如 MiniMax-M3.1-Flash、gpt-4o、qwen-vl、glm-4v 系列），"
                "或加 --no-screenshots 只生成文字笔记。"
            ) from exc
        message = redact_secrets(
            str(exc), settings.llm.api_key.strip()
        )
        raise PipelineError(friendly_llm_error_message(message)) from exc

    cancel.check()
    for warning in getattr(summarizer, "warnings", []):
        cleaned = redact_secrets(
            str(warning), settings.llm.api_key.strip()
        )
        log(f"提示：{cleaned}")
    if not summary.strip():
        raise PipelineError("模型返回了空笔记；请更换模型或改用 --transcript-only")

    summary = build_note_header(summary, resolved.title, resolved.info)
    if frames:
        summary = _publish_note_frames(
            summary, frames, frames[0].path.parent.name
        )
    summary = append_note_footer(
        summary,
        resolved.source_label,
        summarizer.model_type,
        summarizer.model,
        options.style,
    )
    # 三件成品各写各的：带图 Markdown、纯文字 Markdown、单文件 HTML。
    # 同一份正文，带图的用原样，纯文字的去掉插图行。
    note_files: list[Path] = []
    if options.write_markdown_text:
        note_paths["text"].write_text(
            without_images(summary), encoding="utf-8"
        )
        note_files.append(note_paths["text"])
    if options.write_markdown_images:
        note_paths["images"].write_text(summary, encoding="utf-8")
        note_files.append(note_paths["images"])
    if options.write_html:
        try:
            note_files.append(write_note_html(note_paths["html"], summary))
        except OSError as exc:
            log(f"HTML 导出失败：{exc}")

    transcript_paths: dict[str, Path] = {}
    if options.save_transcript:
        transcript_paths = _write_transcript_output(
            resolved,
            bundle,
            options,
            stem=f"{note_stem}.transcript",
        )
    return note_files, transcript_paths


def _frames_directory(output_dir: Path, note_stem: str) -> Path:
    """插图目录就放在笔记旁边：``<笔记>.frames/``。

    抽帧直接写到这里，产物目录里既有成品的 HTML，也有原始截图；缓存里不再留图片。
    """
    name = frame_directory_name(note_stem)
    candidate = output_dir / name
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{name}-{index}"
        index += 1
    return candidate


def _publish_note_frames(
    summary: str,
    frames: Sequence[VideoFrame],
    frames_dir_name: str,
) -> str:
    """把模型写的插图标记换成正文图片（截图已经在本目录里，不再搬运）。

    模型一张都没标记时，末尾补一个图廊，保证成品里仍有画面。
    """
    summary, used_ids, issues = resolve_frame_placeholders(
        summary, frames_dir_name, frames
    )
    for issue in issues:
        log(f"提示：{issue}")
    published = [frame for frame in frames if frame.frame_id in used_ids]
    if published:
        log(f"正文插图：{len(published)} 张")
        return summary
    published = evenly_sample_frames(frames, MAX_GALLERY_FRAMES)
    log(f"模型未在正文引用截图，改为末尾附图 {len(published)} 张")
    return append_screenshot_section(summary, frames_dir_name, published)


async def _prepare_screenshots(
    resolved: ResolvedInput,
    options: RunOptions,
    settings: UserSettings,
    video_processor: VideoProcessor,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    segments: Sequence[TranscriptSegment],
    frames_dir: Path,
) -> list[VideoFrame]:
    """Download one preview per 分P (or use the local file), then cut 画面段落.

    截图直接写进产物目录的 ``<笔记>.frames/``；缓存里只留音频与预览视频。
    """
    run_dir = _run_directory(resolved, options, settings)
    try:
        sources = await _frame_sources(
            resolved, video_processor, cookies, cancel, run_dir, segments
        )
        cancel.check()
        log(
            "抽帧：按画面切换切分，每段留一张代表帧"
            f"（{'、'.join(source.path.name for source in sources)}）"
        )
        frames = await extract_frames_async(
            sources,
            frames_dir,
            options.screenshot_interval,
            cancel.is_set,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log(f"截图失败，笔记仍会继续生成：{redact_secrets(str(exc))}")
        return []

    if not frames:
        log("视频里没有可提取的画面，跳过截图")
        return []
    log(
        f"已提取 {len(frames)} 张截图，"
        f"覆盖 00:00–{format_timestamp(frames[-1].span_end)}"
    )
    return frames


async def _frame_sources(
    resolved: ResolvedInput,
    video_processor: VideoProcessor,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    run_dir: Path,
    segments: Sequence[TranscriptSegment],
) -> list[FrameSource]:
    """每个分 P 一个预览视频，各自带着自己在合并时间轴上的偏移。"""
    if resolved.kind != "url":
        assert resolved.media_path is not None
        return [FrameSource(resolved.media_path)]

    pages = _bilibili_frame_pages(resolved, segments)
    if not pages:
        preview = await _download_preview_video(
            video_processor, resolved.source, run_dir.name, cookies, cancel
        )
        return [FrameSource(preview)]

    sources: list[FrameSource] = []
    for page, url, offset in pages:
        preview = await _download_preview_video(
            video_processor,
            url,
            run_dir.name,
            cookies,
            cancel,
            media_name=f"preview_p{page}",
        )
        sources.append(FrameSource(preview, offset))
    return sources


def _bilibili_frame_pages(
    resolved: ResolvedInput, segments: Sequence[TranscriptSegment]
) -> list[tuple[int, str, float]]:
    """每个分 P 的 (页码, 播放地址, 合并时间轴偏移)；单 P 或偏移不可知时返回空。

    偏移优先取接口给的分 P 时长——``merge_bilibili_pages`` 累加的就是同一个值；
    读缓存转录时 run.json 里可能没有时长，退回用转录里的「【P{n} 分P名】」标记定位。
    """
    pages = resolved.info.get("bili_pages") or []
    if len(pages) < 2:
        return []
    numbers = [int(page.get("page") or 0) for page in pages]
    offsets = _page_offsets(pages) or _page_offsets_from_markers(
        numbers, segments
    )
    if not offsets:
        log(
            "分 P 时长未知，预览视频只能覆盖第 1 个分 P，"
            "后半部分没有截图；加 --no-reuse 或重新转写可恢复"
        )
        return []
    return [
        (number, bilibili_page_url(resolved.source, number), offsets[number])
        for number in numbers
    ]


def _page_offsets(pages: Sequence[dict[str, Any]]) -> dict[int, float]:
    offsets: dict[int, float] = {}
    cursor = 0.0
    for page in pages:
        try:
            number = int(page.get("page") or 0)
            duration = float(page.get("duration") or 0)
        except (TypeError, ValueError):
            return {}
        if number <= 0 or duration <= 0:
            return {}
        offsets[number] = cursor
        cursor += duration
    return offsets


def _page_offsets_from_markers(
    numbers: Sequence[int], segments: Sequence[TranscriptSegment]
) -> dict[int, float]:
    """用转录里的分 P 标记反推各 P 起点：每 P 的第一段带着「【P{n} …】」。"""
    offsets = {numbers[0]: 0.0}
    for number in numbers[1:]:
        marker = f"【P{number} "
        segment = next(
            (item for item in segments if item.text.startswith(marker)), None
        )
        if segment is None:
            return {}
        offsets[number] = segment.start
    return offsets


async def _download_preview_video(
    video_processor: VideoProcessor,
    url: str,
    run_key: str,
    cookies: dict[str, str] | None,
    cancel: Cancellation,
    media_name: str = "preview",
) -> Path:
    run_dir = runs_root() / run_key
    cached = _find_cached_media(run_dir, media_name)
    if cached is not None:
        log(f"复用已下载预览视频：{cached.name}")
        return cached
    log(f"下载低清预览视频（用于抽帧）：{media_name}")
    try:
        return await video_processor.download_preview_video(
            url,
            run_key,
            cookies,
            should_abort=cancel.is_set,
            media_name=media_name,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise PipelineError(
            f"下载预览视频失败：{redact_secrets(str(exc))}"
        ) from exc


def _clean_run_cache(
    resolved: ResolvedInput, options: RunOptions, settings: UserSettings
) -> None:
    if not options.clean_media:
        return
    if resolved.kind not in {"url", "local_media"}:
        return
    run_dir = _run_directory(resolved, options, settings)
    if run_dir.is_dir():
        _remove_cached_media(run_dir)


def _find_cached_media(directory: Path, media_name: str) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [
        path
        for path in directory.glob(f"{media_name}.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_size)


def _remove_cached_media(directory: Path) -> None:
    removed = 0
    for pattern in ("audio.*", "audio_p*.*", "preview.*", "preview_p*.*"):
        for path in directory.glob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    for frames_dir in directory.glob("frames_*"):
        if frames_dir.is_dir():
            shutil.rmtree(frames_dir)
            removed += 1
    if removed:
        log(f"已清理 {removed} 个中间媒体/截图缓存（转录仍保留在缓存）")


def _info_from_segments(
    title: str, segments: Sequence[TranscriptSegment]
) -> dict[str, Any]:
    return {
        "title": title,
        "source": "imported",
        "duration": segments[-1].end if segments else 0.0,
        "owner": "",
        "upload_date": "",
        "view_count": 0,
        "like_count": 0,
        "description": "",
    }


def _strip_transcript_suffix(stem: str) -> str:
    return stem[: -len(".transcript")] if stem.endswith(".transcript") else stem


def _format_page_numbers(pages: Sequence[BiliPage]) -> str:
    numbers = sorted({page.page for page in pages})
    if not numbers:
        return "无"
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def _is_local_endpoint(base_url: str) -> bool:
    value = (base_url or "").strip()
    if not value:
        return False
    if "://" not in value:
        value = f"http://{value}"
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _friendly_source_error(exc: Exception, source: VideoSource) -> str:
    message = redact_secrets(str(exc))
    lowered = message.lower()
    if source == VideoSource.BILIBILI and any(
        marker in lowered
        for marker in ("412", "403", "forbidden", "precondition", "risk control")
    ):
        return (
            "B 站拒绝了本次请求（风控或凭据失效）。"
            "可以配置 [bilibili] sessdata，或用 --cookie-file 传入浏览器导出的 "
            f"cookies.txt 后重试。原始错误：{message}"
        )
    if source == VideoSource.DOUYIN:
        return (
            "抖音解析失败；公开分享链接可重试，"
            f"需要浏览器登录验证的链接目前不支持。原始错误：{message}"
        )
    return f"读取视频信息失败：{message}"


def _friendly_download_error(exc: Exception, url: str) -> str:
    message = redact_secrets(str(exc))
    lowered = message.lower()
    if "bilibili" in url or "b23.tv" in url:
        if any(
            marker in lowered
            for marker in ("412", "403", "forbidden", "precondition")
        ):
            return (
                "B 站下载被风控或凭据拦截。配置 [bilibili] sessdata "
                f"或用 --cookie-file 后重试。原始错误：{message}"
            )
    return f"下载音频失败：{message}"


def _validate_options(options: RunOptions, settings: UserSettings) -> None:
    if options.style not in {"detailed", "faithful", "concise"}:
        raise PipelineError(f"不支持的笔记风格：{options.style}")
    if options.effort not in {"auto", "off", "high", "max"}:
        raise PipelineError(f"不支持的推理强度：{options.effort}")
    if not options.transcript_only and options.effort == "max":
        # The summarizer maps max to high; keep the same behavior.
        options.effort = "high"
    if not options.input_value and options.from_transcript is None:
        raise PipelineError("请提供视频链接、本地文件或 --from-transcript")
    if options.from_transcript is not None and not options.from_transcript.is_file():
        raise PipelineError(f"转录文件不存在：{options.from_transcript}")
    if settings.asr.device not in {"cpu", "cuda"}:
        raise PipelineError(
            f"不支持的转写设备：{settings.asr.device}；可用 cpu / cuda"
        )
    if options.screenshots and not 5 <= options.screenshot_interval <= 300:
        raise PipelineError(
            f"画面切分的最小间隔必须在 5-300 秒之间，当前为 {options.screenshot_interval}"
        )
    known_models = set(WHISPER_MODEL_REPOS) | {"paraformer-zh"}
    if settings.asr.model not in known_models:
        log(
            f"警告：未知转写模型 {settings.asr.model}，"
            "将按 faster-whisper 模型名尝试"
        )
