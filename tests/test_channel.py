from unittest.mock import patch

import pytest

import channel
from channel import PAGE_SIZE, ChannelQueue, channel_videos, resolve_channel

CID = "UC-3GPiUGCTQkVuxAYR9qpwQ"


def _ydl(infos):
    """channel.YoutubeDL을 patch. infos는 호출 순서대로 돌려줄 info dict 목록."""
    patcher = patch("channel.YoutubeDL")
    MockYDL = patcher.start()
    inst = MockYDL.return_value.__enter__.return_value
    inst.extract_info.side_effect = list(infos)
    return patcher, MockYDL, inst


def _entry(i, channel_id=CID, name="조강현 "):
    return {"title": f"영상{i}", "url": f"https://www.youtube.com/watch?v=v{i:010d}", "duration": 100 + i,
            "channel": name, "channel_id": channel_id, "uploader_id": "@조강현_MessiOfTFT"}


def _page(n, start=1, name="조강현 "):
    return {"channel": name, "channel_id": CID, "uploader_id": "@조강현_MessiOfTFT",
            "entries": [_entry(i) for i in range(start, start + n)]}


# --- resolve_channel ---

def test_resolve_channel_by_name_picks_the_most_common_channel_in_the_top_results():
    patcher, MockYDL, inst = _ydl([{"entries": [
        _entry(1, "UCother", "다른 채널"), _entry(2), _entry(3), _entry(4, "UCother", "다른 채널"), _entry(5)]}])
    try:
        ch = resolve_channel("조강현")
    finally:
        patcher.stop()
    assert ch == {"id": CID, "name": "조강현", "handle": "@조강현_MessiOfTFT"}
    inst.extract_info.assert_called_once_with("ytsearch5:조강현", download=False)


def test_resolve_channel_by_handle_reads_the_channel_page():
    patcher, MockYDL, inst = _ydl([_page(1)])
    try:
        ch = resolve_channel("@조강현_MessiOfTFT")
    finally:
        patcher.stop()
    assert ch == {"id": CID, "name": "조강현", "handle": "@조강현_MessiOfTFT"}
    inst.extract_info.assert_called_once_with("https://www.youtube.com/@조강현_MessiOfTFT/videos", download=False)
    assert MockYDL.call_args.args[0]["playlist_items"] == "1:1"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@조강현_MessiOfTFT",
    "https://www.youtube.com/@조강현_MessiOfTFT/videos",
    f"https://www.youtube.com/channel/{CID}",
])
def test_resolve_channel_accepts_channel_urls(url):
    patcher, _, inst = _ydl([_page(1)])
    try:
        ch = resolve_channel(url)
    finally:
        patcher.stop()
    assert ch["id"] == CID
    assert inst.extract_info.call_args.args[0].endswith("/videos")


def test_resolve_channel_returns_none_when_nothing_matches():
    patcher, _, _ = _ydl([{"entries": []}, {"entries": [{"title": "no ids"}]}])
    try:
        assert resolve_channel("ㅁㄴㅇㄹ") is None
        assert resolve_channel("ㅁㄴㅇㄹ") is None
    finally:
        patcher.stop()


def test_resolve_channel_swallows_extractor_errors():
    patcher, _, _ = _ydl([RuntimeError("boom")])
    try:
        assert resolve_channel("@nope") is None
    finally:
        patcher.stop()


# --- channel_videos ---

def test_channel_videos_asks_for_the_pages_item_range_and_shapes_like_search():
    patcher, MockYDL, inst = _ydl([_page(2, start=31)])
    try:
        videos = channel_videos(CID, page=2, name="조강현")
    finally:
        patcher.stop()
    assert MockYDL.call_args.args[0]["playlist_items"] == f"{PAGE_SIZE + 1}:{2 * PAGE_SIZE}"
    inst.extract_info.assert_called_once_with(f"https://www.youtube.com/channel/{CID}/videos", download=False)
    assert videos == [
        {"title": "영상31", "channel": "조강현", "url": "https://www.youtube.com/watch?v=v0000000031", "duration": 131},
        {"title": "영상32", "channel": "조강현", "url": "https://www.youtube.com/watch?v=v0000000032", "duration": 132},
    ]


def test_channel_videos_takes_the_channel_name_from_the_page_when_not_given():
    patcher, _, _ = _ydl([_page(1)])
    try:
        assert channel_videos(CID)[0]["channel"] == "조강현"
    finally:
        patcher.stop()


# --- ChannelQueue ---

def test_queue_ensure_loads_pages_until_it_has_enough_or_the_channel_ends():
    patcher, _, inst = _ydl([_page(PAGE_SIZE), _page(PAGE_SIZE, start=PAGE_SIZE + 1), _page(3, start=2 * PAGE_SIZE + 1)])
    try:
        q = ChannelQueue({"id": CID, "name": "조강현", "handle": None})
        q.ensure(1)
        assert len(q.videos) == PAGE_SIZE and q.exhausted is False
        q.ensure(PAGE_SIZE + 1)
        assert len(q.videos) == 2 * PAGE_SIZE
        q.ensure(10 * PAGE_SIZE)                  # 세 번째 페이지가 짧다 → 채널 끝
        assert len(q.videos) == 2 * PAGE_SIZE + 3 and q.exhausted is True
        q.ensure(10 * PAGE_SIZE)                  # 더는 요청하지 않는다
    finally:
        patcher.stop()
    assert inst.extract_info.call_count == 3


def test_queue_next_after_walks_down_the_list_and_fetches_the_next_page_at_the_edge():
    patcher, _, inst = _ydl([_page(PAGE_SIZE), _page(1, start=PAGE_SIZE + 1)])
    try:
        q = ChannelQueue({"id": CID, "name": "조강현", "handle": None})
        q.ensure(1)
        assert q.next_after(q.videos[0]["url"])["title"] == "영상2"
        assert q.next_after(q.videos[PAGE_SIZE - 1]["url"])["title"] == f"영상{PAGE_SIZE + 1}"  # 페이지 끝 → 다음 페이지
        assert q.next_after(q.videos[PAGE_SIZE]["url"]) is None          # 짧은 페이지 = 채널 끝
        assert q.exhausted is True
        assert inst.extract_info.call_count == 2
    finally:
        patcher.stop()


def test_queue_next_after_an_unknown_url_is_none():
    patcher, _, _ = _ydl([_page(2)])
    try:
        q = ChannelQueue({"id": CID, "name": "조강현", "handle": None})
        q.ensure(1)
        assert q.next_after("https://www.youtube.com/watch?v=zzzzzzzzzzz") is None
    finally:
        patcher.stop()


def test_queue_page_fetch_failure_marks_the_channel_exhausted_instead_of_raising():
    patcher, _, _ = _ydl([_page(1), RuntimeError("network")])
    try:
        q = ChannelQueue({"id": CID, "name": "조강현", "handle": None})
        q.ensure(1)
        assert q.next_after(q.videos[0]["url"]) is None and q.exhausted is True
    finally:
        patcher.stop()
