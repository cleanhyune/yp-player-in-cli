import re

import pytest

from tui import (LinePrompt, Pager, display_width, parse_keys,
                 parse_timecode, render_playing, truncate)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """스타일을 벗겨 순수 텍스트만 남긴다. 폭 검증은 항상 이걸 거친다."""
    return _ANSI.sub("", text)


# --- parse_keys ---

def test_parse_keys_arrow_sequences():
    assert parse_keys(b"\x1b[A") == ["UP"]
    assert parse_keys(b"\x1b[B") == ["DOWN"]
    assert parse_keys(b"\x1b[C") == ["RIGHT"]
    assert parse_keys(b"\x1b[D") == ["LEFT"]

def test_parse_keys_page_and_home_end():
    assert parse_keys(b"\x1b[5~") == ["PGUP"]
    assert parse_keys(b"\x1b[6~") == ["PGDWN"]
    assert parse_keys(b"\x1b[H") == ["HOME"]
    assert parse_keys(b"\x1b[F") == ["END"]

def test_parse_keys_lone_escape():
    assert parse_keys(b"\x1b") == ["ESC"]

def test_parse_keys_special_single_bytes():
    assert parse_keys(b" ") == ["SPACE"]
    assert parse_keys(b"\r") == ["ENTER"]
    assert parse_keys(b"\n") == ["ENTER"]
    assert parse_keys(b"\x7f") == ["BS"]
    assert parse_keys(b"\t") == ["TAB"]

def test_parse_keys_plain_and_multibyte_characters():
    assert parse_keys(b"ab9") == ["a", "b", "9"]
    assert parse_keys("ㄱ".encode("utf-8")) == ["ㄱ"]

def test_parse_keys_mixed_buffer():
    assert parse_keys(b"q\x1b[Cn") == ["q", "RIGHT", "n"]

def test_parse_keys_unknown_csi_sequence_is_dropped():
    assert parse_keys(b"\x1b[3~") == []

def test_parse_keys_unknown_csi_sequence_with_params_is_dropped():
    assert parse_keys(b"\x1b[1;2A") == []

def test_parse_keys_unknown_sequence_does_not_leak_into_surrounding_keys():
    assert parse_keys(b"q\x1b[3~n") == ["q", "n"]


# --- width / truncate ---

def test_display_width_counts_east_asian_wide_as_two():
    assert display_width("abc") == 3
    assert display_width("한글") == 4

def test_truncate_keeps_text_within_width_and_adds_ellipsis():
    assert truncate("abcdef", 10) == "abcdef"
    out = truncate("가나다라마바사", 8)
    assert out.endswith("…")
    assert display_width(out) <= 8


# --- Pager ---

def _blocks(n, size=2):
    """n개 블록, 각 size줄 ("i-0", "i-1", ...)."""
    return [[f" {i}-{j}" for j in range(size)] for i in range(n)]


def _pager(n=10, size=2, header=None, **kw):
    p = Pager(header, **kw)
    p.add_items(_blocks(n, size), [{"n": i} for i in range(n)])
    return p


def test_pager_lines_are_header_plus_blocks_with_anchors():
    p = _pager(3, header=["제목", ""])
    assert p.lines[:2] == ["제목", ""]
    assert p.anchors == [2, 4, 6]
    assert p.selected_payload() == {"n": 0}
    assert p.selected_range() == (2, 4)


def test_pager_cursor_moves_by_block_and_keeps_the_block_visible():
    p = _pager(10)                       # 20줄, 높이 5
    assert p.handle_key("j", 5) is None
    assert (p.cursor, p.top) == (1, 0)   # 블록 1 = 줄 2-3, 아직 보인다
    p.handle_key("j", 5)                 # 블록 2 = 줄 4-5, 끝 6 > 5 → top = 1
    assert (p.cursor, p.top) == (2, 1)
    p.handle_key("k", 5); p.handle_key("k", 5); p.handle_key("k", 5)
    assert (p.cursor, p.top) == (0, 0)


