"""解析 `UPSTREAM.md` 里的模块映射表。

守门脚本与同步脚本读同一张表：表是唯一清单，两边都不另存一份路径列表。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "UPSTREAM.md"
SNAPSHOT_DIR = ROOT / "upstream"
SOURCE_DIR = ROOT / "src" / "video_note"
STATUSES = ("verbatim", "adapted", "local-only")

_BACKTICKED = re.compile(r"^`([^`]+)`$")
_DIVERGENCE = re.compile(r"^D\d+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(RuntimeError):
    """映射表无法解析：列数、状态或哈希不合约定。"""


@dataclass(frozen=True)
class Entry:
    """映射表的一行：本仓文件、上游出处、同步状态、分歧编号、基线哈希。"""

    local: str
    upstream: str | None
    status: str
    divergence: str | None
    sha256: str | None

    @property
    def derived(self) -> bool:
        return self.status != "local-only"


def sha256_file(path: Path) -> str:
    """文件的 sha256，用于比对快照与基线记录。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backticked(cell: str, number: int, column: str) -> str:
    match = _BACKTICKED.match(cell)
    if match is None:
        raise ManifestError(f"UPSTREAM.md:{number} 的{column}应当写在反引号里：{cell!r}")
    return match.group(1)


def load_entries(path: Path = MANIFEST) -> list[Entry]:
    """读出映射表的全部行；表里出现不合约定的行会直接失败。"""
    entries: list[Entry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not _BACKTICKED.match(cells[0]):
            continue
        if len(cells) != 5:
            raise ManifestError(f"UPSTREAM.md:{number} 应当是 5 列，实际 {len(cells)} 列")

        local = _backticked(cells[0], number, "本仓文件")
        status = cells[2]
        if status not in STATUSES:
            raise ManifestError(
                f"UPSTREAM.md:{number} 的状态 {status!r} 不在 {'/'.join(STATUSES)} 里"
            )

        if status == "local-only":
            if cells[1] != "—" or cells[3] != "—" or cells[4] != "—":
                raise ManifestError(
                    f"UPSTREAM.md:{number} 是 local-only，上游文件、分歧、哈希都该写 —"
                )
            entries.append(Entry(local, None, status, None, None))
            continue

        upstream = _backticked(cells[1], number, "上游文件")
        divergence = cells[3]
        if status == "adapted" and not _DIVERGENCE.match(divergence):
            raise ManifestError(
                f"UPSTREAM.md:{number} 的 adapted 行缺分歧编号（D1、D2 …）：{divergence!r}"
            )
        if status == "verbatim" and divergence != "—":
            raise ManifestError(
                f"UPSTREAM.md:{number} 是 verbatim，分歧列该写 —（逐字一致就没有分歧）"
            )
        digest = _backticked(cells[4], number, "基线哈希")
        if not _SHA256.match(digest):
            raise ManifestError(f"UPSTREAM.md:{number} 的基线哈希不是 sha256：{digest!r}")
        entries.append(
            Entry(
                local,
                upstream,
                status,
                divergence if status == "adapted" else None,
                digest,
            )
        )

    if not entries:
        raise ManifestError("UPSTREAM.md 里没有读到任何映射行")

    duplicates = [
        local for local, count in Counter(entry.local for entry in entries).items() if count > 1
    ]
    if duplicates:
        raise ManifestError(f"映射表里重复登记：{', '.join(sorted(duplicates))}")
    return entries
