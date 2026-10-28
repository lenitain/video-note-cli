from __future__ import annotations

from pathlib import Path

from .settings import BilibiliSettings

BILIBILI_COOKIE_FIELDS = (
    "sessdata",
    "bili_jct",
    "buvid3",
    "buvid4",
    "b_nut",
    "b_lsid",
)


def read_bilibili_cookie_file(path: Path) -> dict[str, str]:
    """Read the Bilibili fields from a Netscape cookies.txt export."""
    cookies: dict[str, str] = {}
    try:
        lines = path.expanduser().read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"无法读取 Cookie 文件 {path}：{exc}") from exc

    for raw_line in lines:
        line = raw_line
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _, _, _, _, name, value = parts
        if "bilibili.com" not in domain.lower():
            continue
        field = name.lower()
        if field in BILIBILI_COOKIE_FIELDS:
            cookies[field] = value
    if not cookies:
        raise ValueError(
            f"Cookie 文件 {path} 里没有 SESSDATA 等 B 站字段；"
            "请从浏览器导出 bilibili.com 的 Netscape 格式 cookies.txt"
        )
    return cookies


def resolve_bilibili_cookies(
    settings: BilibiliSettings,
    cookie_file: Path | None = None,
) -> dict[str, str] | None:
    """Merge config/env cookies with an explicit cookies.txt (file wins)."""
    cookies = {
        field: value
        for field in BILIBILI_COOKIE_FIELDS
        if (value := str(getattr(settings, field) or "").strip())
    }
    if cookie_file is not None:
        cookies.update(read_bilibili_cookie_file(cookie_file))
    return cookies or None
