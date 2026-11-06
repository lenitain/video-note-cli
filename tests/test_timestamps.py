"""时间戳读回：笔记里的 ``MM:SS`` 必须能被还原成秒（D 条目之外的基础契约）。"""
from __future__ import annotations

import pytest

from video_note.timestamps import parse_timestamp
from video_note.transcript import format_timestamp


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("00:00", 0.0),
        ("01:30", 90.0),
        ("12:34", 754.0),
        ("130:05", 7805.0),
        ("01:02:03", 3723.0),
        ("1:02:03.5", 3723.5),
    ],
)
def test_parse_timestamp(text: str, seconds: float) -> None:
    assert parse_timestamp(text) == pytest.approx(seconds)


def test_format_round_trips_through_parse() -> None:
    for seconds in (0.0, 5.0, 59.9, 60.0, 599.0, 3600.0, 7325.0):
        assert parse_timestamp(format_timestamp(seconds)) == pytest.approx(seconds, abs=1.0)