def test_pager_cursor_stops_at_the_ends():
    p = _pager(2)
    p.handle_key("k", 5)
    assert p.cursor == 0
    p.handle_key("j", 5); p.handle_key("j", 5)
    assert p.cursor == 1 and p.at_last_item()


def test_pager_page_keys_scroll_and_drag_the_cursor_into_view():
    p = _pager(10)
    p.handle_key("SPACE", 5)
    assert p.top == 5
    assert p.cursor == 3                 # 첫 줄이 화면(5..9)에 보이는 첫 블록 = 블록 3(줄 6)
    p.handle_key("PGUP", 5)
    assert (p.top, p.cursor) == (0, 0)


def test_pager_home_and_end():
    p = _pager(10)
    p.handle_key("END", 5)
    assert p.cursor == 9 and p.top == 15
    p.handle_key("HOME", 5)
    assert (p.cursor, p.top) == (0, 0)
    p.handle_key("G", 5); p.handle_key("g", 5)
    assert (p.cursor, p.top) == (0, 0)


def test_pager_block_taller_than_the_screen_aligns_its_first_line_to_the_top():
    p = _pager(3, size=8)
    p.handle_key("j", 5)
    assert p.top == 8


def test_pager_returns_close_and_open_actions():
    for key in ("q", "ESC", "LEFT"):
        assert _pager(1).handle_key(key, 5) == "close"
    assert _pager(1).handle_key("ENTER", 5) == "open"
    assert _pager(1).handle_key("x", 5) is None


def test_pager_with_no_blocks_ignores_movement():
    p = Pager(["제목"])
    for key in ("j", "k", "SPACE", "END", "G"):
        assert p.handle_key(key, 5) is None
    assert p.cursor == 0 and p.top == 0
    assert p.selected_payload() is None and p.selected_range() is None
    assert p.at_last_item() is False


def test_pager_add_items_keeps_the_view_and_moves_the_last_item_away():
    p = _pager(5)
    p.handle_key("END", 5)
    assert p.at_last_item()
    p.add_items(_blocks(2), [{"n": 5}, {"n": 6}])
    assert p.top == 5 and p.cursor == 4
    assert p.at_last_item() is False
    assert len(p.anchors) == 7


def test_pager_replace_items_resets_cursor_and_scroll():
    p = _pager(10)
    p.handle_key("END", 5)
    p.replace_items(["새 제목", ""], _blocks(2), [{"n": 0}, {"n": 1}])
    assert p.lines[0] == "새 제목" and p.anchors == [2, 4]
    assert (p.cursor, p.top) == (0, 0)


def test_pager_starts_with_no_more_pages_and_no_status():
    p = Pager(["a"])
    assert p.more is False and p.status is None
    assert Pager(["a"], more=True).more is True


# --- LinePrompt ---

def test_line_prompt_collects_text_and_submits():
    lp = LinePrompt("시간: ")
    for k in ["0", "7", "1", "0"]:
        assert lp.handle_key(k) == "pending"
    assert lp.render() == "시간: 0710"
    assert lp.handle_key("ENTER") == "submit"
    assert lp.text == "0710"

def test_line_prompt_backspace_and_cancel():
    lp = LinePrompt("> ")
    lp.handle_key("1"); lp.handle_key("2"); lp.handle_key("BS")
    assert lp.text == "1"
    assert lp.handle_key("ESC") == "cancel"

def test_line_prompt_ignores_named_keys():
    lp = LinePrompt("> ")
    lp.handle_key("LEFT"); lp.handle_key("SPACE")
    assert lp.text == ""


# --- parse_timecode (Lua 판 이식) ---

@pytest.mark.parametrize("text,expected", [
    ("0710", 430), ("7:10", 430), ("012930", 5370), ("1:29:30", 5370),
    ("12930", 5370), (" 0710 ", 430),
    ("9999", None), ("12", None), ("abc", None), ("1234567", None), ("", None),
    ("12³4", None),
])
def test_parse_timecode(text, expected):
    assert parse_timecode(text) == expected


# --- terminal layer ---

import io
import os
import queue
import time
from unittest.mock import patch

