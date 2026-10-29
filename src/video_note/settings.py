from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .llm_summarizer import normalize_provider

APP_NAME = "video-note"
DEFAULT_OUTPUT_DIR = "~/notes/video"

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


@dataclass(frozen=True)
class OutputSettings:
    directory: str = DEFAULT_OUTPUT_DIR


@dataclass(frozen=True)
class ScreenshotSettings:
    enabled: bool = True
    interval: int = 30


@dataclass(frozen=True)
class UserSettings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    asr: ASRSettings = field(default_factory=ASRSettings)
    bilibili: BilibiliSettings = field(default_factory=BilibiliSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    screenshots: ScreenshotSettings = field(default_factory=ScreenshotSettings)


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

    if screenshots_enabled := _env("VIDEO_NOTE_SCREENSHOTS"):
        screenshots = replace(
            screenshots, enabled=_parse_bool(screenshots_enabled)
        )
    if screenshot_interval := _env("VIDEO_NOTE_SCREENSHOT_INTERVAL"):
        screenshots = replace(
            screenshots,
            interval=_parse_int(screenshot_interval, screenshots.interval),
        )

    return UserSettings(
        llm=llm,
        asr=asr,
        bilibili=bilibili,
        output=output,
        screenshots=screenshots,
    )


def apply_cli_overrides(
    settings: UserSettings,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    token_plan_key: str | None = None,
    token_plan_key_env: str | None = None,
    asr_model: str | None = None,
    device: str | None = None,
    output_dir: str | None = None,
    screenshots: bool | None = None,
    screenshot_interval: int | None = None,
) -> UserSettings:
    """Apply explicit command-line values on top of config and environment."""
    source = settings.llm.api_key_source
    if api_key is not None:
        source = "命令行 --api-key"
    elif token_plan_key is not None:
        source = "命令行 --token-plan-key"
    llm = replace(
        settings.llm,
        provider=provider or settings.llm.provider,
        base_url=base_url if base_url is not None else settings.llm.base_url,
        model=model if model is not None else settings.llm.model,
        api_key=api_key if api_key is not None else settings.llm.api_key,
        api_key_env=(
            api_key_env if api_key_env is not None else settings.llm.api_key_env
        ),
        token_plan_key=(
            token_plan_key
            if token_plan_key is not None
            else settings.llm.token_plan_key
        ),
        token_plan_key_env=(
            token_plan_key_env
            if token_plan_key_env is not None
            else settings.llm.token_plan_key_env
        ),
        api_key_source=source,
    )
    asr = replace(
        settings.asr,
        model=asr_model or settings.asr.model,
        device=device or settings.asr.device,
    )
    output = replace(
        settings.output, directory=output_dir or settings.output.directory
    )
    screenshot_settings = replace(
        settings.screenshots,
        enabled=(
            settings.screenshots.enabled
            if screenshots is None
            else screenshots
        ),
        interval=(
            settings.screenshots.interval
            if screenshot_interval is None
            else screenshot_interval
        ),
    )
    return replace(
        settings,
        llm=llm,
        asr=asr,
        output=output,
        screenshots=screenshot_settings,
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
    )


def _output_settings(section: dict[str, Any]) -> OutputSettings:
    return OutputSettings(
        directory=_text(section, "dir", DEFAULT_OUTPUT_DIR) or DEFAULT_OUTPUT_DIR
    )


def _screenshot_settings(section: dict[str, Any]) -> ScreenshotSettings:
    enabled_value = section.get("enabled", True)
    enabled = (
        enabled_value
        if isinstance(enabled_value, bool)
        else _parse_bool(str(enabled_value))
    )
    return ScreenshotSettings(
        enabled=enabled,
        interval=_parse_int(section.get("interval", 30), 30),
    )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


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
    return replace(settings, llm=_resolve_api_key(settings.llm))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()
