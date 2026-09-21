"""_play_session의 정리 범위와 duration 기록."""
from unittest.mock import MagicMock, patch

import pytest

import yp
from player import PlayerError

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIDEO_ID = "abcdefghijk"


class FakeSession:
    def __init__(self, start_exc=None, reason="quit", duration=0.0, position=0.0):
        self.start_exc = start_exc
        self.reason = reason
        self.duration = duration
        self.position = position
        self.volume = 100
        self.autoplay = True
        self.strategy = None
        self.cookies = True
        self.closed = False
        self.started = False
        self.quit_called = False
        self.errors: list[str] = []
        self.tracks: list[tuple] = []
        self.notices: list[str | None] = []

    def start(self):
        if self.start_exc is not None:
            raise self.start_exc
        self.started = True

    def load(self, url, start=None, stream=None):
        return self.reason

    def on_position(self, callback):
        pass

    def set_next_hint(self, text):
        pass

    def set_track(self, title, channel=None, duration=None):
        self.tracks.append((title, channel, duration))

    def set_notice(self, text):
        self.notices.append(text)

    def quit(self):
        self.quit_called = True


TODAY = "2026-09-09"


@pytest.fixture(autouse=True)
def frozen_today():
    with patch("yp._today", return_value=TODAY):
        yield


def _video():
    return {"title": "영상", "channel": "채널", "url": URL, "duration": 0}


def _fake_history():
    h = MagicMock()
    h.load_state.return_value = {"volume": 100, "autoplay": True,
                                 "strategy": None, "probed": None, "cookies": True}
    h.resume_position.return_value = None
    return h


def _run(session, history):
    with patch("yp.PlayerSession", return_value=session), \
         patch("yp.history", history), \
         patch("yp._Prefetch") as prefetch, \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        prefetch.return_value.done = True
        prefetch.return_value.result.return_value = None
        yp._play_session(_video())
    return prefetch


# --- start()가 정리 범위 안에 있는가 (Important 2) ---

def test_keyboard_interrupt_during_start_still_quits_the_session(capsys):
    session = FakeSession(start_exc=KeyboardInterrupt())
    _run(session, _fake_history())
    assert session.quit_called is True   # 고아 mpv / 복원 안 된 cbreak 방지
    assert "재생을 중단합니다" in capsys.readouterr().out


def test_player_error_during_start_reports_and_still_quits(capsys):
    session = FakeSession(start_exc=PlayerError("소켓 없음"))
    history = _fake_history()
    _run(session, history)
    assert session.quit_called is True
    out = capsys.readouterr().out
    assert "mpv를 시작할 수 없습니다: 소켓 없음" in out
    history.record_start.assert_not_called()   # 재생 루프에는 진입하지 않는다


def test_state_is_saved_and_session_quit_on_normal_end():
    session = FakeSession(reason="quit")
    history = _fake_history()
    _run(session, history)
    assert session.started is True and session.quit_called is True
    history.save_state.assert_called_once_with(100, True, None, TODAY, True)


def _run_with_state(state, session):
    history = _fake_history()
    history.load_state.return_value = state
    with patch("yp.PlayerSession", return_value=session) as ctor, \
         patch("yp.history", history), \
         patch("yp._Prefetch") as prefetch, \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        prefetch.return_value.done = True
        prefetch.return_value.result.return_value = None
        yp._play_session(_video())
    return ctor, history


def test_remembered_strategy_is_reused_within_the_same_day():
    """차단이 걸린 상태에서 곡을 고를 때마다 익명 시도를 다시 낭비하지 않기 위한 연결."""
    session = FakeSession(reason="quit")
    session.strategy = "web_safari"
    ctor, history = _run_with_state(
        {"volume": 100, "autoplay": True, "strategy": "web_embedded", "probed": TODAY, "cookies": True},
        session)
    assert ctor.call_args.kwargs["strategy"] == "web_embedded"
    history.save_state.assert_called_once_with(100, True, "web_safari", TODAY, True)


