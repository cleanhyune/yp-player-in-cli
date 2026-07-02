from __future__ import annotations

import os
import subprocess
import tempfile

# Must be set before yt_dlp is imported: a user's globally-installed yt-dlp
# plugin (e.g. a broken PO-token provider under ~/.config/yt-dlp/plugins/)
# can raise on load and take down comment fetching with it. Comments don't
# need any extractor plugins, so disable plugin auto-loading entirely.
os.environ["YTDLP_NO_PLUGINS"] = "1"

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
        })
    return result


def open_comments_window(comments: list[dict], title: str) -> None:
    lines = [title, "━" * 44, ""]
    for i, c in enumerate(comments, 1):
        like = f" | 👍 {c['like_count']:,}" if c["like_count"] else ""
        lines.append(f" {i:2}. {c['author']}{like}")
        text = c["text"]
        while text:
            lines.append(f"     {text[:76]}")
            text = text[76:]
        lines.append("")
    lines.append("(엔터로 창 닫기)")

    content = "\n".join(lines)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(content)
        tmp.close()
        if os.environ.get("TMUX"):
            subprocess.run(
                ["tmux", "new-window", f"cat {tmp.name}; read; rm {tmp.name}"],
                check=False,
            )
        else:
            script = (
                f'tell application "Terminal" to do script '
                f'"cat {tmp.name}; read; rm {tmp.name}"'
            )
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