from tui import (KeyReader, _ends_with_partial_escape,
                 enter_alt_screen, exit_alt_screen, terminal_mode)


def test_terminal_mode_enters_cbreak_and_restores_even_on_error():
    saved = ["saved-attrs"]
    with patch("tui.termios.tcgetattr", return_value=saved) as getattr_, \
         patch("tui.termios.tcsetattr") as setattr_, \
         patch("tui.tty.setcbreak") as cbreak:
        with pytest.raises(RuntimeError):
            with terminal_mode(fd=7):
                cbreak.assert_called_once_with(7)
                raise RuntimeError("boom")
    getattr_.assert_called_once_with(7)
    setattr_.assert_called_once()
    assert setattr_.call_args[0][0] == 7
    assert setattr_.call_args[0][2] == saved


def test_key_reader_pushes_parsed_keys_to_queue():
    r, w = os.pipe()
    events = queue.Queue()
    reader = KeyReader(r, events, poll_interval=0.05)
    reader.start()
    try:
        os.write(w, b"n\x1b[A")
        got = [events.get(timeout=1), events.get(timeout=1)]
        assert got == [{"event": "key", "key": "n"}, {"event": "key", "key": "UP"}]
    finally:
        reader.stop()
        os.close(w)
        os.close(r)


def test_key_reader_stops_when_asked():
    r, w = os.pipe()
    reader = KeyReader(r, queue.Queue(), poll_interval=0.05)
    reader.start()
    reader.stop()
    assert not reader._thread.is_alive()
    os.close(w)
    os.close(r)


def test_key_reader_stop_is_synchronous_and_ignores_bytes_written_after():
    r, w = os.pipe()
    events = queue.Queue()
    reader = KeyReader(r, events, poll_interval=0.05)
    reader.start()
    reader.stop()
    os.write(w, b"x")
    time.sleep(0.3)
    assert events.empty()
    os.close(w)
    os.close(r)


def test_key_reader_emits_lone_esc():
    r, w = os.pipe()
    events = queue.Queue()
    reader = KeyReader(r, events, poll_interval=0.05)
    reader.start()
    try:
        os.write(w, b"\x1b")
        got = events.get(timeout=1)
        assert got == {"event": "key", "key": "ESC"}
    finally:
        reader.stop()
        os.close(w)
        os.close(r)


def test_alt_screen_sequences():
    out = io.StringIO()
    enter_alt_screen(out)
    exit_alt_screen(out)
    assert out.getvalue() == "\x1b[?1049h\x1b[H\x1b[?1049l"


# --- _ends_with_partial_escape ---

@pytest.mark.parametrize("buf,expected", [
    (b"\x1b", True),
    (b"\x1b[", True),
    (b"\x1b[1;2", True),
    (b"\x1bO", True),
    (b"\x1b[A", False),
    (b"abc", False),
    (b"", False),
])
def test_ends_with_partial_escape(buf, expected):
    assert _ends_with_partial_escape(buf) == expected


def test_key_reader_reassembles_escape_split_across_reads():
    r, w = os.pipe()
    events = queue.Queue()
    reader = KeyReader(r, events, poll_interval=0.05)
    reader.start()
    try:
        os.write(w, b"\x1b")
        time.sleep(0.02)
        os.write(w, b"[A")
        got = events.get(timeout=1)
        assert got == {"event": "key", "key": "UP"}
    finally:
        reader.stop()
        os.close(w)
        os.close(r)


# --- 스타일 헬퍼 ---

def test_bold_and_dim_wrap_with_reset(monkeypatch):
    # 실행 환경에 NO_COLOR가 설정돼 있으면(값과 무관하게) 스타일이 꺼져 이 단언이
    # 깨진다 — 이 테스트는 스타일이 켜진 경우를 검증하는 것이므로 격리한다.
    monkeypatch.delenv("NO_COLOR", raising=False)
    from tui import bold, dim
    assert bold("가") == "\x1b[1m가\x1b[0m"
    assert dim("가") == "\x1b[2m가\x1b[0m"