def test_stale_probe_date_discards_the_remembered_strategy():
    """하루가 지나면 기본 순서로 다시 탐색해, 차단이 풀렸을 때 빠른 경로로 되돌아간다."""
    session = FakeSession(reason="quit")
    session.strategy = "android"
    ctor, history = _run_with_state(
        {"volume": 100, "autoplay": True, "strategy": "web_safari", "probed": "2026-09-08", "cookies": True},
        session)
    assert ctor.call_args.kwargs["strategy"] is None
    history.save_state.assert_called_once_with(100, True, "android", TODAY, True)


def test_state_from_an_older_yp_probes_because_it_has_no_date():
    session = FakeSession(reason="quit")
    ctor, _ = _run_with_state(
        {"volume": 100, "autoplay": True, "strategy": None, "probed": None, "cookies": True},
        session)
    assert ctor.call_args.kwargs["strategy"] is None


# --- Chrome 쿠키 opt-in ---

def _state(cookies):
    return {"volume": 100, "autoplay": True, "strategy": None, "probed": None, "cookies": cookies}


def test_cookie_choice_is_asked_once_on_a_tty_and_saved():
    session = FakeSession(reason="quit")
    history = _fake_history()
    history.load_state.return_value = _state(None)
    with patch("yp.PlayerSession", return_value=session) as ctor, \
         patch("yp.history", history), \
         patch("yp._Prefetch") as prefetch, \
         patch("yp._ask_cookies", return_value=True) as ask, \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = True
        prefetch.return_value.done = True
        prefetch.return_value.result.return_value = None
        yp._play_session(_video())
    ask.assert_called_once_with()
    assert ctor.call_args.kwargs["cookies"] is True
    history.save_state.assert_called_once_with(100, True, None, TODAY, True)


def test_cookie_choice_is_not_asked_again_once_saved():
    session = FakeSession(reason="quit")
    with patch("yp._ask_cookies") as ask:
        ctor, history = _run_with_state(_state(False), session)
    ask.assert_not_called()
    assert ctor.call_args.kwargs["cookies"] is False
    history.save_state.assert_called_once_with(100, True, None, TODAY, False)


def test_cookie_choice_defaults_to_off_without_a_tty():
    session = FakeSession(reason="quit")
    with patch("yp._ask_cookies") as ask:
        ctor, history = _run_with_state(_state(None), session)   # _run_with_state는 isatty=False
    ask.assert_not_called()
    assert ctor.call_args.kwargs["cookies"] is False
    history.save_state.assert_called_once_with(100, True, None, TODAY, False)


# --- 자동재생 항목의 실제 길이 기록 (Important 4) ---

def test_observed_duration_is_recorded_before_position_bookkeeping():
    session = FakeSession(reason="quit", duration=1000.0, position=990.0)
    history = _fake_history()
    _run(session, history)
    history.record_duration.assert_called_once_with(VIDEO_ID, 1000.0)
    names = [c[0] for c in history.mock_calls]
    assert names.index("record_duration") < names.index("record_position")


def test_duration_is_not_recorded_when_mpv_never_reported_one():
    session = FakeSession(reason="error", duration=0.0)
    history = _fake_history()
    _run(session, history)
    history.record_duration.assert_not_called()


@pytest.mark.parametrize("reason", ["eof", "quit", "error"])
def test_duration_is_recorded_on_every_exit_reason(reason):
    session = FakeSession(reason=reason, duration=42.0)
    history = _fake_history()
    _run(session, history)
    history.record_duration.assert_called_once_with(VIDEO_ID, 42.0)


# --- 재생 중에는 아무것도 스크롤백에 찍지 않는다 ---

def test_nothing_is_printed_while_the_alt_screen_is_up(capsys):
    session = FakeSession(reason="quit")
    _run(session, _fake_history())
    assert capsys.readouterr().out == ""


def test_the_track_metadata_is_pushed_to_the_card(capsys):
    session = FakeSession(reason="quit")
    _run(session, _fake_history())
    assert session.tracks == [("영상", "채널", 0)]


def test_the_resume_position_and_connecting_message_share_one_notice():
    session = FakeSession(reason="quit")
    history = _fake_history()
    history.resume_position.return_value = 59.0
    _run(session, history)
    assert session.notices[0] == "⏩ 0:59부터 이어서 · 스트림 연결 중..."


