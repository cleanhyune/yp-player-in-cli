from __future__ import annotations

import os
import re

# Must be set before yt_dlp is imported: a user's globally-installed yt-dlp
# plugin (e.g. a broken PO-token provider under ~/.config/yt-dlp/plugins/)
# can raise on load and take down comment fetching with it. Comments don't
# need any extractor plugins, so disable plugin auto-loading entirely.
os.environ["YTDLP_NO_PLUGINS"] = "1"

from yt_dlp import YoutubeDL

from ytdlp_common import SilentLogger

PAGE_SIZE = 100

# YouTube가 지원하는 최상위 댓글 정렬. "top"은 요청마다 순서가 흔들리고 "new"는 안정적이다
# (CommentFeed의 중복 제거가 필요한 이유).
SORT_LABELS = {"top": "인기순", "new": "최신순"}


def next_sort(sort: str) -> str:
    return "new" if sort == "top" else "top"


def fetch_root_comments(url: str, offset: int = 0, limit: int = PAGE_SIZE,
                        sort: str = "top") -> tuple[list[dict], bool]:
    """최상위 댓글을 offset부터 limit개 가져온다. 반환값은 (댓글, 뒤에 더 있을 수 있음).

    yt-dlp는 "여기서부터 이어서" 같은 커서를 노출하지 않고 상한값만 받으므로, 다음 페이지는
    offset+limit개를 다시 걷고 앞부분을 잘라내서 만든다. 답글 상한을 0으로 내려 답글
    continuation 요청을 아예 없애 이 재조회 비용을 낮게 유지한다.
    """
    want = offset + limit
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": SilentLogger(),
        "getcomments": True,
        # max_comments = [total, max_parents, max_replies, max_replies_per_thread]; 빈 값은 무제한.
        "extractor_args": {"youtube": {"max_comments": ["", str(want), "0", "0"],
                                       "comment_sort": [sort]}},
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    roots = [c for c in (info.get("comments") or []) if (c.get("parent") or "root") == "root"]
    # 요청한 만큼 다 받았으면 아직 뒤가 남아 있다고 본다.
    more = len(roots) >= want
    page = [{
        "author": c.get("author") or "알 수 없음",
        "text": c.get("text") or "",
        "like_count": c.get("like_count") or 0,
        "age": format_age(c.get("_time_text")),
    } for c in roots[offset:]]
    return page, more


class CommentFeed:
    """한 영상의 최상위 댓글을 페이지 단위로 넘겨준다.

    yt-dlp에는 커서가 없어서 매 페이지를 offset+page_size개 다시 걷고 앞부분을 잘라내는데,
    "top" 정렬은 요청 사이에 순서가 흔들려서 그 슬라이스만으로는 같은 댓글이 두 번 나온다
    (실측: 40개 중 6~38개가 재정렬됨). 그래서 (작성자, 본문) 키로 이미 보여준 것을 걸러낸다.
    걸러진 만큼 페이지가 page_size보다 짧아지고, 반대로 앞으로 밀려온 댓글은 못 보게 된다 —
    "new" 정렬은 안정적이라 이 손실이 없다.
    """

    def __init__(self, url: str, sort: str = "top", page_size: int = PAGE_SIZE):
        self.url = url
        self.sort = sort
        self.shown = 0            # 지금까지 내보낸 댓글 수 = 다음 페이지의 시작 번호 - 1
        self._page_size = page_size
        self._offset = 0          # YouTube 목록에서 건너뛸 개수
        self._seen: set[tuple[str, str]] = set()

    def next_page(self) -> tuple[list[dict], bool]:
        """다음 페이지를 가져온다. 반환값은 (댓글, 뒤에 더 있을 수 있음)."""
        raw, more = fetch_root_comments(self.url, offset=self._offset,
                                        limit=self._page_size, sort=self.sort)
        page = []
        for c in raw:
            key = (c["author"], c["text"])
            if key in self._seen:
                continue
            self._seen.add(key)
            page.append(c)
        self._offset += self._page_size
        self.shown += len(page)
        return page, more


_AGE_UNITS = {"minute": "분", "hour": "시간", "day": "일",
              "week": "주", "month": "개월", "year": "년"}
_AGE_RE = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago")


def format_age(time_text: str | None) -> str | None:
    """YouTube의 상대 표기("5 hours ago")를 한국어로 옮긴다. 못 읽으면 None.

    같은 정보를 담은 `timestamp`를 쓰지 않는 이유: yt-dlp는 상대 표기를 역산한 뒤 자정/정시로
    양자화하므로 (실측: "5 hours ago" → 22:00:00, 실제 차이 4.79시간) 다시 나누면 단위가
    하나씩 밀리고, YouTube의 "일" 버킷이 13일까지 뻗는 것 같은 규칙도 맞춰줘야 한다. 원본
    문자열을 옮기면 그 전부가 필요 없다. `_time_text`는 yt-dlp 내부 필드라 사라질 수 있는데,
    그때는 시기 표시만 조용히 빠진다 — 틀린 시기가 나오는 것보다 낫다.
    """
    m = _AGE_RE.search(time_text or "")
    if m is None:
        return None
    count, unit = m.group(1), m.group(2)
    if unit == "second":
        return "방금 전"
    return f"{count}{_AGE_UNITS[unit]} 전"


def _wrap(text: str, width: int, indent: str) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        while True:
            lines.append(indent + paragraph[:width])
            paragraph = paragraph[width:]
            if not paragraph:
                break
    return lines


def format_comments(comments: list[dict], title: str | None = None, start_index: int = 1,
                    note: str | None = None) -> list[str]:
    """댓글 목록을 페이저에 그릴 줄 목록으로 만든다.

    title이 없으면 헤더를 붙이지 않는다 (열려 있는 페이저 뒤에 이어붙일 페이지용).
    note는 제목 구분선 아래에 한 줄로 들어간다 (현재 정렬 표시용).
    """
    lines = []
    if title is not None:
        lines = [title, "━" * 44]
        if note is not None:
            lines.append(note)
        lines.append("")
    for i, c in enumerate(comments, start_index):
        like_count = c.get("like_count") or 0
        meta = "".join(f" | {part}" for part in (
            f"좋아요 {like_count:,}" if like_count else None, c.get("age")) if part)
        author = c.get("author") or "알 수 없음"
        lines.append(f" {i:2}. {author}{meta}")
        lines.extend(_wrap(c.get("text") or "", 76, "     "))
        lines.append("")
    return lines
