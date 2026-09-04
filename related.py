from __future__ import annotations

import json
import re
import urllib.request

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def extract_video_id(url: str) -> str | None:
    match = re.search(r"[?&]v=([\w-]{11})", url)
    return match.group(1) if match else None


def _fetch_initial_data(video_id: str) -> dict:
    req = urllib.request.Request(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html)
    if not match:
        raise ValueError("ytInitialData not found in watch page")
    return json.loads(match.group(1))


def _iter_sidebar_videos(initial_data: dict):
    results = initial_data["contents"]["twoColumnWatchNextResults"]["secondaryResults"]["secondaryResults"]["results"]
    for section in results:
        for item in section.get("itemSectionRenderer", {}).get("contents", []):
            lockup = item.get("lockupViewModel")
            if lockup and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                yield lockup


def _lockup_to_video(lockup: dict) -> dict:
    metadata = lockup["metadata"]["lockupMetadataViewModel"]
    channel = "알 수 없음"
    rows = metadata.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
    if rows:
        parts = rows[0].get("metadataParts", [])
        if parts:
            channel = parts[0].get("text", {}).get("content", channel)
    return {
        "title": metadata["title"]["content"],
        "channel": channel,
        "url": f"https://www.youtube.com/watch?v={lockup['contentId']}",
    }


def fetch_next(url: str, played_ids: set[str], current_channel: str | None = None) -> dict | None:
    """다음 자동재생 영상을 유튜브 워치 페이지의 '연관 동영상' 사이드바에서 찾는다.

    같은 채널의 미재생 영상이 있으면 그것을 우선한다 — 예능/드라마 회차는 같은 공식
    채널이 이어서 올리는 경우가 대부분이라 회차 연속 시청에 가장 잘 맞는다. 없으면
    사이드바의 첫 미재생 항목을 고른다.

    yt-dlp는 이 목록을 공식적으로 지원하지 않으므로 워치 페이지 HTML에 내장된
    ytInitialData를 직접 파싱한다. 유튜브가 이 JSON 구조를 바꾸면 조용히 깨질 수
    있는 비공식 경로이므로, 어떤 예외가 나든 여기서 흡수하고 None만 반환해
    상위(yp.py)의 재생 흐름에 영향이 전파되지 않게 한다.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return None

    try:
        initial_data = _fetch_initial_data(video_id)
        candidates = []
        for lockup in _iter_sidebar_videos(initial_data):
            content_id = lockup.get("contentId")
            if not content_id or content_id in played_ids:
                continue
            candidates.append(_lockup_to_video(lockup))
    except Exception:
        return None

    if not candidates:
        return None
    if current_channel:
        for video in candidates:
            if video["channel"] == current_channel:
                return video
    return candidates[0]
