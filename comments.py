from __future__ import annotations

import os

# Must be set before yt_dlp is imported: a user's globally-installed yt-dlp
# plugin (e.g. a broken PO-token provider under ~/.config/yt-dlp/plugins/)
# can raise on load and take down comment fetching with it. Comments don't
# need any extractor plugins, so disable plugin auto-loading entirely.
os.environ["YTDLP_NO_PLUGINS"] = "1"

from yt_dlp import YoutubeDL

from ytdlp_common import SilentLogger


def fetch_comments(url: str, limit: int = 100) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": SilentLogger(),
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(limit)], "comment_sort": ["top"]}},
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
            "parent": c.get("parent") or "root",
        })
    return result


def _wrap(text: str, width: int, indent: str) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        while True:
            lines.append(indent + paragraph[:width])
            paragraph = paragraph[width:]
            if not paragraph:
                break
    return lines


def format_comments(comments: list[dict], title: str) -> list[str]:
    """댓글 목록을 페이저에 그릴 줄 목록으로 만든다. 답글은 한 단계 들여쓴다."""
    lines = [title, "━" * 44, ""]
    for i, c in enumerate(comments, 1):
        like_count = c.get("like_count") or 0
        like = f" | 좋아요 {like_count:,}" if like_count else ""
        author = c.get("author") or "알 수 없음"
        text = c.get("text") or ""
        if c.get("parent", "root") != "root":
            lines.append(f"     └─ {author}{like}")
            lines.extend(_wrap(text, 72, "        "))
        else:
            lines.append(f" {i:2}. {author}{like}")
            lines.extend(_wrap(text, 76, "     "))
        lines.append("")
    return lines
