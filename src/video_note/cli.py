from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path

from . import __version__
from .pipeline import (
    Cancellation,
    CancelledRun,
    PipelineError,
    RunOptions,
    log,
    run_video_note_pipeline,
)
from .settings import (
    apply_cli_overrides,
    load_user_settings,
    output_directory,
    user_config_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-note",
        description=(
            "把视频链接、本地音视频或字幕文件变成带时间轴的 Markdown 笔记。"
            "默认先找平台字幕，找不到再用本地模型转写，最后调用大模型写笔记。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例子：\n"
            "  video-note 'https://www.bilibili.com/video/BV1xx'\n"
            "  video-note ./lecture.mp4 --transcript-only\n"
            "  video-note ./lecture.srt --style concise\n"
            "  video-note --from-transcript ./lecture.transcript.json --style detailed\n"
            "\n"
            f"配置文件：{user_config_path()}\n"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="视频链接、本地音视频文件，或 .srt/.vtt/.json 字幕文件",
    )
    parser.add_argument(
        "-o",
        "--out",
        metavar="DIR",
        help="输出目录（默认读取配置，初始为 ~/notes/video）",
    )
    parser.add_argument(
        "--style",
        choices=("detailed", "faithful", "concise"),
        default="detailed",
        help="笔记风格：detailed 详细+点评 / faithful 忠实复原 / concise 精简（默认 detailed）",
    )
    parser.add_argument(
        "--effort",
        choices=("auto", "off", "high", "max"),
        default="auto",
        help="模型推理强度（默认 auto；max 按 high 处理）",
    )
    parser.add_argument(
        "-t",
        "--transcript-only",
        action="store_true",
        help="只生成带时间轴的转录稿，不调用大模型、不需要 API Key",
    )
    parser.add_argument(
        "--save-transcript",
        action="store_true",
        help="生成笔记时，同时把转录稿写到输出目录",
    )
    parser.add_argument(
        "--from-transcript",
        metavar="FILE",
        help="跳过下载和转写，直接从 .json/.srt/.vtt/.md 转录稿生成笔记",
    )
    parser.add_argument(
        "--no-subtitles",
        action="store_true",
        help="不读取平台字幕，强制使用本地语音转写",
    )
    parser.add_argument(
        "--pages",
        type=parse_pages,
        metavar="N[,N-M]",
        help="B 站分 P 选择，例如 1,3-5（默认跟随链接 ?p=，否则全部）",
    )
    parser.add_argument(
        "--asr",
        metavar="MODEL",
        help="转写模型：tiny/base/small/medium/large-v3/turbo/belle-turbo-zh/paraformer-zh",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "cuda"),
        help="转写设备（默认读取配置，初始为 cpu）",
    )
    parser.add_argument(
        "--provider",
        metavar="NAME",
        help=(
            "模型供应商标识：deepseek/openai/qwen/glm/moonshot/minimax/"
            "minimax_cn/custom 等"
        ),
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        help="OpenAI 兼容接口地址（custom 必填）",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help="模型名（留空使用 provider 默认值）",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="API Key；建议写在配置的 [llm] api_key 里",
    )
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        help=(
            "从哪个环境变量读 API Key（如 DEEPSEEK_API_KEY）；"
            "写 provider 则用该 provider 的惯用变量名"
        ),
    )
    parser.add_argument(
        "--token-plan-key",
        metavar="KEY",
        help=(
            "Token Plan 订阅 Key；与按量计费的 API Key 是同一 provider 的两种计费方式，"
            "端点相同、Key 不互通"
        ),
    )
    parser.add_argument(
        "--token-plan-key-env",
        metavar="VAR",
        help="从哪个环境变量读 Token Plan 订阅 Key",
    )
    parser.add_argument(
        "--cookie-file",
        metavar="FILE",
        help="浏览器导出的 Netscape cookies.txt，用于 B 站 AI 字幕",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help=f"配置文件路径（默认 {user_config_path()}）",
    )
    parser.add_argument(
        "--reuse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="复用同一输入的已有转录（默认开启；用 --no-reuse 强制重跑）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="完成后删除缓存里的音频和截图缓存，只保留转录",
    )
    parser.add_argument(
        "--screenshots",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="抽帧并把画面插进笔记正文（默认读取配置，初始为开启）",
    )
    parser.add_argument(
        "--screenshot-interval",
        type=int,
        metavar="SECONDS",
        help="抽帧间隔，5-300 秒；指定这一项会启用抽帧（默认读取配置）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="失败时打印完整堆栈",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def parse_pages(value: str) -> tuple[int, ...]:
    pages: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    f"分 P 范围写法不对：{chunk}"
                ) from exc
            if start < 1 or end < start:
                raise argparse.ArgumentTypeError(
                    f"分 P 范围必须从 1 开始且递增：{chunk}"
                )
            pages.update(range(start, end + 1))
            continue
        try:
            page = int(chunk)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"分 P 必须是数字：{chunk}"
            ) from exc
        if page < 1:
            raise argparse.ArgumentTypeError(f"分 P 从 1 开始：{chunk}")
        pages.add(page)
    if not pages:
        raise argparse.ArgumentTypeError("--pages 不能为空")
    return tuple(sorted(pages))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.input and not args.from_transcript:
        parser.error("请提供视频链接/本地文件，或使用 --from-transcript")

    cancel = Cancellation()
    try:
        settings = load_user_settings(
            Path(args.config).expanduser() if args.config else None
        )
        screenshots = args.screenshots
        if screenshots is None and args.screenshot_interval is not None:
            screenshots = True
        settings = apply_cli_overrides(
            settings,
            provider=args.provider,
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
            token_plan_key=args.token_plan_key,
            token_plan_key_env=args.token_plan_key_env,
            asr_model=args.asr,
            device=args.device,
            output_dir=args.out,
            screenshots=screenshots,
            screenshot_interval=args.screenshot_interval,
        )
        options = RunOptions(
            input_value=args.input or "",
            output_dir=output_directory(settings),
            style=args.style,
            effort=args.effort,
            transcript_only=args.transcript_only,
            save_transcript=args.save_transcript,
            prefer_subtitles=not args.no_subtitles,
            pages=args.pages or (),
            from_transcript=(
                Path(args.from_transcript).expanduser()
                if args.from_transcript
                else None
            ),
            reuse=args.reuse,
            clean_media=args.clean,
            screenshots=settings.screenshots.enabled,
            screenshot_interval=settings.screenshots.interval,
            cookie_file=(
                Path(args.cookie_file).expanduser()
                if args.cookie_file
                else None
            ),
        )
        cancel.install()
        result = asyncio.run(
            run_video_note_pipeline(options, settings, cancel)
        )
    except CancelledRun:
        print("已取消。", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except PipelineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if args.verbose:
            traceback.print_exc()
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        cancel.restore()

    log(
        f"完成：{result.title}（{result.segments} 段，"
        f"{result.source}，{result.duration:.1f} 秒）"
    )
    if result.note_path is not None:
        print(result.note_path)
    for path in result.transcript_paths.values():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
