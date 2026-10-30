from __future__ import annotations

import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .llm_summarizer import normalize_provider
from .web_search import WEB_SEARCH_BACKENDS

APP_NAME = "video-note"
DEFAULT_OUTPUT_DIR = "~/notes/video"

# 笔记风格与推理强度的可选取值：命令行 --style/--effort 与配置 [note] 共用同一份。
NOTE_STYLES = ("detailed", "faithful", "concise")
NOTE_EFFORTS = ("auto", "off", "high", "max")

# 成品格式的名字沿用命令行开关（--md-noimage / --md-image / --html），
# 配置 [output] formats 与环境变量都用这套名字。
OUTPUT_FORMAT_MARKDOWN_TEXT = "md-noimage"
OUTPUT_FORMAT_MARKDOWN_IMAGES = "md-image"
OUTPUT_FORMAT_HTML = "html"
OUTPUT_FORMATS = (
    OUTPUT_FORMAT_MARKDOWN_TEXT,
    OUTPUT_FORMAT_MARKDOWN_IMAGES,
    OUTPUT_FORMAT_HTML,
)
DEFAULT_OUTPUT_FORMATS = (OUTPUT_FORMAT_MARKDOWN_TEXT, OUTPUT_FORMAT_HTML)

# 每个 provider 惯用的环境变量名。程序不会自己去找它们——要用就在配置里写
# api_key_env = "provider"（跟着 provider 换）或 api_key_env = "变量名"。
PROVIDER_API_KEY_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openai_gpt4": ("OPENAI_API_KEY",),
    "openai_gpt35": ("OPENAI_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
    "glm": ("ZHIPUAI_API_KEY", "GLM_API_KEY"),
    "moonshot": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "minimax": ("MINIMAX_API_KEY",),
    "minimax_cn": ("MINIMAX_CN_API_KEY",),
}

# Token Plan 订阅 Key：套餐维度，不是 provider 维度。按量计费 Key 与订阅 Key
# 计费方式不同、不互通，但同一个 provider 的端点一样，所以它们是同一维度的
# 两种凭据，各占一个槽位。
PROVIDER_TOKEN_PLAN_KEY_ENV = {
    "minimax": ("MINIMAX_TOKEN_PLAN_API_KEY",),
    "minimax_cn": ("MINIMAX_CN_TOKEN_PLAN_API_KEY",),
}


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    token_plan_key: str = ""
    token_plan_key_env: str = ""
    api_key_source: str = ""


@dataclass(frozen=True)
class ASRSettings:
    model: str = "base"
    device: str = "cpu"


@dataclass(frozen=True)
class BilibiliSettings:
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""
    buvid4: str = ""
    b_nut: str = ""
    b_lsid: str = ""
    cookie_file: str = ""


@dataclass(frozen=True)
class OutputSettings:
    directory: str = DEFAULT_OUTPUT_DIR
    formats: tuple[str, ...] = DEFAULT_OUTPUT_FORMATS


@dataclass(frozen=True)
class ScreenshotSettings:
    enabled: bool = True
    interval: int = 30


@dataclass(frozen=True)
class NoteSettings:
    """笔记本身的偏好：写多细、思考多少。"""

    style: str = "detailed"
    effort: str = "auto"


@dataclass(frozen=True)
class WebSearchSettings:
    """点评阶段的联网核验：用什么搜索后端、一次点评搜几次。"""

    enabled: bool = True
    backend: str = "auto"
    api_key: str = ""
    api_key_env: str = ""
    max_searches: int = 4
    timeout: int = 15


@dataclass(frozen=True)
class RunSettings:
    """一次运行怎么做：复用旧稿、存转录稿、用哪种字幕、要不要清缓存。"""

    reuse: bool = True
    save_transcript: bool = False
    prefer_subtitles: bool = True
    clean: bool = False


