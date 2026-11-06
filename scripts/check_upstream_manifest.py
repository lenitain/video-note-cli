#!/usr/bin/env python3
"""守门：派生模块、上游快照与 `UPSTREAM.md` 必须一起演进。

三条规则，全部机械可查：

1. 暂存了派生模块（状态非 ``local-only``）或 ``upstream/`` 快照的改动，却没有
   暂存 ``UPSTREAM.md``：就地改写不留编号，下次同步就分不清哪些行是谁的。
2. 快照的 sha256 与表里记录的基线不一致：改写 base 会让三方合并悄悄变成错的。
3. 标成 ``verbatim`` 的本仓文件与快照不再逐字节相同：这个状态的含义正是「同步
   时可以直接覆盖」，不成立就要改状态，不能默默留着。

顺带核对清单完整性：``src/video_note/`` 下的每个模块都要在表里，派生模块也要
有对应的快照文件。

用法::

    python3 scripts/check_upstream_manifest.py            # 校验整份工作区
    python3 scripts/check_upstream_manifest.py --staged   # 只校验暂存区（pre-commit）
"""
from __future__ import annotations

import subprocess
import sys

from upstream_manifest import (
    MANIFEST,
    ROOT,
    SNAPSHOT_DIR,
    SOURCE_DIR,
    Entry,
    ManifestError,
    load_entries,
    sha256_file,
)

MANIFEST_NAME = MANIFEST.relative_to(ROOT).as_posix()


def staged_paths() -> list[str]:
    """暂存区里的新增和修改（不含删除）。"""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def check_inventory(entries: list[str]) -> list[str]:
    """清单完整性：源目录里的模块与映射表互相覆盖。"""
    problems: list[str] = []
    listed = set(entries)
    for path in sorted(SOURCE_DIR.glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative not in listed:
            problems.append(f"{relative} 没在 {MANIFEST_NAME} 的映射表里登记状态")
    for relative in sorted(listed):
        if not (ROOT / relative).is_file():
            problems.append(f"{MANIFEST_NAME} 登记的 {relative} 不存在")
    return problems


def check_snapshots(entries: list[Entry]) -> list[str]:
    """基线快照：记录的哈希必须与文件一致，且没有多余的快照。"""
    problems: list[str] = []
    referenced: set[str] = set()
    for entry in entries:
        if not entry.derived:
            continue
        assert entry.upstream is not None and entry.sha256 is not None
        referenced.add(entry.upstream)
        snapshot = SNAPSHOT_DIR / entry.upstream
        if not snapshot.is_file():
            problems.append(f"缺快照 {snapshot.relative_to(ROOT).as_posix()}（同步的合并基底）")
            continue
        actual = sha256_file(snapshot)
        if actual != entry.sha256:
            problems.append(
                f"{snapshot.relative_to(ROOT).as_posix()} 的 sha256 是 {actual}，"
                f"表里记录的是 {entry.sha256}：快照只读，改写它等于改写历史"
            )
    for snapshot in sorted(SNAPSHOT_DIR.rglob("*.py")):
        relative = snapshot.relative_to(SNAPSHOT_DIR).as_posix()
        if relative not in referenced:
            problems.append(f"快照 {relative} 没有任何映射行引用：删掉或补登记")
    return problems


def check_verbatim(entries: list[Entry]) -> list[str]:
    """verbatim 断言：本仓文件与快照逐字节相同。"""
    problems: list[str] = []
    for entry in entries:
        if entry.status != "verbatim":
            continue
        assert entry.upstream is not None
        local = ROOT / entry.local
        if not local.is_file():
            problems.append(f"{entry.local} 不存在")
            continue
        if sha256_file(local) != sha256_file(SNAPSHOT_DIR / entry.upstream):
            problems.append(
                f"{entry.local} 标着 verbatim 却与快照不同："
                "要么改回逐字一致，要么把状态改成 adapted 并补分歧条目"
            )
    return problems


def check_staged(entries: list[Entry], staged: list[str]) -> list[str]:
    """同一提交纪律：派生模块与快照的改动必须带上映射表。"""
    if MANIFEST_NAME in staged:
        return []
    derived = {entry.local for entry in entries if entry.derived}
    touched = [path for path in staged if path in derived]
    touched += [path for path in staged if path.startswith("upstream/")]
    touched += [
        path
        for path in staged
        if path.startswith("src/video_note/")
        and path.endswith(".py")
        and path not in {entry.local for entry in entries}
    ]
    if not touched:
        return []
    listed = "\n".join(f"  {path}" for path in touched)
    message = (
        "派生代码改了，映射表没动：\n"
        f"{listed}\n"
        f"在 {MANIFEST_NAME} 登记新的分歧条目（或更新已有条目）并一起暂存。"
    )
    return [message]


def main(argv: list[str]) -> int:
    try:
        entries = load_entries()
    except (ManifestError, OSError) as error:
        print(f"守门失败：{error}", file=sys.stderr)
        return 1

    problems = check_inventory([entry.local for entry in entries])
    problems += check_snapshots(entries)
    problems += check_verbatim(entries)
    if "--staged" in argv:
        problems += check_staged(entries, staged_paths())

    if problems:
        print("守门失败：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"守门通过：{len(entries)} 个模块与 {MANIFEST_NAME} 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
