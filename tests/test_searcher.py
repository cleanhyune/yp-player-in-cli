from unittest.mock import patch

from searcher import search


def _video(video_id, title, channel="채널", length="3:21", age="5일 전"):
    r = {
        "videoId": video_id,
        "title": {"runs": [{"text": title}]},
        "ownerText": {"runs": [{"text": channel}]},
    }
    if length is not None:
        r["lengthText"] = {"simpleText": length}
    if age is not None:
        r["publishedTimeText"] = {"simpleText": age}
    return {"videoRenderer": r}


def _continuation(token):
    return {"continuationItemRenderer": {
        "continuationEndpoint": {"continuationCommand": {"token": token}}}}


def _first_page(items, token=None):
    sections = [{"itemSectionRenderer": {"contents": items}}]
    if token:
        sections.append(_continuation(token))
    return {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
        "sectionListRenderer": {"contents": sections}}}}}


def _next_page(items, token=None):
    sections = [{"itemSectionRenderer": {"contents": items}}]
    if token:
        sections.append(_continuation(token))
    return {"onResponseReceivedCommands": [
        {"appendContinuationItemsAction": {"continuationItems": sections}}]}


def test_search_shapes_video_renderers_like_before_plus_age():
    page = _first_page([
        _video("abc", "로파이 음악 1시간", "Lofi Girl", "1:00:00", "1년 전"),
        _video("def", "집중 BGM", "ChillHop", "30:00", "3주 전"),
    ])
    with patch("searcher.innertube.search", return_value=page):
        results = search("로파이")

    assert results == [
        {"title": "로파이 음악 1시간", "channel": "Lofi Girl",
         "url": "https://www.youtube.com/watch?v=abc", "duration": 3600, "age": "1년 전"},
        {"title": "집중 BGM", "channel": "ChillHop",
         "url": "https://www.youtube.com/watch?v=def", "duration": 1800, "age": "3주 전"},
    ]


def test_search_degrades_missing_fields_per_field():
    page = _first_page([_video("abc", "라이브 방송", length=None, age=None)])
    with patch("searcher.innertube.search", return_value=page):
        [result] = search("라이브")

    assert result["duration"] == 0
    assert result["age"] is None


def test_search_skips_shorts_and_shelves_and_items_without_an_id():
    page = _first_page([
        {"shortsLockupViewModel": {"entityId": "x"}},
        {"lockupViewModel": {"contentId": "PL123"}},
        {"videoRenderer": {"title": {"runs": [{"text": "id 없음"}]}}},
        _video("abc", "정상 영상"),
    ])
    with patch("searcher.innertube.search", return_value=page):
        results = search("테스트")

    assert [r["title"] for r in results] == ["정상 영상"]


def test_search_follows_the_continuation_until_max_results_and_cuts_there():
    first = _first_page([_video(f"a{i}", f"영상{i}") for i in range(20)], token="T1")
    second = _next_page([_video(f"b{i}", f"영상{20 + i}") for i in range(18)], token="T2")
    calls = []

    def fake(query=None, continuation=None):
        calls.append((query, continuation))
        return first if continuation is None else second

    with patch("searcher.innertube.search", fake):
        results = search("테스트", max_results=30)

    assert len(results) == 30
    assert results[-1]["title"] == "영상29"
    assert calls == [("테스트", None), (None, "T1")]


def test_search_stops_when_there_is_no_continuation():
    with patch("searcher.innertube.search", return_value=_first_page([_video("abc", "하나")])) as fake:
        results = search("테스트", max_results=30)

    assert len(results) == 1
    assert fake.call_count == 1


def test_search_returns_empty_list_when_nothing_matches():
    with patch("searcher.innertube.search", return_value=_first_page([])):
        assert search("존재하지않는검색어xyz") == []
