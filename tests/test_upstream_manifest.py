"""同步契约：映射表、基线快照与 verbatim 状态三者必须自洽。

与 `scripts/check_upstream_manifest.py` 同源，多跑一遍是为了让 pytest 也能在不装
钩子的环境里发现漂移。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from upstream_manifest import SNAPSHOT_DIR, load_entries, sha256_file


def test_manifest_lists_every_derived_module() -> None:
    entries = load_entries()

    assert {entry.local for entry in entries} >= {
        "src/video_note/transcript.py",
        "src/video_note/download.py",
        "src/video_note/whisper_asr.py",
        "src/video_note/paraformer_asr.py",
        "src/video_note/video_processor.py",
        "src/video_note/llm_summarizer.py",
    }


@pytest.mark.parametrize("entry", load_entries(), ids=lambda entry: entry.local)
def test_snapshot_matches_recorded_baseline(entry) -> None:
    if not entry.derived:
        return
    snapshot = SNAPSHOT_DIR / str(entry.upstream)

    assert snapshot.is_file(), "基线快照缺失，同步就没有可以三方合并的基底"
    assert sha256_file(snapshot) == entry.sha256


@pytest.mark.parametrize(
    "entry",
    [entry for entry in load_entries() if entry.status == "verbatim"],
    ids=lambda entry: entry.local,
)
def test_verbatim_modules_are_byte_identical(entry) -> None:
    local: Path = Path(entry.local)

    assert local.read_bytes() == (SNAPSHOT_DIR / str(entry.upstream)).read_bytes()
