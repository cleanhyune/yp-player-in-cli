from __future__ import annotations

import re

from yt_dlp import YoutubeDL


class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def extract_video_id(url: str) -> str | None:
    match = re.search(r"[?&]v=([\w-]{11})", url)
    return match.group(1) if match else None


def fetch_next(url: str, played_ids: set[str]) -> dict | None:
    video_id = extract_video_id(url)
    if not video_id:
        return None

    mix_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "logger": _SilentLogger(),
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(mix_url, download=False)
    except Exception:
        return None

    entries = info.get("entries") or []
    for entry in entries:
        entry_url = entry.get("url") or entry.get("webpage_url")
        entry_id = entry.get("id") or extract_video_id(entry_url or "")
        if not entry_id or entry_id in played_ids:
            continue
        return {
            "title": entry.get("title", "제목 없음"),
            "channel": entry.get("channel") or entry.get("uploader") or "알 수 없음",
            "url": entry_url or f"https://www.youtube.com/watch?v={entry_id}",
            "duration": entry.get("duration") or 0,
        }
    return None