def test_no_color_env_disables_styles(monkeypatch):
    from tui import bold, dim
    monkeypatch.setenv("NO_COLOR", "1")
    assert bold("가") == "가"
    assert dim("가") == "가"


def test_no_color_empty_string_does_not_disable_styles(monkeypatch):
    # no-color.org 규약: NO_COLOR는 non-empty 값일 때만 색을 끈다. 빈 문자열은
    # "설정 안 됨"과 동일하게 취급해야 한다.
    from tui import bold, dim
    monkeypatch.setenv("NO_COLOR", "")
    assert bold("가") == "\x1b[1m가\x1b[0m"
    assert dim("가") == "\x1b[2m가\x1b[0m"


def test_fit_pads_to_width_and_truncates_cjk_correctly():
    from tui import _fit
    assert _fit("abc", 6) == "abc   "
    assert display_width(_fit("가나다라마바사", 8)) == 8
    assert display_width(_fit("", 5)) == 5


def test_fit_with_zero_width_is_empty():
    from tui import _fit
    assert _fit("abc", 0) == ""


def test_fit_truncates_cjk_into_a_single_column():
    from tui import _fit
    # width=1이면 truncate()가 "…"(폭 1)만 남긴다 — 잘린 결과도 정확히 폭 1이어야 한다.
    assert display_width(_fit("가나", 1)) == 1


# --- progress_bar ---

def test_progress_bar_characters_share_an_east_asian_width_class():
    """등급이 섞이면 ambiguous=2 터미널에서 재생 중 막대 폭이 자란다."""
    import unicodedata
    from tui import _BAR_EMPTY, _BAR_FULL
    assert unicodedata.east_asian_width(_BAR_FULL) == unicodedata.east_asian_width(_BAR_EMPTY)


def test_progress_bar_width_is_exact_regardless_of_ratio():
    from tui import progress_bar
    for pos in (0, 1, 50, 99, 100, 500):
        assert display_width(plain(progress_bar(pos, 100, 30))) == 30


def test_progress_bar_fills_proportionally():
    from tui import progress_bar
    assert plain(progress_bar(50, 100, 10)) == "█████▒▒▒▒▒"
    assert plain(progress_bar(100, 100, 10)) == "██████████"


def test_progress_bar_dims_only_the_remaining_portion(monkeypatch):
    # NO_COLOR가 설정된 환경에서는 dim()이 아무것도 감싸지 않으므로 격리한다.
    monkeypatch.delenv("NO_COLOR", raising=False)
    from tui import progress_bar
    bar = progress_bar(50, 100, 10)
    assert "\x1b[2m" in bar          # 남은 칸에 dim이 실제로 걸린다
    assert not bar.startswith("\x1b[2m")  # 채운 칸(맨 앞)은 dim이 아니다


def test_progress_bar_with_unknown_duration_is_all_empty():
    from tui import progress_bar
    assert plain(progress_bar(30, 0, 8)) == "▒▒▒▒▒▒▒▒"


def test_progress_bar_clamps_position_past_the_end():
    from tui import progress_bar
    assert plain(progress_bar(500, 100, 10)) == "██████████"


def test_progress_bar_with_zero_width_is_empty():
    from tui import progress_bar
    assert progress_bar(10, 100, 0) == ""


# --- render_playing ---

def _pstate(**over):
    base = {"title": "침착맨 삼국지 완전판", "channel": "침착맨",
            "position": 62.0, "duration": 18378.0, "track_duration": 18379.0,
            "paused": False, "volume": 60, "autoplay": True,
            "next_hint": None, "notice": None, "prompt": None}
    base.update(over)
    return base


def test_render_playing_returns_exactly_rows_lines():
    from tui import render_playing
    for rows in (0, 1, 2, 5, 10, 14, 15, 24, 60):
        assert len(render_playing(_pstate(), 80, rows)) == rows


