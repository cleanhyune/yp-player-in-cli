from unittest.mock import patch
from searcher import search

def test_search_returns_list_of_dicts():
    mock_info = {
        "entries": [
            {"title": "로파이 음악 1시간", "channel": "Lofi Girl", "webpage_url": "https://youtube.com/watch?v=abc", "duration": 3600},
            {"title": "집중 BGM", "channel": "ChillHop", "webpage_url": "https://youtube.com/watch?v=def", "duration": 1800},
        ]
    }
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("로파이")

    assert len(results) == 2
    assert results[0]["title"] == "로파이 음악 1시간"
    assert results[0]["channel"] == "Lofi Girl"
    assert results[0]["url"] == "https://youtube.com/watch?v=abc"
    assert results[0]["duration"] == 3600

def test_search_falls_back_to_uploader_for_channel():
    mock_info = {
        "entries": [
            {"title": "테스트", "uploader": "업로더명", "webpage_url": "https://youtube.com/watch?v=abc", "duration": 100},
        ]
    }
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("테스트")

    assert results[0]["channel"] == "업로더명"

def test_search_returns_empty_list_when_no_entries():
    mock_info = {"entries": []}
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("존재하지않는검색어xyz")

    assert results == []

def test_search_skips_entries_missing_url():
    mock_info = {
        "entries": [
            {"title": "정상 영상", "channel": "채널", "webpage_url": "https://youtube.com/watch?v=abc", "duration": 100},
            {"title": "URL 없는 영상", "channel": "채널", "duration": 100},
        ]
    }
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("테스트")

    assert len(results) == 1
    assert results[0]["title"] == "정상 영상"

def test_search_default_max_results_is_30():
    mock_info = {"entries": []}
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        search("테스트")
        instance.extract_info.assert_called_once_with("ytsearch30:테스트", download=False)
