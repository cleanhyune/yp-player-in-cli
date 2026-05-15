from unittest.mock import patch
from selector import format_duration, select_video

def test_format_duration_seconds():
    assert format_duration(90) == "1:30"

def test_format_duration_hours():
    assert format_duration(3661) == "1:01:01"

def test_format_duration_zero():
    assert format_duration(0) == "0:00"

def test_select_video_returns_url_of_chosen():
    videos = [
        {"title": "로파이 음악", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
        {"title": "집중 BGM", "url": "https://youtube.com/watch?v=def", "duration": 1800},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "로파이 음악 [1:00:00]"
        result = select_video(videos)

    assert result == "https://youtube.com/watch?v=abc"

def test_select_video_returns_none_when_cancelled():
    videos = [
        {"title": "로파이 음악", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        result = select_video(videos)

    assert result is None
