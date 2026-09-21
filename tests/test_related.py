import json
from unittest.mock import patch, MagicMock

from related import fetch_next, extract_video_id


def _sidebar_html(entries):
    """entries: list of (video_id, title, channel, content_type)"""
    contents = []
    for vid, title, channel, ctype in entries:
        contents.append({
            "lockupViewModel": {
                "contentId": vid,
                "contentType": ctype,
                "metadata": {
                    "lockupMetadataViewModel": {
                        "title": {"content": title},
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [
                                    {"metadataParts": [{"text": {"content": channel}}]},
                                ],
                            },
                        },
                    },
                },
            },
        })
    data = {
        "contents": {
            "twoColumnWatchNextResults": {
                "secondaryResults": {
                    "secondaryResults": {
                        "results": [
                            {"itemSectionRenderer": {"contents": contents}},
                        ],
                    },
                },
            },
        },
    }
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


def _mock_urlopen(html):
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = html.encode("utf-8")
    return mock_urlopen


def test_extract_video_id_parses_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"


def test_extract_video_id_returns_none_for_invalid_url():
    assert extract_video_id("https://example.com/") is None


def test_fetch_next_returns_first_unplayed_sidebar_video():
    html = _sidebar_html([
        ("seed12345678", "시드", "채널A", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ("next12345678", "다음 편", "채널B", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", {"seed12345678"})

    assert result["title"] == "다음 편"
    assert result["channel"] == "채널B"
    assert result["url"] == "https://www.youtube.com/watch?v=next12345678"


def test_fetch_next_skips_non_video_lockup_types():
    html = _sidebar_html([
        ("playlist12345", "재생목록", "채널A", "LOCKUP_CONTENT_TYPE_PLAYLIST"),
        ("next12345678", "다음 편", "채널B", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", set())

    assert result["url"] == "https://www.youtube.com/watch?v=next12345678"


def test_fetch_next_returns_none_when_all_candidates_played():
    html = _sidebar_html([
        ("seed12345678", "시드", "채널A", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", {"seed12345678"})

    assert result is None


def test_fetch_next_returns_none_when_ytInitialData_missing():
    html = "<html><body>no data here</body></html>"
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", set())

    assert result is None


def test_fetch_next_returns_none_on_malformed_json():
    html = "<html><script>var ytInitialData = {not valid json};</script></html>"
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", set())

    assert result is None


def test_fetch_next_returns_none_on_unexpected_json_shape():
    html = "<html><script>var ytInitialData = {\"unexpected\": true};</script></html>"
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", set())

    assert result is None


def test_fetch_next_returns_none_on_network_error():
    with patch("innertube.urllib.request.urlopen", side_effect=OSError("network down")):
        result = fetch_next("https://youtube.com/watch?v=seed12345678", set())

    assert result is None


def test_fetch_next_returns_none_for_invalid_url():
    assert fetch_next("not-a-url", set()) is None


def test_fetch_next_prefers_same_channel_even_if_later_in_sidebar():
    html = _sidebar_html([
        ("vid00000001", "다른 채널 영상", "다른채널", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ("vid00000002", "3화", "본채널", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://www.youtube.com/watch?v=abcdefghijk", set(), current_channel="본채널")
    assert result["url"].endswith("vid00000002")
    assert result["channel"] == "본채널"


def test_fetch_next_same_channel_skips_played_and_falls_back_to_first_unplayed():
    html = _sidebar_html([
        ("vid00000001", "다른 채널 영상", "다른채널", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ("vid00000002", "이미 본 회차", "본채널", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://www.youtube.com/watch?v=abcdefghijk", {"vid00000002"}, current_channel="본채널")
    assert result["url"].endswith("vid00000001")


def test_fetch_next_without_channel_keeps_first_unplayed_behaviour():
    html = _sidebar_html([
        ("vid00000001", "첫 항목", "채널1", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ("vid00000002", "둘째 항목", "채널2", "LOCKUP_CONTENT_TYPE_VIDEO"),
    ])
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://www.youtube.com/watch?v=abcdefghijk", set())
    assert result["url"].endswith("vid00000001")


def _sidebar_html_with_malformed_index(entries, malformed_index):
    """_sidebar_html과 동일하게 만들되, malformed_index 항목의 lockupViewModel에서
    metadata 키를 제거해 _lockup_to_video가 그 항목에서 예외를 던지게 만든다."""
    contents = []
    for vid, title, channel, ctype in entries:
        contents.append({
            "lockupViewModel": {
                "contentId": vid,
                "contentType": ctype,
                "metadata": {
                    "lockupMetadataViewModel": {
                        "title": {"content": title},
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [
                                    {"metadataParts": [{"text": {"content": channel}}]},
                                ],
                            },
                        },
                    },
                },
            },
        })
    del contents[malformed_index]["lockupViewModel"]["metadata"]
    data = {
        "contents": {
            "twoColumnWatchNextResults": {
                "secondaryResults": {
                    "secondaryResults": {
                        "results": [
                            {"itemSectionRenderer": {"contents": contents}},
                        ],
                    },
                },
            },
        },
    }
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


def test_fetch_next_skips_malformed_lockup_and_returns_earlier_valid_entry():
    html = _sidebar_html_with_malformed_index(
        [
            ("vid00000001", "첫 항목", "채널1", "LOCKUP_CONTENT_TYPE_VIDEO"),
            ("vid00000002", "깨진 항목", "채널2", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ],
        malformed_index=1,
    )
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://www.youtube.com/watch?v=abcdefghijk", set())
    assert result["url"].endswith("vid00000001")


def test_fetch_next_returns_none_when_only_candidates_are_malformed():
    html = _sidebar_html_with_malformed_index(
        [
            ("vid00000001", "깨진 항목", "채널1", "LOCKUP_CONTENT_TYPE_VIDEO"),
        ],
        malformed_index=0,
    )
    with patch("innertube.urllib.request.urlopen", _mock_urlopen(html)):
        result = fetch_next("https://www.youtube.com/watch?v=abcdefghijk", set())
    assert result is None


def test_fetch_next_logs_swallowed_exception(caplog):
    import logging
    with patch("related.fetch_initial_data", side_effect=RuntimeError("구조 변경")), \
         caplog.at_level(logging.DEBUG, logger="yp.related"):
        assert fetch_next("https://www.youtube.com/watch?v=abcdefghijk", set(), None) is None
    assert "연관 영상 조회 실패" in caplog.text
    assert "RuntimeError: 구조 변경" in caplog.text
