import pytest
from unittest.mock import patch

from comments import (SORT_LABELS, CommentFeed, ReplyFeed, format_comment, format_header,
                      next_sort, _parse_page, _sort_tokens, _comment_section_token)


# --- fixture 빌더: 프로브에서 본 innertube 응답 모양을 최소 크기로 재현 ---

def _payload(key, cid, author="유저", text="본문", likes="12", replies="0", age="3일 전"):
    return {"key": key, "properties": {"commentId": cid, "content": {"content": text},
                                       "publishedTime": age},
            "author": {"displayName": author},
            "toolbar": {"likeCountNotliked": likes, "replyCount": replies}}


def _cir(token, via_button=False):
    cmd = {"continuationCommand": {"token": token}}
    if via_button:
        return {"continuationItemRenderer": {"button": {"buttonRenderer": {"command": cmd}}}}
    return {"continuationItemRenderer": {"trigger": "x", "continuationEndpoint": cmd}}


def _thread(key, cid, reply_token=None):
    thread = {"commentViewModel": {"commentViewModel": {"commentKey": key, "commentId": cid}}}
    if reply_token:
        thread["replies"] = {"commentRepliesRenderer": {"contents": [_cir(reply_token)]}}
    return {"commentThreadRenderer": thread}


def _bare(key, cid):
    return {"commentViewModel": {"commentKey": key, "commentId": cid}}


def _header(top="TOP", new="NEW"):
    items = [{"title": "Top", "serviceEndpoint": {"continuationCommand": {"token": top}}},
             {"title": "Newest", "serviceEndpoint": {"continuationCommand": {"token": new}}}]
    return {"commentsHeaderRenderer": {"sortMenu": {"sortFilterSubMenuRenderer": {"subMenuItems": items}}}}


def _resp(items, payloads, header=None, append=False):
    key = "appendContinuationItemsAction" if append else "reloadContinuationItemsCommand"
    endpoints = []
    if header is not None:
        endpoints.append({"reloadContinuationItemsCommand": {"continuationItems": [header]}})
    endpoints.append({key: {"continuationItems": items}})
    return {"onResponseReceivedEndpoints": endpoints,
            "frameworkUpdates": {"entityBatchUpdate": {"mutations": [
                {"entityKey": p["key"], "type": "x", "payload": {"commentEntityPayload": p}}
                for p in payloads]}}}


# --- _parse_page ---

def test_parse_page_keeps_thread_order_and_reads_the_reply_token():
    resp = _resp([_thread("k1", "c1", reply_token="R1"), _thread("k2", "c2"), _cir("NEXT")],
                 [_payload("k2", "c2", author="B", replies="0"),
                  _payload("k1", "c1", author="A", replies="27", likes="31만", age="6년 전(수정됨)")])
    page, nxt = _parse_page(resp)
    assert [c["author"] for c in page] == ["A", "B"]
    assert page[0] == {"id": "c1", "author": "A", "text": "본문", "likes": "31만",
                       "age": "6년 전(수정됨)", "reply_count": 27, "reply_token": "R1"}
    assert page[1]["reply_token"] is None and page[1]["reply_count"] == 0
    assert nxt == "NEXT"


def test_parse_page_reads_bare_view_models_and_button_continuations_on_reply_pages():
    resp = _resp([_bare("k1", "r1"), _bare("k2", "r2"), _cir("MORE", via_button=True)],
                 [_payload("k1", "r1", author="X"), _payload("k2", "r2", author="Y")], append=True)
    page, nxt = _parse_page(resp)
    assert [c["author"] for c in page] == ["X", "Y"]
    assert all(c["reply_token"] is None for c in page)
    assert nxt == "MORE"


def test_parse_page_without_a_continuation_reports_none():
    resp = _resp([_thread("k1", "c1")], [_payload("k1", "c1")])
    assert _parse_page(resp)[1] is None


def test_parse_page_skips_items_without_a_payload_and_the_header():
    resp = _resp([_header(), _thread("k1", "c1"), _thread("ghost", "c9")], [_payload("k1", "c1")])
    page, _ = _parse_page(resp)
    assert [c["id"] for c in page] == ["c1"]


