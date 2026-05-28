from __future__ import annotations

import os
import subprocess
import tempfile

from yt_dlp import YoutubeDL


class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def fetch_comments(url: str, limit: int = 30) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(limit), "0", "0", "0"]}},
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    raw = info.get("comments") or []
    result = []
    for c in raw[:limit]:
        result.append({
            "author": c.get("author") or "알 수 없음",
            "text": c.get("text") or "",
            "like_count": c.get("like_count") or 0,
        })
    return result


def open_comments_window(comments: list[dict], title: str) -> None:
    pass
