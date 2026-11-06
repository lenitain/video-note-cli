"""把笔记里的 ``MM:SS`` / ``HH:MM:SS`` 读回秒。

``transcript`` 里的私有 ``_parse_timestamp`` 服务字幕解析，只认播放器导出的两种
写法；这里服务的是「读回模型写出的时间戳」：正文里的跨度标记、HTML 导出的锚点、
尾部补写时定位缺口，都要求同一套读法。``transcript.format_timestamp`` 是它的
逆运算。
"""
from __future__ import annotations


def parse_timestamp(value: str) -> float:
    """把 ``MM:SS`` / ``HH:MM:SS`` 读回秒；两段且分钟超过 99 时按累计分钟处理。"""
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return int(parts[0]) * 60 + float(parts[1])
