#!/usr/bin/env python3
"""把上游新版本三方合并进派生模块，结果落在 `build/sync/` 供人工复核。

合并的三个输入：

- base = `upstream/<上游文件>`（当前固定的快照）
- theirs = `git -C <clone> show <ref>:<上游文件>`
- local = 本仓文件

默认不碰工作区。上游没动过的模块自动跳过，所以一次同步真正要看的就是剩下的几个
文件。`--accept` 才把新版本写成下一份快照，并更新 `UPSTREAM.md` 的基线与哈希；
分歧日志仍由人复核，脚本不猜哪些条目该退休。

用法::

    uv run python scripts/sync-upstream.py --ref v1.4.8
    uv run python scripts/sync-upstream.py --ref v1.4.8 --accept
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from upstream_manifest import (
    MANIFEST,
    ROOT,
    SNAPSHOT_DIR,
    Entry,
    load_entries,
    sha256_file,
)

DEFAULT_CLONE = Path.home() / ".cloned" / "video-to-note"
DEFAULT_OUT = ROOT / "build" / "sync"


@dataclass(frozen=True)
class MergeResult:
    """一个派生模块的合并结果，供汇总表打印。"""

    entry: Entry
    state: str
    conflicts: int
    output: Path | None


def git(clone: Path, *args: str) -> bytes:
    """在上游 clone 里跑一条 git 命令，失败时把 stderr 带出来。"""
    result = subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, check=False
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} 失败：{message}")
    return result.stdout


def resolve_commit(clone: Path, ref: str) -> str:
    """把 tag / 分支解析成提交哈希，顺手确认它存在。"""
    return git(clone, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def show_file(clone: Path, ref: str, path: str) -> bytes:
    """取上游在 ref 下的文件内容。"""
    try:
        return git(clone, "show", f"{ref}:{path}")
    except RuntimeError as error:
        raise RuntimeError(f"上游 {ref} 里没有 {path}（文件被改名或删除？）") from error


def three_way_merge(
    local: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str]
) -> tuple[bytes, int]:
    """用 git merge-file 合并，返回（合并结果, 冲突处数）。

    冲突标记里写本仓路径与上游版本，而不是临时文件名：合并结果要给人读。
    """
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        paths = []
        for name, content in (("local", local), ("base", base), ("theirs", theirs)):
            path = scratch / name
            path.write_bytes(content)
            paths.append(path)
        result = subprocess.run(
            [
                "git",
                "merge-file",
                "-p",
                "--diff3",
                *chain.from_iterable(("-L", label) for label in labels),
                *(str(path) for path in paths),
            ],
            capture_output=True,
            check=False,
        )
    if result.returncode < 0:
        raise RuntimeError("git merge-file 无法合并（二进制或参数问题）")
    return result.stdout, result.returncode


def plan(entries: list[Entry], clone: Path, ref: str, out: Path) -> list[MergeResult]:
    """逐个派生模块比对并合并，把需要复核的结果写进 out。"""
    results: list[MergeResult] = []
    for entry in entries:
        if not entry.derived:
            continue
        assert entry.upstream is not None
        base = (SNAPSHOT_DIR / entry.upstream).read_bytes()
        theirs = show_file(clone, ref, entry.upstream)
        if theirs == base:
            results.append(MergeResult(entry, "上游未变", 0, None))
            continue
        local = (ROOT / entry.local).read_bytes()
        merged, conflicts = three_way_merge(
            local,
            base,
            theirs,
            (entry.local, f"upstream/{entry.upstream} (base)", f"upstream@{ref}"),
        )
        target = out / Path(entry.local).name
        target.write_bytes(merged)
        state = "自动合并" if conflicts == 0 else f"冲突 {conflicts} 处"
        results.append(MergeResult(entry, state, conflicts, target))
    return results


def accept(entries: list[Entry], clone: Path, ref: str, commit: str) -> None:
    """把上游新版本写成下一份快照，并更新映射表里的基线与哈希。"""
    for entry in entries:
        if not entry.derived:
            continue
        assert entry.upstream is not None
        (SNAPSHOT_DIR / entry.upstream).write_bytes(show_file(clone, ref, entry.upstream))

    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    digests = {
        entry.upstream: sha256_file(SNAPSHOT_DIR / str(entry.upstream))
        for entry in entries
        if entry.derived
    }
    rewritten: list[str] = []
    for line in lines:
        if line.startswith("| 固定版本 |"):
            line = f"| 固定版本 | `{ref}` |"
        elif line.startswith("| 提交 |"):
            line = f"| 提交 | `{commit}` |"
        elif line.startswith("| `src/"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            upstream = cells[1].strip("`")
            if upstream in digests:
                cells[4] = f"`{digests[upstream]}`"
                line = "| " + " | ".join(cells) + " |"
        rewritten.append(line)
    MANIFEST.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def report(results: list[MergeResult], out: Path) -> int:
    """打印汇总表，返回需要复核的模块个数。"""
    width = max(len(result.entry.local) for result in results)
    pending = 0
    for result in results:
        if result.conflicts:
            pending += 1
        print(f"{result.entry.local:<{width}}  {result.state}")
        if result.output is not None:
            try:
                shown = result.output.relative_to(ROOT).as_posix()
            except ValueError:
                shown = result.output.as_posix()
            print(f"{'':<{width}}  → {shown}")
    if pending:
        sys.stdout.flush()
        print(
            f"\n{pending} 个模块有冲突：按 <<<<<<< / ======= / >>>>>>> 标记保留上游行为与"
            f"本仓分歧，然后写回 src/。合并结果在 {out.relative_to(ROOT).as_posix()}。",
            file=sys.stderr,
        )
    elif any(result.output for result in results):
        print("\n合并结果无冲突，逐份复核后写回 src/。")
    else:
        print("\n上游没有改动这些模块。")
    return pending

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="三方合并上游改动")
    parser.add_argument("--ref", required=True, help="上游 tag / 分支 / 提交")
    parser.add_argument(
        "--clone",
        type=Path,
        default=Path(os.environ.get("VIDEO_TO_NOTE_CLONE", DEFAULT_CLONE)),
        help="上游 clone 路径（默认 %(default)s，可用 VIDEO_TO_NOTE_CLONE 覆盖）",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="合并结果目录（默认 build/sync）"
    )
    parser.add_argument(
        "--accept",
        action="store_true",
        help="把 --ref 写进 upstream/ 快照并更新 UPSTREAM.md 的基线与哈希",
    )
    args = parser.parse_args(argv)

    if not (args.clone / ".git").exists():
        print(f"上游 clone 不存在：{args.clone}（用 --clone 或 VIDEO_TO_NOTE_CLONE 指定）", file=sys.stderr)
        return 1
    args.out = args.out.resolve()
    try:
        commit = resolve_commit(args.clone, args.ref)
        entries = load_entries()
        if args.out.exists():
            shutil.rmtree(args.out)
        args.out.mkdir(parents=True)
        results = plan(entries, args.clone, args.ref, args.out)
        pending = report(results, args.out)
        if args.accept and pending:
            print(
                "\n还有未解决的冲突，不能确认新基线：先把合并结果写回 src/ 再跑一次。",
                file=sys.stderr,
            )
            return 1
        if args.accept:
            accept(entries, args.clone, args.ref, commit)
            print(
                f"\n基线已更新：{args.ref} @ {commit}。"
                "下一步复核分歧日志，删掉新版本已经不需要的条目，然后提交。",
                file=sys.stderr,
            )
    except (RuntimeError, OSError) as error:
        print(f"同步失败：{error}", file=sys.stderr)
        return 1
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
