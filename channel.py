"""채널 모드(-c): 채널을 찾고 최신 업로드를 페이지 단위로 받아 재생 순서를 준다."""
from __future__ import annotations

import re
from collections import Counter

from yt_dlp import YoutubeDL

from ytdlp_common import SilentLogger

PAGE_SIZE = 30
_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "logger": SilentLogger(),
    "http_headers": {"Accept-Language": "ko-KR,ko;q=0.9"},
    "extractor_args": {"youtube": {"lang": ["ko"]}},
}


def _extract(target: str, **extra) -> dict:
    with YoutubeDL({**_BASE_OPTS, **extra}) as ydl:
        return ydl.extract_info(target, download=False) or {}


def _channel_url(query: str) -> str | None:
    """@핸들이나 채널 URL이면 그 채널의 /videos 탭 URL. 이름이면 None."""
    if query.startswith("@"):
        return f"https://www.youtube.com/{query}/videos"
    m = re.match(r"https?://(?:www\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+)", query)
    return f"https://www.youtube.com/{m.group(1)}/videos" if m else None


def _channel_of(info: dict) -> dict | None:
    cid = info.get("channel_id")
    if not cid:
        return None
    name = (info.get("channel") or info.get("uploader") or "알 수 없음").strip()
    return {"id": cid, "name": name, "handle": info.get("uploader_id")}


def resolve_channel(query: str) -> dict | None:
    """query → {"id", "name", "handle"}. 이름이면 검색 상위 5개에서 가장 많이 나온 채널을 고른다."""
    try:
        url = _channel_url(query)
        if url is not None:
            return _channel_of(_extract(url, playlist_items="1:1"))
        entries = [e for e in _extract(f"ytsearch5:{query}").get("entries") or [] if e.get("channel_id")]
        if not entries:
            return None
        cid = Counter(e["channel_id"] for e in entries).most_common(1)[0][0]
        return _channel_of(next(e for e in entries if e["channel_id"] == cid))
    except Exception:
        return None


def channel_videos(channel_id: str, page: int = 1, name: str | None = None) -> list[dict]:
    """채널 최신 업로드의 page번째 묶음(PAGE_SIZE개). 항목 모양은 searcher.search와 같다."""
    start, end = (page - 1) * PAGE_SIZE + 1, page * PAGE_SIZE
    info = _extract(f"https://www.youtube.com/channel/{channel_id}/videos", playlist_items=f"{start}:{end}")
    channel = (name or info.get("channel") or info.get("uploader") or "알 수 없음").strip()
    videos = []
    for entry in info.get("entries") or []:
        url = entry.get("webpage_url") or entry.get("url")
        if url:
            videos.append({"title": entry.get("title") or "제목 없음", "channel": channel,
                           "url": url, "duration": entry.get("duration") or 0})
    return videos


class ChannelQueue:
    """지금까지 받은 채널 영상 목록. 재생 순서는 목록 위에서 아래(최신 → 과거)다."""

    def __init__(self, channel: dict):
        self.channel = channel
        self.videos: list[dict] = []
        self.exhausted = False
        self._pages = 0

    def ensure(self, count: int) -> None:
        """목록이 count개 이상 되도록 페이지를 받는다. 채널이 끝나면 exhausted."""
        while len(self.videos) < count and not self.exhausted:
            try:
                page = channel_videos(self.channel["id"], self._pages + 1, self.channel["name"])
            except Exception:
                page = []
            self._pages += 1
            self.videos.extend(page)
            if len(page) < PAGE_SIZE:
                self.exhausted = True

    def next_after(self, url: str) -> dict | None:
        """url 바로 아래 영상. 목록 끝이면 다음 페이지를 받고, 채널 끝이면 None."""
        index = next((i for i, v in enumerate(self.videos) if v["url"] == url), None)
        if index is None:
            return None
        self.ensure(index + 2)
        return self.videos[index + 1] if index + 1 < len(self.videos) else None