def test_without_a_resume_position_the_notice_is_just_connecting():
    session = FakeSession(reason="quit")
    _run(session, _fake_history())
    assert session.notices[0] == "스트림 연결 중..."


def test_mpv_errors_are_printed_after_the_screen_is_restored(capsys):
    session = FakeSession(reason="error")
    session.errors = ["[mpv] ffmpeg: Server returned 403"]
    _run(session, _fake_history())
    out = capsys.readouterr().out
    assert "[mpv] ffmpeg: Server returned 403" in out
    assert "재생에 실패했습니다" in out


def test_a_missing_next_video_is_reported_after_the_screen_is_restored(capsys):
    session = FakeSession(reason="eof")
    _run(session, _fake_history())
    assert "다음 영상을 찾지 못했습니다" in capsys.readouterr().out


def test_errors_accumulated_across_the_autoplay_chain_are_all_printed(capsys):
    """session.errors는 load() 사이에 리셋되지 않고 누적된다 — 체인이 여러 곡을
    거치며 실패해도 마지막 곡의 오류만 남지 않고 전부 화면 복구 뒤에 찍혀야 한다."""
    session = FakeSession(reason="eof")
    call_count = 0

    def load(url, start=None, stream=None):
        nonlocal call_count
        call_count += 1
        session.errors.append(f"[mpv] 오류 {call_count}")
        return "eof" if call_count < 3 else "quit"

    session.load = load

    history = _fake_history()
    with patch("yp.PlayerSession", return_value=session), \
         patch("yp.history", history), \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        videos = [
            {"title": f"영상{n}", "channel": "채널", "url": URL} for n in range(2, 4)
        ]
        with patch("yp._Prefetch") as prefetch:
            prefetch.return_value.done = True
            prefetch.return_value.result.side_effect = [*videos, None]
            yp._play_session(_video())

    out = capsys.readouterr().out
    assert "[mpv] 오류 1" in out
    assert "[mpv] 오류 2" in out
    assert "[mpv] 오류 3" in out


# --- 재생목록: p로 되감고 n으로 복귀 ---

A_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
B_URL = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
C_URL = "https://www.youtube.com/watch?v=ccccccccccc"


def _track(title, url):
    return {"title": title, "channel": "채널", "url": url, "duration": 0}


class ChainSession(FakeSession):
    """load()마다 정해진 사유를 돌려주고, 그때의 url/start/has_prev를 기록한다."""

    def __init__(self, reasons):
        super().__init__()
        self._reasons = list(reasons)
        self.loads: list[tuple] = []
        self.prev_flags: list = []
        self.hints: list = []
        self.has_prev = None   # yp가 세우지 않으면 None이 남는다
        self.streams: list = []

    def load(self, url, start=None, stream=None):
        self.loads.append((url, start))
        self.streams.append(stream)
        self.prev_flags.append(self.has_prev)
        return self._reasons.pop(0) if self._reasons else "quit"

    def set_next_hint(self, text):
        self.hints.append(text)


class _DonePrefetch:
    def __init__(self, value):
        self.value = value
        self.done = True

    def result(self):
        return self.value


class FakePrefetch:
    """url -> 다음 영상 맵. 몇 번 호출됐는지 created로 남긴다."""

    def __init__(self, nexts):
        self.nexts = nexts
        self.created: list[str] = []

    def __call__(self, url, played_ids, channel, session, find_next=None):
        self.created.append(url)
        return _DonePrefetch(self.nexts.get(url))


def _run_chain(session, nexts, history=None):
    history = history or _fake_history()
    prefetch = FakePrefetch(nexts)
    with patch("yp.PlayerSession", return_value=session), \
         patch("yp.history", history), \
         patch("yp._Prefetch", prefetch), \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        yp._play_session(_track("영상A", A_URL))
    return prefetch, history


def _played(session):
    return [url for url, _ in session.loads]


def test_prev_goes_back_to_the_previous_track():
    session = ChainSession(["next", "prev", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL)})
    assert _played(session) == [A_URL, B_URL, A_URL]


def test_next_after_prev_returns_to_the_track_you_left():
    session = ChainSession(["next", "prev", "next", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL),
                         B_URL: _track("영상C", C_URL)})
    assert _played(session) == [A_URL, B_URL, A_URL, B_URL]


