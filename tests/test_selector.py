from unittest.mock import patch

import questionary

from selector import format_duration, select_video, NEXT_PAGE, PREV_PAGE, select_recent, NEW_SEARCH

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


def test_select_recent_returns_url_of_chosen():
    items = [
        {"title": "지난 영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "지난 영상 · 채널 [1:40]"
        assert select_recent(items) == "https://youtube.com/watch?v=abc"


def test_select_recent_returns_new_search_sentinel():
    items = [{"title": "지난 영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100}]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "🔍 새로 검색"
        assert select_recent(items) == NEW_SEARCH


def test_select_recent_returns_none_when_cancelled():
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        assert select_recent([]) is None


def test_select_recent_puts_new_search_first():
    items = [{"title": "지난 영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100},
             {"title": "둘째 영상", "channel": "채널", "url": "https://youtube.com/watch?v=def", "duration": 100}]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_recent(items)
    choices = mock_select.call_args[1]["choices"]
    assert choices[0] == "🔍 새로 검색"


def test_select_video_puts_channel_duration_and_age_on_a_dim_second_line():
    videos = [
        {"title": "새 영상", "channel": "채널", "url": "https://youtube.com/watch?v=a", "duration": 100, "age": "5일 전"},
        {"title": "옛 영상", "channel": "채널", "url": "https://youtube.com/watch?v=b", "duration": 100},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_video(videos)
        choices = [c for c in mock_select.call_args[1]["choices"]
                   if isinstance(c, questionary.Choice) and not isinstance(c, questionary.Separator)]

    assert choices[0].title == [("class:text", "새 영상"), ("class:choice-channel", "\n   채널 · 1:40 · 5일 전")]
    assert choices[1].title == [("class:text", "옛 영상"), ("class:choice-channel", "\n   채널 · 1:40")]
    assert choices[0].value == "새 영상 · 채널 [1:40]"


def test_select_video_puts_a_blank_line_between_videos():
    videos = [{"title": f"영상{i}", "channel": "채널", "url": f"https://youtube.com/watch?v={i}", "duration": 100} for i in range(3)]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_video(videos)
        choices = mock_select.call_args[1]["choices"]

    kinds = ["sep" if isinstance(c, questionary.Separator) else "video" for c in choices]
    assert kinds == ["video", "sep", "video", "sep", "video"]


def test_select_recent_uses_the_same_two_line_layout():
    items = [{"title": "지난 영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100},
             {"title": "둘째 영상", "channel": "채널", "url": "https://youtube.com/watch?v=def", "duration": 100}]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        select_recent(items)
        choices = mock_select.call_args[1]["choices"]

    assert choices[0] == "🔍 새로 검색"
    assert isinstance(choices[1], questionary.Separator)
    assert choices[2].title == [("class:text", "지난 영상"), ("class:choice-channel", "\n   채널 · 1:40")]
    assert isinstance(choices[3], questionary.Separator)
    assert choices[4].title[0] == ("class:text", "둘째 영상")


def test_select_video_reprompts_when_questionary_returns_an_unknown_value():
    videos = [{"title": "영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100}]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.side_effect = ["", "영상 · 채널 [1:40]"]
        result = select_video(videos)

    assert result == "https://youtube.com/watch?v=abc"
    assert mock_select.call_count == 2


def test_select_recent_reprompts_when_questionary_returns_an_unknown_value():
    items = [{"title": "지난 영상", "channel": "채널", "url": "https://youtube.com/watch?v=abc", "duration": 100}]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.side_effect = ["", "지난 영상 · 채널 [1:40]"]
        result = select_recent(items)

    assert result == "https://youtube.com/watch?v=abc"
    assert mock_select.call_count == 2