@dataclass(frozen=True)
class UserSettings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    asr: ASRSettings = field(default_factory=ASRSettings)
    bilibili: BilibiliSettings = field(default_factory=BilibiliSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    screenshots: ScreenshotSettings = field(default_factory=ScreenshotSettings)
    note: NoteSettings = field(default_factory=NoteSettings)
    web: WebSearchSettings = field(default_factory=WebSearchSettings)
    run: RunSettings = field(default_factory=RunSettings)


def user_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    return base / APP_NAME / "config.toml"


def load_user_settings(path: Path | None = None) -> UserSettings:
    """Read config.toml, then let environment variables override it."""
    data = _read_toml(path or user_config_path())
    llm = _llm_settings(_section(data, "llm"))
    asr = _asr_settings(_section(data, "asr"))
    bilibili = _bilibili_settings(_section(data, "bilibili"))
    output = _output_settings(_section(data, "output"))
    screenshots = _screenshot_settings(_section(data, "screenshots"))
    note = _note_settings(_section(data, "note"))
    web = _web_settings(_section(data, "web"))
    run = _run_settings(_section(data, "run"))

    if provider := _env("VIDEO_NOTE_PROVIDER"):
        llm = replace(llm, provider=provider)
    if base_url := _env("VIDEO_NOTE_BASE_URL"):
        llm = replace(llm, base_url=base_url)
    if model := _env("VIDEO_NOTE_MODEL"):
        llm = replace(llm, model=model)

    if model := _env("VIDEO_NOTE_ASR_MODEL"):
        asr = replace(asr, model=model)
    if device := _env("VIDEO_NOTE_DEVICE"):
        asr = replace(asr, device=device)

    bilibili = replace(
        bilibili,
        sessdata=_env("BILI_SESSDATA") or bilibili.sessdata,
        bili_jct=_env("BILI_JCT") or bilibili.bili_jct,
        buvid3=_env("BILI_BUVID3") or bilibili.buvid3,
        buvid4=_env("BILI_BUVID4") or bilibili.buvid4,
        b_nut=_env("BILI_B_NUT") or bilibili.b_nut,
        b_lsid=_env("BILI_B_LSID") or bilibili.b_lsid,
    )

    if output_dir := _env("VIDEO_NOTE_OUTPUT_DIR"):
        output = replace(output, directory=output_dir)
    if formats := _env("VIDEO_NOTE_OUTPUT_FORMATS"):
        output = replace(
            output,
            formats=_parse_output_formats(
                formats.split(","), "环境变量 VIDEO_NOTE_OUTPUT_FORMATS"
            ),
        )

    if screenshots_enabled := _env("VIDEO_NOTE_SCREENSHOTS"):
        screenshots = replace(
            screenshots,
            enabled=_parse_bool(
                screenshots_enabled, "环境变量 VIDEO_NOTE_SCREENSHOTS"
            ),
        )
    if screenshot_interval := _env("VIDEO_NOTE_SCREENSHOT_INTERVAL"):
        screenshots = replace(
            screenshots,
            interval=_parse_int(screenshot_interval, screenshots.interval),
        )

    if style := _env("VIDEO_NOTE_STYLE"):
        note = replace(
            note,
            style=_validate_choice(
                style, NOTE_STYLES, "环境变量 VIDEO_NOTE_STYLE"
            ),
        )
    if effort := _env("VIDEO_NOTE_EFFORT"):
        note = replace(
            note,
            effort=_validate_choice(
                effort, NOTE_EFFORTS, "环境变量 VIDEO_NOTE_EFFORT"
            ),
        )

    if web_enabled := _env("VIDEO_NOTE_WEB"):
        web = replace(
            web,
            enabled=_parse_bool(web_enabled, "环境变量 VIDEO_NOTE_WEB"),
        )
    if web_backend := _env("VIDEO_NOTE_WEB_BACKEND"):
        web = replace(
            web,
            backend=_validate_choice(
                web_backend, WEB_SEARCH_BACKENDS, "环境变量 VIDEO_NOTE_WEB_BACKEND"
            ),
        )
    if web_api_key := _env("VIDEO_NOTE_WEB_API_KEY"):
        web = replace(web, api_key=web_api_key)
    if web_max_searches := _env("VIDEO_NOTE_WEB_MAX_SEARCHES"):
        web = replace(
            web, max_searches=_parse_int(web_max_searches, web.max_searches)
        )

    if reuse := _env("VIDEO_NOTE_REUSE"):
        run = replace(run, reuse=_parse_bool(reuse, "环境变量 VIDEO_NOTE_REUSE"))
    if save_transcript := _env("VIDEO_NOTE_SAVE_TRANSCRIPT"):
        run = replace(
            run,
            save_transcript=_parse_bool(
                save_transcript, "环境变量 VIDEO_NOTE_SAVE_TRANSCRIPT"
            ),
        )
    if prefer_subtitles := _env("VIDEO_NOTE_PREFER_SUBTITLES"):
        run = replace(
            run,
            prefer_subtitles=_parse_bool(
                prefer_subtitles, "环境变量 VIDEO_NOTE_PREFER_SUBTITLES"
            ),
        )
    if clean := _env("VIDEO_NOTE_CLEAN"):
        run = replace(run, clean=_parse_bool(clean, "环境变量 VIDEO_NOTE_CLEAN"))

    if cookie_file := _env("VIDEO_NOTE_COOKIE_FILE"):
        bilibili = replace(bilibili, cookie_file=cookie_file)

    return UserSettings(
        llm=llm,
        asr=asr,
        bilibili=bilibili,
        output=output,
        screenshots=screenshots,
        note=note,
        web=web,
        run=run,
    )


@dataclass(frozen=True)
class CliOverrides:
    """命令行显式给出的值。

    每个字段都是 None / 空元组时表示「这次命令行没说」，由配置和环境变量决定；
    给了值就盖过配置。``output_formats`` 是空元组时同样表示命令行没点名成品，
    这时用配置 [output] formats 里的清单。
    """

    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    token_plan_key: str | None = None
    token_plan_key_env: str | None = None
    asr_model: str | None = None
    device: str | None = None
    output_dir: str | None = None
    output_formats: tuple[str, ...] = ()
    screenshots: bool | None = None
    screenshot_interval: int | None = None
    note_style: str | None = None
    note_effort: str | None = None
    web: bool | None = None
    web_backend: str | None = None
    reuse: bool | None = None
    save_transcript: bool | None = None
    prefer_subtitles: bool | None = None
    clean: bool | None = None
    cookie_file: str | None = None


def apply_cli_overrides(
    settings: UserSettings, overrides: CliOverrides
) -> UserSettings:
    """Apply explicit command-line values on top of config and environment."""
    source = settings.llm.api_key_source
    if overrides.api_key is not None:
        source = "命令行 --api-key"
    elif overrides.token_plan_key is not None:
        source = "命令行 --token-plan-key"
    llm = replace(
        settings.llm,
        provider=overrides.provider or settings.llm.provider,
        base_url=(
            overrides.base_url
            if overrides.base_url is not None
            else settings.llm.base_url
        ),
        model=(
            overrides.model if overrides.model is not None else settings.llm.model
        ),
        api_key=(
            overrides.api_key
            if overrides.api_key is not None
            else settings.llm.api_key
        ),
        api_key_env=(
            overrides.api_key_env
            if overrides.api_key_env is not None
            else settings.llm.api_key_env
        ),
        token_plan_key=(
            overrides.token_plan_key
            if overrides.token_plan_key is not None
            else settings.llm.token_plan_key
        ),
        token_plan_key_env=(
            overrides.token_plan_key_env
            if overrides.token_plan_key_env is not None
            else settings.llm.token_plan_key_env
        ),
        api_key_source=source,
    )
    asr = replace(
        settings.asr,
        model=overrides.asr_model or settings.asr.model,
        device=overrides.device or settings.asr.device,
    )
    output = replace(
        settings.output,
        directory=overrides.output_dir or settings.output.directory,
        # 命令行点名了成品就整份替换配置里的清单：一个都没点名才用配置的。
        formats=overrides.output_formats or settings.output.formats,
    )
    screenshot_settings = replace(
        settings.screenshots,
        enabled=(
            settings.screenshots.enabled
            if overrides.screenshots is None
            else overrides.screenshots
        ),
        interval=(
            settings.screenshots.interval
            if overrides.screenshot_interval is None
            else overrides.screenshot_interval
        ),
    )
    note = replace(
        settings.note,
        style=(
            overrides.note_style
            if overrides.note_style is not None
            else settings.note.style
        ),
        effort=(
            overrides.note_effort
            if overrides.note_effort is not None
            else settings.note.effort
        ),
    )
    web = replace(
        settings.web,
        enabled=(
            settings.web.enabled if overrides.web is None else overrides.web
        ),
        backend=(
            overrides.web_backend
            if overrides.web_backend is not None
            else settings.web.backend
        ),
    )
    run = replace(
        settings.run,
        reuse=(
            settings.run.reuse if overrides.reuse is None else overrides.reuse
        ),
        save_transcript=(
            settings.run.save_transcript
            if overrides.save_transcript is None
            else overrides.save_transcript
        ),
        prefer_subtitles=(
            settings.run.prefer_subtitles
            if overrides.prefer_subtitles is None
            else overrides.prefer_subtitles
        ),
        clean=(
            settings.run.clean if overrides.clean is None else overrides.clean
        ),
    )
    bilibili = replace(
        settings.bilibili,
        cookie_file=(
            overrides.cookie_file
            if overrides.cookie_file is not None
            else settings.bilibili.cookie_file
        ),
    )
    return replace(
        settings,
        llm=llm,
        asr=asr,
        bilibili=bilibili,
        output=output,
        screenshots=screenshot_settings,
        note=note,
        web=web,
        run=run,
    )


def output_directory(settings: UserSettings) -> Path:
    return Path(settings.output.directory).expanduser().resolve()


def cache_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser()
    return base / APP_NAME


def runs_root() -> Path:
    return cache_root() / "runs"


def model_cache_root() -> Path:
    configured = os.environ.get("WHISPER_CACHE_DIR")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else cache_root() / "models"
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as file:
            payload = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path}：{exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _text(section: dict[str, Any], key: str, default: str = "") -> str:
    value = section.get(key, default)
    return str(value).strip() if value is not None else default


def _llm_settings(section: dict[str, Any]) -> LLMSettings:
    return LLMSettings(
        provider=_text(section, "provider", "deepseek") or "deepseek",
        base_url=_text(section, "base_url"),
        model=_text(section, "model"),
        api_key=_text(section, "api_key"),
        api_key_env=_text(section, "api_key_env"),
        token_plan_key=_text(section, "token_plan_key"),
        token_plan_key_env=_text(section, "token_plan_key_env"),
    )


def _asr_settings(section: dict[str, Any]) -> ASRSettings:
    return ASRSettings(
        model=_text(section, "model", "base") or "base",
        device=_text(section, "device", "cpu") or "cpu",
    )


def _bilibili_settings(section: dict[str, Any]) -> BilibiliSettings:
    return BilibiliSettings(
        sessdata=_text(section, "sessdata"),
        bili_jct=_text(section, "bili_jct"),
        buvid3=_text(section, "buvid3"),
        buvid4=_text(section, "buvid4"),
        b_nut=_text(section, "b_nut"),
        b_lsid=_text(section, "b_lsid"),
        cookie_file=_text(section, "cookie_file"),
    )


def _output_settings(section: dict[str, Any]) -> OutputSettings:
    return OutputSettings(
        directory=_text(section, "dir", DEFAULT_OUTPUT_DIR) or DEFAULT_OUTPUT_DIR,
        formats=_output_formats(section),
    )


def _screenshot_settings(section: dict[str, Any]) -> ScreenshotSettings:
    return ScreenshotSettings(
        enabled=_config_bool(
            section, "enabled", True, "配置文件 [screenshots] enabled"
        ),
        interval=_parse_int(section.get("interval", 30), 30),
    )


def _note_settings(section: dict[str, Any]) -> NoteSettings:
    return NoteSettings(
        style=_validate_choice(
            _text(section, "style", "detailed") or "detailed",
            NOTE_STYLES,
            "配置文件 [note] style",
        ),
        effort=_validate_choice(
            _text(section, "effort", "auto") or "auto",
            NOTE_EFFORTS,
            "配置文件 [note] effort",
        ),
    )


def _web_settings(section: dict[str, Any]) -> WebSearchSettings:
    return WebSearchSettings(
        enabled=_config_bool(
            section, "enabled", True, "配置文件 [web] enabled"
        ),
        backend=_validate_choice(
            _text(section, "backend", "auto") or "auto",
            WEB_SEARCH_BACKENDS,
            "配置文件 [web] backend",
        ),
        api_key=_text(section, "api_key"),
        api_key_env=_text(section, "api_key_env"),
        max_searches=_parse_int(section.get("max_searches", 4), 4),
        timeout=_parse_int(section.get("timeout", 15), 15),
    )


def _run_settings(section: dict[str, Any]) -> RunSettings:
    return RunSettings(
        reuse=_config_bool(section, "reuse", True, "配置文件 [run] reuse"),
        save_transcript=_config_bool(
            section,
            "save_transcript",
            False,
            "配置文件 [run] save_transcript",
        ),
        prefer_subtitles=_config_bool(
            section,
            "prefer_subtitles",
            True,
            "配置文件 [run] prefer_subtitles",
        ),
        clean=_config_bool(section, "clean", False, "配置文件 [run] clean"),
    )


def _output_formats(section: dict[str, Any]) -> tuple[str, ...]:
    """配置 [output] formats：留空或没写就用默认的两种成品。"""
    value = section.get("formats")
    if value is None:
        return DEFAULT_OUTPUT_FORMATS
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ValueError(
            "配置文件 [output] formats 必须是字符串列表，"
            f"可选 {' / '.join(OUTPUT_FORMATS)}"
        )
    names = tuple(item.strip() for item in items if item.strip())
    if not names:
        return DEFAULT_OUTPUT_FORMATS
    return _parse_output_formats(names, "配置文件 [output] formats")


def _parse_output_formats(
    names: Sequence[str], source: str
) -> tuple[str, ...]:
    """成品格式名归一：去重、按固定顺序排列，认不出来的直接报错。"""
    unknown = [name for name in names if name not in OUTPUT_FORMATS]
    if unknown:
        raise ValueError(
            f"{source} 不支持 {', '.join(unknown)}；"
            f"可选 {' / '.join(OUTPUT_FORMATS)}"
        )
    return tuple(name for name in OUTPUT_FORMATS if name in names)


def _validate_choice(value: str, allowed: Sequence[str], source: str) -> str:
    """取值必须落在白名单里，否则当场报错（说清楚是哪个配置项写错了）。"""
    if value not in allowed:
        raise ValueError(
            f"{source} 只支持 {' / '.join(allowed)}，当前为 {value!r}"
        )
    return value


def _config_bool(
    section: dict[str, Any], key: str, default: bool, source: str
) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    return _parse_bool(str(value), source)


def _parse_bool(value: str, source: str) -> bool:
    """把 true/false（也认 1/0、yes/no、on/off）解析成布尔值。"""
    text = value.strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    raise ValueError(
        f"{source} 只接受 true/false（也认 1/0、yes/no、on/off），"
        f"当前为 {value!r}"
    )


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_api_key(llm: LLMSettings) -> LLMSettings:
    """Key 的来源只有两种：直接写的值，或显式指定的环境变量名。

    置空就是置空：``api_key = ""`` 不会再去别处找 Key。要用环境变量就写
    ``api_key_env = "变量名"``（写 ``"provider"`` 则用该 provider 的惯用变量名，
    换 provider 时跟着换）。两个槽位都没给 Key 时就是没有 Key，由调用方报错。
    """
    api_key, api_source = _first_key(
        (llm.api_key, "配置 api_key"),
        _key_from_env(llm.api_key_env, PROVIDER_API_KEY_ENV, llm.provider),
    )
    plan_key, plan_source = _first_key(
        (llm.token_plan_key, "配置 token_plan_key"),
        _key_from_env(
            llm.token_plan_key_env, PROVIDER_TOKEN_PLAN_KEY_ENV, llm.provider
        ),
    )
    return replace(
        llm,
        api_key=api_key or plan_key,
        token_plan_key=plan_key,
        api_key_source=llm.api_key_source or api_source or plan_source,
    )


def _key_from_env(
    name: str, table: dict[str, tuple[str, ...]], provider: str
) -> tuple[str, str]:
    """按设置里写的名字取 Key，返回 (Key, 来源说明)。

    写具体变量名就照着取；写 ``provider`` 则用该 provider 的惯用变量名。
    """
    if not name:
        return "", ""
    if name.strip().lower() == "provider":
        for variable in table.get(normalize_provider(provider), ()):
            value = _env(variable)
            if value:
                return value, f"环境变量 {variable}"
        return "", ""
    value = _env(name)
    return (value, f"环境变量 {name}") if value else ("", "")


def _first_key(*candidates: tuple[str, str]) -> tuple[str, str]:
    """按顺序取第一个非空的 (Key, 来源说明)。"""
    for value, source in candidates:
        if value:
            return value, source
    return "", ""


def resolve_credentials(settings: UserSettings) -> UserSettings:
    """定下实际用哪把 Key。

    ``api_key_env = "provider"`` 依赖最终 provider，所以这一步必须晚于命令行覆盖，
    只做一次（pipeline 入口调用）。
    """
    return replace(
        settings,
        llm=_resolve_api_key(settings.llm),
        web=_resolve_web_api_key(settings.web),
    )


def _resolve_web_api_key(web: WebSearchSettings) -> WebSearchSettings:
    """tavily 的 Key：直接写值，或 api_key_env 指认环境变量名。"""
    if web.api_key or not web.api_key_env:
        return web
    return replace(web, api_key=_env(web.api_key_env))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()