def test_render_playing_never_exceeds_cols():
    from tui import render_playing
    state = _pstate(title="가나다라마바사아자차카타파하" * 6,
                    channel="아주아주아주 긴 채널 이름입니다" * 3,
                    next_hint="진격의 거인 감상회 완전판 · 침착맨" * 4,
                    notice="스트림 연결 중... " * 8)
    for cols in (40, 43, 44, 60, 80, 120, 200):
        for line in render_playing(state, cols, 24):
            assert display_width(plain(line)) <= cols


def test_render_playing_box_lines_all_share_one_width():
    """margin/inner/w 산술이 틀어지면 오른쪽 테두리가 어긋난다. 폭 집합의 크기가 1이어야 한다."""
    from tui import render_playing
    for cols in (44, 45, 60, 80, 120, 200):
        widths = {display_width(plain(line))
                  for line in render_playing(_pstate(), cols, 24)
                  if plain(line).strip().startswith(("┌", "│", "└"))}
        assert len(widths) == 1, f"cols={cols}: {widths}"


def test_render_playing_shows_title_and_channel():
    from tui import render_playing
    text = "\n".join(plain(l) for l in render_playing(_pstate(), 80, 24))
    assert "침착맨 삼국지 완전판" in text
    assert "1:02" in text and "5:06:18" in text
    assert "vol 60" in text


def test_render_playing_notice_does_not_shift_other_lines():
    from tui import render_playing
    without = [plain(l) for l in render_playing(_pstate(), 80, 24)]
    with_ = [plain(l) for l in render_playing(_pstate(notice="스트림 연결 중..."), 80, 24)]
    assert len(without) == 24 and len(with_) == 24
    differing = [i for i, (a, b) in enumerate(zip(without, with_)) if a != b]
    assert len(differing) == 1, "안내 슬롯만 달라져야 한다"
    assert "스트림 연결 중..." in with_[differing[0]]


def test_render_playing_shows_next_hint_only_when_present():
    from tui import render_playing
    text = "\n".join(plain(l) for l in render_playing(_pstate(next_hint="2화 · 채널"), 80, 24))
    assert "↳ 2화 · 채널" in text
    text = "\n".join(plain(l) for l in render_playing(_pstate(), 80, 24))
    assert "↳" not in text


def test_render_playing_last_row_is_key_hints():
    from tui import render_playing
    assert "q 종료" in plain(render_playing(_pstate(), 80, 24)[-1])


def test_render_playing_prompt_replaces_the_key_hints_and_shows_a_cursor():
    from tui import render_playing
    last = plain(render_playing(_pstate(prompt="이동할 시간: 0710"), 80, 24)[-1])
    assert "이동할 시간: 0710_" in last
    assert "q 종료" not in last


def test_render_playing_paused_icon():
    from tui import render_playing
    # "▶"(A) 와 짝을 맞추기 위해 "‖"(A)를 쓴다. East Asian Width 등급이 다른 "⏸"(N)를
    # 쓰면 ambiguous를 2칸으로 렌더하는 터미널에서 일시정지를 토글할 때마다 시간 줄의
    # 폭이 흔들린다.
    assert "‖" in "\n".join(plain(l) for l in render_playing(_pstate(paused=True), 80, 24))


def test_render_playing_shows_autoplay_on_or_off():
    from tui import render_playing
    on_text = "\n".join(plain(l) for l in render_playing(_pstate(autoplay=True), 80, 24))
    off_text = "\n".join(plain(l) for l in render_playing(_pstate(autoplay=False), 80, 24))
    assert "ON" in on_text and "OFF" not in on_text
    assert "OFF" in off_text and "ON" not in off_text


def test_render_playing_unknown_duration_shows_placeholder():
    from tui import render_playing
    text = "\n".join(plain(l)
                     for l in render_playing(_pstate(duration=0, track_duration=0), 80, 24))
    assert "--:--" in text


def test_render_playing_falls_back_to_track_duration_before_mpv_reports_one():
    from tui import render_playing
    text = "\n".join(plain(l) for l in render_playing(_pstate(duration=0), 80, 24))
    assert "5:06:19" in text and "--:--" not in text


def test_render_playing_uses_the_box_on_a_tall_terminal():
    from tui import render_playing
    text = "\n".join(plain(l) for l in render_playing(_pstate(), 80, 24))
    assert "┌" in text and "└" in text


