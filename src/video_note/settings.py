from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

APP_NAME = "video-note"
DEFAULT_OUTPUT_DIR = "~/notes/video"

# Common provider-specific environment variables. They are used only when
# api_key is empty in config.toml; VIDEO_NOTE_API_KEY always wins.
PROVIDER_API_KEY_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openai_gpt4": ("OPENAI_API_KEY",),
    "openai_gpt35": ("OPENAI_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
    "glm": ("ZHIPUAI_API_KEY", "GLM_API_KEY"),
    "moonshot": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
}


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


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
class UserSettings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    asr: ASRSettings = field(default_factory=ASRSettings)
    bilibili: BilibiliSettings = field(default_factory=BilibiliSettings)
    output: OutputSettings = field(default_factory=OutputSettings)


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

    if provider := _env("VIDEO_NOTE_PROVIDER"):
        llm = replace(llm, provider=provider)
    if base_url := _env("VIDEO_NOTE_BASE_URL"):
        llm = replace(llm, base_url=base_url)
    if model := _env("VIDEO_NOTE_MODEL"):
        llm = replace(llm, model=model)
    if api_key := _env("VIDEO_NOTE_API_KEY"):
        llm = replace(llm, api_key=api_key)
    elif not llm.api_key:
        provider_key = _provider_api_key_from_env(llm.provider)
        if provider_key:
            llm = replace(llm, api_key=provider_key)

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

    return UserSettings(llm=llm, asr=asr, bilibili=bilibili, output=output)


def apply_cli_overrides(
    settings: UserSettings,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    asr_model: str | None = None,
    device: str | None = None,
    output_dir: str | None = None,
) -> UserSettings:
    """Apply explicit command-line values on top of config and environment."""
    llm = replace(
        settings.llm,
        provider=provider or settings.llm.provider,
        base_url=base_url if base_url is not None else settings.llm.base_url,
        model=model if model is not None else settings.llm.model,
        api_key=api_key if api_key is not None else settings.llm.api_key,
    )
    asr = replace(
        settings.asr,
        model=asr_model or settings.asr.model,
        device=device or settings.asr.device,
    )
    output = replace(
        settings.output, directory=output_dir or settings.output.directory
    )
    return replace(settings, llm=llm, asr=asr, output=output)


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


def _provider_api_key_from_env(provider: str) -> str:
    for name in PROVIDER_API_KEY_ENV.get(provider.strip().lower(), ()):
        value = _env(name)
        if value:
            return value
    return ""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()