def test_parse_page_absorbs_missing_fields_per_field():
    payload = {"key": "k1", "properties": {"commentId": "c1"}}
    page, _ = _parse_page(_resp([_thread("k1", "c1")], [payload]))
    assert page[0] == {"id": "c1", "author": "알 수 없음", "text": "", "likes": None,
                       "age": None, "reply_count": 0, "reply_token": None}


def test_parse_page_treats_zero_likes_as_no_likes():
    page, _ = _parse_page(_resp([_thread("k1", "c1")], [_payload("k1", "c1", likes="0")]))
    assert page[0]["likes"] is None


def test_parse_page_raises_when_the_response_has_no_comment_data_at_all():
    with pytest.raises(ValueError):
        _parse_page({"onResponseReceivedEndpoints": [], "frameworkUpdates": {}})


def test_parse_page_with_only_a_header_is_an_empty_page():
    page, nxt = _parse_page(_resp([_header()], []))
    assert page == [] and nxt is None


# --- 토큰 헬퍼 ---

def test_sort_tokens_come_from_the_header_menu():
    assert _sort_tokens(_resp([_header("T", "N")], [])) == {"top": "T", "new": "N"}


def test_sort_tokens_are_none_without_a_header():
    assert _sort_tokens(_resp([_thread("k1", "c1")], [_payload("k1", "c1")])) is None


def test_comment_section_token_is_found_by_section_identifier():
    data = {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": [
        {"itemSectionRenderer": {"sectionIdentifier": "other", "contents": [_cir("X")]}},
        {"itemSectionRenderer": {"sectionIdentifier": "comment-item-section",
                                 "contents": [_cir("SECTION")]}},
    ]}}}}}
    assert _comment_section_token(data) == "SECTION"
    assert _comment_section_token({"contents": {}}) is None


# --- CommentFeed ---

def _net(responses: dict, initial=None):
    """innertube를 patch한다. responses는 토큰 → 응답. initial은 watch 페이지 ytInitialData."""
    calls = []

    def fake_next(token, hl="ko"):
        calls.append(token)
        return responses[token]
    p1 = patch("comments.next_continuation", fake_next)
    p2 = patch("comments.fetch_initial_data", lambda vid: initial)
    p1.start(); p2.start()
    return calls, (p1, p2)


def _initial(token="SECTION"):
    return {"contents": {"x": [{"itemSectionRenderer": {"sectionIdentifier": "comment-item-section",
                                                        "contents": [_cir(token)]}}]}}


URL = "https://www.youtube.com/watch?v=abcdefghijk"


def test_feed_first_page_walks_watch_page_then_section_token_and_learns_sort_tokens():
    first = _resp([_thread("k1", "c1"), _cir("P2")], [_payload("k1", "c1")], header=_header("T", "N"))
    calls, ps = _net({"SECTION": first}, initial=_initial("SECTION"))
    try:
        feed = CommentFeed(URL)
        page, more = feed.next_page()
    finally:
        [p.stop() for p in ps]
    assert calls == ["SECTION"]
    assert [c["id"] for c in page] == ["c1"] and more is True
    assert feed.sort_tokens == {"top": "T", "new": "N"}
    assert feed.shown == 1


def test_feed_follows_the_continuation_and_stops_when_it_runs_out():
    first = _resp([_thread("k1", "c1"), _cir("P2")], [_payload("k1", "c1")], header=_header())
    second = _resp([_thread("k2", "c2")], [_payload("k2", "c2")], append=True)
    calls, ps = _net({"SECTION": first, "P2": second}, initial=_initial())
    try:
        feed = CommentFeed(URL)
        feed.next_page()
        page, more = feed.next_page()
        tail = feed.next_page()
    finally:
        [p.stop() for p in ps]
    assert calls == ["SECTION", "P2"]
    assert [c["id"] for c in page] == ["c2"] and more is False
    assert tail == ([], False)
    assert feed.shown == 2


def test_feed_for_newest_sort_fetches_top_once_to_learn_the_token_then_switches():
    top = _resp([_thread("k1", "c1")], [_payload("k1", "c1")], header=_header("T", "N"))
    new = _resp([_thread("k2", "c2")], [_payload("k2", "c2")])
    calls, ps = _net({"SECTION": top, "N": new}, initial=_initial())
    try:
        page, _ = CommentFeed(URL, sort="new").next_page()
    finally:
        [p.stop() for p in ps]
    assert calls == ["SECTION", "N"]
    assert [c["id"] for c in page] == ["c2"]