def test_render_playing_drops_the_box_on_a_short_terminal():
    from tui import render_playing
    text = "\n".join(plain(l) for l in render_playing(_pstate(), 80, 12))
    assert "┌" not in text
    assert "침착맨 삼국지 완전판" in text
    assert "q 종료" in text


def test_render_playing_survives_missing_state_keys():
    from tui import render_playing
    assert len(render_playing({}, 80, 24)) == 24


# --- render_comments ---

def test_comments_height_reserves_header_separator_and_footer():
    from tui import comments_height
    assert comments_height(24) == 20
    assert comments_height(5) == 1
    assert comments_height(2) == 1   # 최소 1줄은 보장


def test_render_comments_returns_exactly_rows_lines():
    from tui import render_comments
    pager = _pager(25)
    for rows in (6, 10, 24, 40):
        assert len(render_comments(_pstate(), pager, 80, rows)) == rows


def test_render_comments_keeps_the_track_header_and_progress():
    from tui import Pager, render_comments
    pager = Pager(["댓글1", "댓글2"])
    text = "\n".join(plain(l) for l in render_comments(_pstate(), pager, 80, 24))
    assert "침착맨 삼국지 완전판" in text
    assert "1:02" in text and "5:06:18" in text


def test_render_comments_shows_the_visible_slice_and_footer_counts():
    from tui import render_comments
    pager = _pager(50, more=True)
    lines = [plain(l) for l in render_comments(_pstate(), pager, 80, 24)]
    assert lines[3].startswith("▶0-0")
    assert lines[4].startswith(" 0-1")
    assert "1/50+" in lines[-1]
    assert "q 닫기" in lines[-1]


def test_render_comments_bolds_the_selected_block_and_marks_its_first_line():
    from tui import render_comments
    p = _pager(3, header=["제목", ""])
    p.handle_key("j", 20)
    body = render_comments(_pstate(), p, 80, 24)[3:-1]
    assert plain(body[0]).startswith("제목")
    assert body[0] == plain(body[0])                # 선택 안 된 줄은 평문
    assert plain(body[2]).startswith(" 0-0")        # 블록 0: 선택 해제, 마커 없음
    assert plain(body[4]).startswith("▶1-0")        # 블록 1의 첫 줄
    assert body[4] != plain(body[4])                # bold가 입혀졌다
    assert body[5] != plain(body[5])                # 블록의 나머지 줄도 bold


def test_render_comments_footer_counts_blocks_not_lines():
    from tui import Pager, render_comments
    p = _pager(7, more=True)
    p.handle_key("j", 20)
    assert "2/7+" in plain(render_comments(_pstate(), p, 80, 24)[-1])
    assert "0/0" in plain(render_comments(_pstate(), Pager(["x"]), 80, 24)[-1])


def test_render_comments_footer_prefers_the_pager_status():
    from tui import render_comments
    pager = _pager(1, more=True)
    pager.status = "다음 댓글 불러오는 중..."
    assert "다음 댓글 불러오는 중..." in plain(render_comments(_pstate(), pager, 80, 24)[-1])


def test_render_comments_footer_uses_a_custom_hint():
    from tui import render_comments
    pager = _pager(1, hint="s 정렬 · q 닫기")
    assert "s 정렬 · q 닫기" in plain(render_comments(_pstate(), pager, 80, 24)[-1])


def test_render_comments_never_exceeds_cols():
    from tui import Pager, render_comments
    pager = Pager()
    pager.add_items([["가나다라마바사" * 30], ["짧은 댓글"]], [None, None])
    for cols in (1, 2, 3, 4, 5, 40, 80, 200):
        for line in render_comments(_pstate(title="긴 제목 " * 20), pager, cols, 24):
            assert display_width(plain(line)) <= cols


def test_render_comments_pads_when_there_are_fewer_comments_than_the_body():
    from tui import render_comments
    assert len(render_comments(_pstate(), _pager(1), 80, 24)) == 24


