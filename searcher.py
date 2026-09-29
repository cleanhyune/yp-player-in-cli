"""innertube 검색 응답을 yp의 결과 모양으로 바꾼다.

yt-dlp의 flat 검색을 쓰지 않는 이유: 업로드 시기가 "5일 전"처럼 한국어 상대 표기로만 오는데
yt-dlp는 영어 표기만 timestamp로 바꾸고 원문은 버린다. 여기서는 댓글의 시기 표기처럼
YouTube가 준 문자열을 그대로 age에 싣는다. 파싱은 related.py처럼 자체 유지보수 대상이다.
"""
from __future__ import annotations

import innertube


def search(query: str, max_results: int = 30) -> list[dict]:
    results: list[dict] = []
    data = innertube.search(query)
    while True:
        videos, token = _parse_page(data)
        results.extend(videos)
        if len(results) >= max_results or token is None:
            return results[:max_results]
        data = innertube.search(continuation=token)


def _parse_page(data: dict) -> tuple[list[dict], str | None]:
    videos = []
    token = None
    for item in _walk(data):
        renderer = item.get("videoRenderer")
        if renderer is not None:
            video = _parse_video(renderer)
            if video is not None:
                videos.append(video)
            continue
        cont = item.get("continuationItemRenderer")
        if cont is not None and token is None:
            token = (cont.get("continuationEndpoint", {})
                     .get("continuationCommand", {}).get("token"))
    return videos, token


def _walk(node):
    """videoRenderer나 continuationItemRenderer를 품은 dict를 어디에 있든 찾는다. 첫 페이지와
    continuation 페이지의 겉모양이 달라 위치를 고정하지 않는다."""
    if isinstance(node, dict):
        if "videoRenderer" in node or "continuationItemRenderer" in node:
            yield node
            return
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _parse_video(r: dict) -> dict | None:
    video_id = r.get("videoId")
    if not video_id:
        return None
    return {
        "title": _text(r.get("title")) or "제목 없음",
        "channel": _text(r.get("ownerText")) or "알 수 없음",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": _parse_length(_text(r.get("lengthText"))),
        "age": _text(r.get("publishedTimeText")) or None,
    }


def _text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"] or ""
    return "".join(run.get("text", "") for run in node.get("runs", []) or [])


def _parse_length(text: str) -> int:
    parts = text.split(":") if text else []
    if not parts or not all(p.isdigit() for p in parts):
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds
