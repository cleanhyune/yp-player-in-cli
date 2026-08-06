from unittest.mock import patch

import questionary

from selector import format_duration, select_video, NEXT_PAGE, PREV_PAGE

def test_format_duration_seconds():
    assert format_duration(90) == "1:30"

def test_format_duration_hours():
    assert format_duration(3661) == "1:01:01"

def test_format_duration_zero():
    assert format_duration(0) == "0:00"

def test_select_video_returns_url_of_chosen():
    videos = [
        {"title": "로파이 음악", "channel": "Lofi Girl", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
        {"title": "집중 BGM", "channel": "ChillHop", "url": "https://youtube.com/watch?v=def", "duration": 1800},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "로파이 음악 · Lofi Girl [1:00:00]"
        result = select_video(videos)

    assert result == "https://youtube.com/watch?v=abc"

def test_select_video_returns_none_when_cancelled():
    videos = [
        {"title": "로파이 음악", "channel": "Lofi Girl", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        result = select_video(videos)

    assert result is None


def test_select_video_returns_next_page_sentinel():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(20)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "다음 페이지 ▶"
        result = select_video(videos, page=1, max_pages=3)
    assert result == NEXT_PAGE


def test_select_video_returns_prev_page_sentinel():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(20)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "◀ 이전 페이지"
        result = select_video(videos, page=2, max_pages=3)
    assert result == PREV_PAGE


def test_select_video_next_page_hidden_on_last_page():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(30)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_video(videos, page=3, max_pages=3)
        call_choices = mock_select.call_args[1]["choices"]
    assert "다음 페이지 ▶" not in call_choices


def test_select_video_prev_page_hidden_on_first_page():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(30)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_video(videos, page=1, max_pages=3)
        call_choices = mock_select.call_args[1]["choices"]
    assert "◀ 이전 페이지" not in call_choices


def test_select_video_shows_only_10_items_per_page():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(30)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_video(videos, page=2, max_pages=3)
        call_choices = mock_select.call_args[1]["choices"]
    video_values = [
        c.value if isinstance(c, questionary.Choice) else c
        for c in call_choices
        if not isinstance(c, questionary.Separator)
        and c not in ("◀ 이전 페이지", "다음 페이지 ▶")
    ]
    assert len(video_values) == 10
    assert "영상10 · 채널 [1:40]" in video_values
    assert "영상19 · 채널 [1:40]" in video_values