def test_comments_height_matches_the_renderers_body_line_count():
    """comments_height()가 렌더러가 실제로 그리는 본문 줄 수와 어긋나면, Pager._ensure_visible이
    맞춰 둔 커서가 화면 밖으로 밀린다 — 이 태스크에서 가장 값비싼 회귀다."""
    from tui import render_comments, comments_height
    pager = _pager(50)
    for rows in (5, 6, 24):
        height = comments_height(rows)
        lines = [plain(l) for l in render_comments(_pstate(), pager, 80, rows)]
        # 헤더 2줄 + 구분선 1줄을 건너뛰고, 마지막 푸터 1줄 앞까지가 본문이다.
        body = lines[3:-1]
        assert len(body) == height
        # 본문 영역이 실제로 pager가 보여주는 슬라이스와 같아야 한다 (▶ 마커만 제외).
        assert [l.rstrip().replace("▶", " ", 1) for l in body] == pager.visible(height)


# --- Screen ---

def test_screen_first_draw_clears_then_writes_each_line_cleared():
    """첫 draw는 크기를 모르던 상태에서 오므로 화면 지우기가 앞에 붙는다."""
    from tui import Screen
    out = io.StringIO()
    Screen(out).draw(["가", "나"], 10, 2)
    assert out.getvalue() == "\x1b[2J\x1b[H가\x1b[K\r\n나\x1b[K"


def test_screen_second_draw_has_no_clear_when_the_size_is_unchanged():
    from tui import Screen
    out = io.StringIO()
    screen = Screen(out)
    screen.draw(["가"], 10, 1)
    out.truncate(0), out.seek(0)
    screen.draw(["나"], 10, 1)
    assert out.getvalue() == "\x1b[H나\x1b[K"


def test_screen_skips_the_write_when_the_frame_is_unchanged():
    from tui import Screen
    out = io.StringIO()
    screen = Screen(out)
    screen.draw(["가"], 10, 1)
    before = out.getvalue()
    screen.draw(["가"], 10, 1)
    assert out.getvalue() == before


def test_screen_clears_everything_when_the_terminal_was_resized():
    from tui import Screen
    out = io.StringIO()
    screen = Screen(out)
    screen.draw(["가"], 10, 1)
    out.truncate(0), out.seek(0)
    screen.draw(["가"], 20, 1)
    assert out.getvalue().startswith("\x1b[2J")


def test_screen_reset_forces_the_next_draw_to_write():
    from tui import Screen
    out = io.StringIO()
    screen = Screen(out)
    screen.draw(["가"], 10, 1)
    screen.reset()
    out.truncate(0), out.seek(0)
    screen.draw(["가"], 10, 1)
    assert out.getvalue() != ""


def test_cursor_sequences():
    from tui import hide_cursor, show_cursor
    out = io.StringIO()
    hide_cursor(out)
    show_cursor(out)
    assert out.getvalue() == "\x1b[?25l\x1b[?25h"


def test_screen_draw_of_a_real_rendered_frame_clears_every_line_to_eol():
    """render_playing()의 박스 카드는 왼쪽만 여백을 넣고 오른쪽은 채우지 않는다 (가운데 정렬).
    Screen이 줄마다 \\x1b[K를 붙이지 않으면, 이전 프레임이 더 넓었을 때 그 잔상이 오른쪽에
    남는다 — 여기서는 각 줄의 실제 길이가 cols(200)보다 훨씬 짧은데도 잔상이 안 남는지를
    \\x1b[K 개수로 확인한다."""
    from tui import Screen, render_playing
    out = io.StringIO()
    lines = render_playing(_pstate(), 200, 24)
    Screen(out).draw(lines, 200, 24)
    assert out.getvalue().count("\x1b[K") == len(lines)


def test_the_key_hint_line_lists_prev_next_in_playing_order():
    lines = render_playing({"position": 10.0, "duration": 100.0}, 100, 24)
    tail = plain(lines[-1])
    assert "p 이전" in tail and "n 다음" in tail
    assert tail.index("p 이전") < tail.index("n 다음")