def test_autoplay_after_prev_also_returns_to_the_track_you_left():
    session = ChainSession(["next", "prev", "eof", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL),
                         B_URL: _track("영상C", C_URL)})
    assert _played(session) == [A_URL, B_URL, A_URL, B_URL]


def test_walking_forward_again_reuses_the_prefetch_instead_of_looking_twice():
    session = ChainSession(["next", "prev", "next", "quit"])
    prefetch, _ = _run_chain(session, {A_URL: _track("영상B", B_URL),
                                       B_URL: _track("영상C", C_URL)})
    assert prefetch.created == [A_URL, B_URL]


def test_the_already_known_next_track_is_hinted_without_a_lookup():
    session = ChainSession(["next", "prev", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL)})
    assert "영상B · 채널" in session.hints


def test_a_track_revisited_by_prev_starts_from_the_beginning():
    """세션 안에서 되감아 돌아온 곡은 이어보기를 적용하지 않는다."""
    history = _fake_history()
    history.resume_position.return_value = 120.0
    session = ChainSession(["next", "prev", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL)}, history)
    assert [start for _, start in session.loads] == [120.0, 120.0, None]


def test_has_prev_is_false_only_on_the_first_track():
    session = ChainSession(["next", "prev", "quit"])
    _run_chain(session, {A_URL: _track("영상B", B_URL)})
    assert session.prev_flags == [False, True, False]


# --- 프리페치 2단계: 다음 곡 찾기 → 스트림 풀기 ---

from player import Stream

D_URL = "https://rr1---sn-xyz.googlevideo.com/videoplayback?itag=140"


class _HintSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.hints: list = []

    def set_next_hint(self, text):
        self.hints.append(text)


def test_prefetch_attaches_the_resolved_stream_and_duration_to_the_next_track():
    session = _HintSession()
    nxt = {"title": "다음", "channel": "채널", "url": B_URL}
    with patch("yp.fetch_next", return_value=nxt), \
         patch("yp.resolve_stream", return_value=Stream(D_URL, "android", 617.0)) as resolve:
        result = yp._Prefetch(A_URL, set(), "채널", session).result()
    resolve.assert_called_once_with(B_URL, None, True)
    assert result == {**nxt, "stream": Stream(D_URL, "android", 617.0), "duration": 617.0}


def test_prefetch_hints_the_next_track_before_resolving_its_stream():
    session = _HintSession()
    seen_hints_at_resolve = []

    def fake_resolve(url, strategy, cookies):
        seen_hints_at_resolve.append(list(session.hints))
        return None
    with patch("yp.fetch_next", return_value={"title": "다음", "channel": "채널", "url": B_URL}), \
         patch("yp.resolve_stream", fake_resolve):
        result = yp._Prefetch(A_URL, set(), "채널", session).result()
    assert seen_hints_at_resolve == [["다음 · 채널"]]
    assert result["stream"] is None and result["duration"] == 0


def test_prefetch_passes_the_sessions_current_strategy_to_the_resolver():
    session = _HintSession()
    session.strategy = "web_safari"
    with patch("yp.fetch_next", return_value={"title": "다음", "channel": "채널", "url": B_URL}), \
         patch("yp.resolve_stream", return_value=None) as resolve:
        yp._Prefetch(A_URL, set(), "채널", session).result()
    resolve.assert_called_once_with(B_URL, "web_safari", True)


def test_prefetch_passes_the_sessions_cookie_choice_to_the_resolver():
    session = _HintSession()
    session.cookies = False
    with patch("yp.fetch_next", return_value={"title": "다음", "channel": "채널", "url": B_URL}), \
         patch("yp.resolve_stream", return_value=None) as resolve:
        yp._Prefetch(A_URL, set(), "채널", session).result()
    assert resolve.call_args[0][2] is False


def test_prefetch_without_a_next_track_does_not_resolve_anything():
    with patch("yp.fetch_next", return_value=None), \
         patch("yp.resolve_stream") as resolve:
        assert yp._Prefetch(A_URL, set(), "채널", _HintSession()).result() is None
    resolve.assert_not_called()


