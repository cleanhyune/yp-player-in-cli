"""YouTube 댓글·답글. innertube 응답을 직접 파싱한다.

yt-dlp를 쓰지 않는 이유: 댓글 dict에 답글 수가 없고, "이 댓글의 답글만" 가져오는 옵션이 없다
(부모 1..k번 답글을 순서대로 각 1요청씩 받는 구조라 20번째 댓글 하나를 열려면 요청 20번).
innertube는 최상위 20개/0.7초, 답글 10개/0.5초에 답글 수와 답글 커서를 같이 준다 (2026-09 실측).
이 파서는 비공식이라 YouTube가 JSON 모양을 바꾸면 여기를 고쳐야 한다 — `pip install -U yt-dlp`는
도움이 안 된다.
"""
from __future__ import annotations

from innertube import fetch_initial_data, next_continuation
from related import extract_video_id

SORT_LABELS = {"top": "인기순", "new": "최신순"}


def next_sort(sort: str) -> str:
    return "new" if sort == "top" else "top"


def _walk(obj, key: str):
    """중첩 dict/list에서 key의 값을 깊이 우선으로 전부 낸다."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for value in obj.values():
            yield from _walk(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value, key)


def _first(obj, key: str, default=None):
    return next(_walk(obj, key), default)


def _token(node) -> str | None:
    """node 아래 첫 continuationCommand의 토큰. 루트 다음 페이지는 continuationEndpoint 아래,
    답글 다음 페이지는 button.buttonRenderer.command 아래에 있어 경로를 고정하지 않는다."""
    command = _first(node, "continuationCommand")
    token = command.get("token") if isinstance(command, dict) else None
    return token if isinstance(token, str) and token else None


def _int(text) -> int:
    try:
        return int(str(text).replace(",", ""))
    except ValueError:
        return 0


def _comment_from_payload(payload: dict, reply_token: str | None) -> dict:
    """commentEntityPayload 하나를 댓글 dict로. 필드가 깨지면 그 필드만 비운다."""
    props = payload.get("properties") or {}
    toolbar = payload.get("toolbar") or {}
    # hl=ko에서 좋아요 수는 "31만"처럼 축약 문자열로 온다. 정수로 되돌리지 않고 그대로 보여준다.
    likes = toolbar.get("likeCountNotliked") or None
    if likes == "0":
        likes = None
    return {
        "id": props.get("commentId") or "",
        "author": (payload.get("author") or {}).get("displayName") or "알 수 없음",
        "text": (props.get("content") or {}).get("content") or "",
        "likes": likes,
        "age": props.get("publishedTime") or None,
        "reply_count": _int(toolbar.get("replyCount")),
        "reply_token": reply_token,
    }


def _view_model(node: dict) -> dict | None:
    """commentViewModel을 꺼낸다. 스레드 안에서는 한 겹 더 싸여 있다
    (commentThreadRenderer.commentViewModel.commentViewModel)."""
    vm = node.get("commentViewModel")
    if isinstance(vm, dict) and "commentKey" not in vm and isinstance(vm.get("commentViewModel"), dict):
        vm = vm["commentViewModel"]
    return vm if isinstance(vm, dict) else None


def _continuation_items(resp: dict):
    for endpoint in resp.get("onResponseReceivedEndpoints") or []:
        for key in ("reloadContinuationItemsCommand", "appendContinuationItemsAction"):
            for item in (endpoint.get(key) or {}).get("continuationItems") or []:
                yield item


def _parse_page(resp: dict) -> tuple[list[dict], str | None]:
    """next 응답 한 장을 (순서대로의 댓글, 다음 페이지 토큰)으로.

    본문은 frameworkUpdates의 commentEntityPayload에 키로 흩어져 있고, 순서와 답글 커서는
    continuationItems의 commentThreadRenderer(루트) / 맨몸 commentViewModel(답글)에 있다.
    """
    payloads = {p.get("key"): p for p in _walk(resp.get("frameworkUpdates") or {}, "commentEntityPayload")
                if isinstance(p, dict)}
    comments: list[dict] = []
    next_token = None
    saw_items = False
    for item in _continuation_items(resp):
        saw_items = True
        if "commentThreadRenderer" in item:
            thread = item["commentThreadRenderer"]
            vm, reply_token = _view_model(thread), _token(thread.get("replies") or {})
        elif "commentViewModel" in item:
            vm, reply_token = _view_model(item), None
        elif "continuationItemRenderer" in item:
            next_token = _token(item)
            continue
        else:
            continue  # commentsHeaderRenderer 등
        payload = payloads.get((vm or {}).get("commentKey"))
        if payload is not None:
            comments.append(_comment_from_payload(payload, reply_token))
    if not saw_items and not payloads:
        raise ValueError("no comment items in innertube response")
    return comments, next_token


def _sort_tokens(resp: dict) -> dict[str, str] | None:
    """첫 응답 헤더의 정렬 메뉴에서 {"top": 토큰, "new": 토큰}. 메뉴가 없으면 None."""
    header = _first(resp, "commentsHeaderRenderer")
    items = _first(header or {}, "subMenuItems") or []
    tokens = [_token(item) for item in items[:2]]
    if len(tokens) == 2 and all(tokens):
        return {"top": tokens[0], "new": tokens[1]}
    return None


def _comment_section_token(initial_data: dict) -> str | None:
    for section in _walk(initial_data.get("contents") or {}, "itemSectionRenderer"):
        if isinstance(section, dict) and section.get("sectionIdentifier") == "comment-item-section":
            return _token(section)
    return None


class CommentFeed:
    """한 영상의 최상위 댓글을 YouTube의 continuation 커서를 따라 페이지 단위로 넘겨준다.

    첫 페이지는 watch 페이지 → 댓글 섹션 토큰 → next 순서로 얻고, 그 응답 헤더에서 정렬 메뉴의
    토큰 두 개(top/new)를 배워 `sort_tokens`에 둔다. 정렬을 바꿀 때는 이 토큰을 새 피드에 넘겨
    watch 페이지를 다시 받지 않는다. 페이지 사이 중복은 실측 0이지만 고정 댓글이 최신순에서
    두 번 오는 yt-dlp 보고 사례가 있어 id 기준 중복 제거를 안전장치로 남긴다.
    """

    def __init__(self, url: str, sort: str = "top", sort_tokens: dict[str, str] | None = None):
        self.url = url
        self.sort = sort
        self.sort_tokens = sort_tokens
        self.shown = 0            # 지금까지 내보낸 댓글 수 = 다음 페이지의 시작 번호 - 1
        self._next: str | None = None
        self._started = False
        self._seen: set[str] = set()

    def next_page(self) -> tuple[list[dict], bool]:
        """다음 페이지를 가져온다. 반환값은 (댓글, 뒤에 더 있음)."""
        if not self._started:
            self._started = True
            resp = self._first_response()
            if resp is None:
                return [], False
        elif self._next is None:
            return [], False
        else:
            resp = next_continuation(self._next)
        comments, self._next = _parse_page(resp)
        page = []
        for c in comments:
            if c["id"] in self._seen:
                continue
            self._seen.add(c["id"])
            page.append(c)
        self.shown += len(page)
        return page, self._next is not None

    def _first_response(self) -> dict | None:
        if self.sort_tokens is None:
            video_id = extract_video_id(self.url)
            if video_id is None:
                raise ValueError(f"not a YouTube watch URL: {self.url}")
            token = _comment_section_token(fetch_initial_data(video_id))
            if token is None:
                return None  # 댓글이 꺼진 영상
            resp = next_continuation(token)
            self.sort_tokens = _sort_tokens(resp)
            if self.sort == "top" or self.sort_tokens is None:
                return resp
        return next_continuation(self.sort_tokens[self.sort])


class ReplyFeed:
    """댓글 하나의 답글을 답글 커서부터 페이지 단위로 넘겨준다."""

    def __init__(self, token: str):
        self.shown = 0
        self._next: str | None = token

    def next_page(self) -> tuple[list[dict], bool]:
        if self._next is None:
            return [], False
        replies, self._next = _parse_page(next_continuation(self._next))
        self.shown += len(replies)
        return replies, self._next is not None


def _wrap(text: str, width: int, indent: str) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        while True:
            lines.append(indent + paragraph[:width])
            paragraph = paragraph[width:]
            if not paragraph:
                break
    return lines


def format_header(title: str, note: str | None = None) -> list[str]:
    """페이저 머리: 제목, 구분선, (정렬 안내), 빈 줄."""
    lines = [title, "━" * 44]
    if note is not None:
        lines.append(note)
    lines.append("")
    return lines


def format_comment(c: dict, index: int, reply: bool = False) -> list[str]:
    """댓글 한 개를 페이저 블록 한 덩이로. 첫 줄 앞 한 칸은 선택 표시(▶) 자리다."""
    parts = []
    if c.get("likes"):
        parts.append(f"좋아요 {c['likes']}")
    if c.get("reply_count"):
        parts.append(f"답글 {c['reply_count']:,}")
    elif c.get("reply_token"):
        parts.append("답글")
    if c.get("age"):
        parts.append(c["age"])
    meta = "".join(f" | {part}" for part in parts)
    author = c.get("author") or "알 수 없음"
    lines = [f" {index:2}. {'↳ ' if reply else ''}{author}{meta}"]
    lines.extend(_wrap(c.get("text") or "", 76, "     "))
    lines.append("")
    return lines
