"""YouTube 내부 API(innertube) 최소 클라이언트. yp 지식은 없다.

searcher.py(검색), related.py(추천 사이드바), comments.py(댓글·답글)가 함께 쓴다. yt-dlp는 사이드바를 노출하지
않고 답글 커서도 숨기므로 직접 호출한다. 이 모듈은 요청/응답 전달만 하고 응답 모양은 각 파서가
책임진다 — YouTube가 JSON을 바꾸면 고칠 곳은 searcher.py / related.py / comments.py다.
"""
from __future__ import annotations

import json
import re
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_NEXT_URL = "https://www.youtube.com/youtubei/v1/next?prettyPrint=false"
_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
# 2026-09 실측: API 키 없이 WEB 클라이언트 컨텍스트만으로 통한다.
_CLIENT = {"clientName": "WEB", "clientVersion": "2.20250101.00.00"}


def fetch_initial_data(video_id: str) -> dict:
    """watch 페이지에 박힌 ytInitialData JSON."""
    req = urllib.request.Request(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html)
    if not match:
        raise ValueError("ytInitialData not found in watch page")
    return json.loads(match.group(1))


def next_continuation(token: str, hl: str = "ko") -> dict:
    """continuation 토큰 하나로 /youtubei/v1/next를 한 번 호출한다.

    hl은 응답 문자열의 언어다. 댓글은 "6일 전"처럼 YouTube가 이미 번역한 시기 표기를 그대로
    쓰기 위해 ko를 기본으로 한다.
    """
    body = json.dumps({"context": {"client": {**_CLIENT, "hl": hl}},
                       "continuation": token}).encode("utf-8")
    req = urllib.request.Request(
        _NEXT_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(query: str | None = None, continuation: str | None = None, hl: str = "ko") -> dict:
    """/youtubei/v1/search 한 번. query로 첫 페이지를, continuation으로 다음 페이지를 받는다."""
    payload = {"context": {"client": {**_CLIENT, "hl": hl}}}
    if continuation is not None:
        payload["continuation"] = continuation
    else:
        payload["query"] = query
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _SEARCH_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))