def test_play_session_hands_the_prefetched_stream_to_load():
    session = ChainSession(["eof", "quit"])
    stream = Stream(D_URL, "android", 617.0)
    nexts = {A_URL: {"title": "영상B", "channel": "채널", "url": B_URL, "stream": stream, "duration": 617.0}}
    _run_chain(session, nexts)
    assert session.streams == [None, stream]
    assert session.tracks[1] == ("영상B", "채널", 617.0)   # 프리페치가 채운 길이가 카드로 간다


# --- 채널 모드 (-c) ---

from channel import ChannelQueue


def test_prefetch_uses_the_given_find_next_instead_of_the_sidebar():
    session = _HintSession()
    nxt = {"title": "다음", "channel": "채널", "url": B_URL}
    with patch("yp.fetch_next") as sidebar, patch("yp.resolve_stream", return_value=None):
        result = yp._Prefetch(A_URL, set(), "채널", session, find_next=lambda: nxt).result()
    sidebar.assert_not_called()
    assert result["url"] == B_URL and session.hints == ["다음 · 채널"]


def test_prefetch_defaults_to_the_sidebar_lookup():
    session = _HintSession()
    with patch("yp.fetch_next", return_value=None) as sidebar, patch("yp.resolve_stream"):
        yp._Prefetch(A_URL, {"x"}, "채널", session).result()
    sidebar.assert_called_once_with(A_URL, {"x"}, "채널")


def test_play_session_in_channel_mode_follows_the_channel_order_and_ends_with_the_channel_note(capsys):
    session = ChainSession(["eof", "eof", "eof"])
    q = ChannelQueue({"id": "UC1", "name": "조강현", "handle": None})
    q.videos = [_track("영상A", A_URL), _track("영상B", B_URL), _track("영상C", C_URL)]
    q.exhausted = True
    with patch("yp.PlayerSession", return_value=session), \
         patch("yp.history", _fake_history()), \
         patch("yp.resolve_stream", return_value=None), \
         patch("yp.fetch_next") as sidebar, \
         patch("yp.sys.stdin") as stdin:
        stdin.isatty.return_value = False
        yp._play_session(q.videos[0], find_next=q.next_after, end_note="채널 영상을 모두 들었습니다.")
    sidebar.assert_not_called()
    assert _played(session) == [A_URL, B_URL, C_URL]
    assert "채널 영상을 모두 들었습니다." in capsys.readouterr().out


def test_run_channel_lists_the_channel_and_plays_the_pick_then_returns_to_the_list(capsys):
    ch = {"id": "UC1", "name": "조강현", "handle": "@jo"}
    videos = [_track(f"영상{i}", f"https://www.youtube.com/watch?v=v{i:010d}") for i in range(1, 31)]
    picks = iter([videos[2]["url"], None])
    with patch("yp.resolve_channel", return_value=ch), \
         patch("channel.channel_videos", side_effect=[videos, []]), \
         patch("yp.select_video", side_effect=lambda *a, **k: next(picks)) as select, \
         patch("yp._play_session") as play:
        yp._run_channel("조강현")
    out = capsys.readouterr().out
    assert "조강현" in out and "@jo" in out
    play.assert_called_once()
    args, kwargs = play.call_args
    assert args[0] is videos[2]
    assert kwargs["find_next"] is not None and "채널" in kwargs["end_note"]
    assert select.call_count == 2                       # 재생 후 목록으로 돌아왔다
    assert "채널" in select.call_args.kwargs.get("message", "")


def test_run_channel_reports_when_the_channel_is_not_found(capsys):
    with patch("yp.resolve_channel", return_value=None), patch("yp._play_session") as play:
        yp._run_channel("ㅁㄴㅇㄹ")
    assert "찾지 못" in capsys.readouterr().out
    play.assert_not_called()


def test_main_routes_dash_c_to_the_channel_flow():
    with patch("yp.check_mpv", return_value=True), patch("yp._run_channel") as run_channel, \
         patch("yp.sys.argv", ["yp", "-c", "조강현", "게임"]):
        yp.main()
    run_channel.assert_called_once_with("조강현 게임")