def test_feed_with_known_sort_tokens_skips_the_watch_page():
    new = _resp([_thread("k2", "c2")], [_payload("k2", "c2")])
    calls, ps = _net({"N": new}, initial=None)
    try:
        feed = CommentFeed(URL, sort="new", sort_tokens={"top": "T", "new": "N"})
        page, _ = feed.next_page()
    finally:
        [p.stop() for p in ps]
    assert calls == ["N"] and [c["id"] for c in page] == ["c2"]


def test_feed_without_a_comment_section_is_empty():
    calls, ps = _net({}, initial={"contents": {}})
    try:
        assert CommentFeed(URL).next_page() == ([], False)
    finally:
        [p.stop() for p in ps]
    assert calls == []


def test_feed_drops_a_comment_id_it_already_showed():
    first = _resp([_thread("k1", "c1"), _cir("P2")], [_payload("k1", "c1")], header=_header())
    second = _resp([_thread("k1", "c1"), _thread("k2", "c2")],
                   [_payload("k1", "c1"), _payload("k2", "c2")], append=True)
    _, ps = _net({"SECTION": first, "P2": second}, initial=_initial())
    try:
        feed = CommentFeed(URL)
        feed.next_page()
        page, _ = feed.next_page()
    finally:
        [p.stop() for p in ps]
    assert [c["id"] for c in page] == ["c2"]


def test_feed_rejects_a_non_watch_url():
    _, ps = _net({}, initial=None)
    try:
        with pytest.raises(ValueError):
            CommentFeed("https://example.com/").next_page()
    finally:
        [p.stop() for p in ps]


def test_next_sort_toggles_between_the_two_sorts():
    assert next_sort("top") == "new"
    assert next_sort("new") == "top"
    assert set(SORT_LABELS) == {"top", "new"}


# --- ReplyFeed ---

def test_reply_feed_starts_from_the_reply_token_and_follows_button_continuations():
    r1 = _resp([_bare("k1", "r1"), _cir("R2", via_button=True)], [_payload("k1", "r1")], append=True)
    r2 = _resp([_bare("k2", "r2")], [_payload("k2", "r2")], append=True)
    calls, ps = _net({"R1": r1, "R2": r2})
    try:
        feed = ReplyFeed("R1")
        p1, more1 = feed.next_page()
        p2, more2 = feed.next_page()
        tail = feed.next_page()
    finally:
        [p.stop() for p in ps]
    assert calls == ["R1", "R2"]
    assert ([c["id"] for c in p1], more1) == (["r1"], True)
    assert ([c["id"] for c in p2], more2) == (["r2"], False)
    assert tail == ([], False) and feed.shown == 2


# --- 포맷터 ---

def _c(**over):
    base = {"id": "c", "author": "유저", "text": "본문", "likes": None, "age": None,
            "reply_count": 0, "reply_token": None}
    base.update(over)
    return base


def test_format_comment_puts_likes_replies_and_age_in_that_order():
    lines = format_comment(_c(likes="31만", reply_count=27, reply_token="R", age="6년 전"), 4)
    assert lines[0] == "  4. 유저 | 좋아요 31만 | 답글 27 | 6년 전"
    assert lines[1] == "     본문"
    assert lines[-1] == ""


def test_format_comment_omits_unknown_meta():
    assert format_comment(_c(), 12)[0] == " 12. 유저"


def test_format_comment_says_replies_exist_even_when_the_count_is_unreadable():
    assert format_comment(_c(reply_token="R"), 1)[0] == "  1. 유저 | 답글"


def test_format_comment_marks_replies_with_an_arrow():
    assert format_comment(_c(author="X"), 2, reply=True)[0] == "  2. ↳ X"


def test_format_comment_wraps_long_text_and_splits_newlines():
    lines = format_comment(_c(text="a" * 100 + "\nb"), 1)
    assert lines[1] == "     " + "a" * 76
    assert lines[2] == "     " + "a" * 24
    assert lines[3] == "     b"


def test_format_header_with_and_without_a_note():
    assert format_header("제목", "인기순") == ["제목", "━" * 44, "인기순", ""]
    assert format_header("제목") == ["제목", "━" * 44, ""]
