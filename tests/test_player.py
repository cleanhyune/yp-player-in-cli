import contextlib
import io
import os
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

import player
from player import PlayerError, PlayerSession, check_mpv
from tui import Pager, render_comments, render_playing

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class FakeClient:
    def __init__(self, path):
        self.path = path
        self.events = queue.Queue()
        self.commands = []
        self.closed = False

    def connect(self, timeout=5.0):
        pass

    def observe(self, observe_id, name):
        self.commands.append(("observe_property", observe_id, name))

    def request_log_messages(self, level="error"):
        self.commands.append(("request_log_messages", level))

    def command(self, *args, timeout=5.0):
        self.commands.append(tuple(args))
        return None

    def close(self):
        self.closed = True


class _InterruptingQueue(queue.Queue):
    """allow번까지만 정상으로 꺼내주고 그 다음 get에서 KeyboardInterrupt를 던진다."""

    def __init__(self, allow):
        super().__init__()
        self._allow = allow

    def get(self, *args, **kwargs):
        if self._allow <= 0:
            raise KeyboardInterrupt
        self._allow -= 1
        return super().get(*args, **kwargs)


@pytest.fixture
def session_factory():
    """PlayerSession을 인자와 함께 만들어야 하는 테스트용. session 픽스처의 일반형."""
    with patch("player.MpvClient", FakeClient), \
         patch("player.subprocess.Popen") as popen, \
         patch("player.os.remove"):
        def make(**kwargs):
            kwargs.setdefault("volume", 80)
            kwargs.setdefault("autoplay", True)
            kwargs.setdefault("tty", False)
            s = PlayerSession(**kwargs)
            s.start()
            s.popen = popen
            return s
        yield make


@pytest.fixture
def session(session_factory):
    yield session_factory()


def _end(reason):
    return {"event": "end-file", "reason": reason}


def _key(k):
    return {"event": "key", "key": k}


def _blocks(items):
    """문자열 하나 = 한 줄짜리 블록. 이미 블록(list)이면 그대로."""
    return [[b] if isinstance(b, str) else b for b in items]


def _comments(items, generation, more=False, header=None):
    blocks = _blocks(items)
    return {"event": "comments-ready", "header": header or ["제목", ""], "blocks": blocks,
            "payloads": [{"reply_token": None} for _ in blocks], "more": more,
            "generation": generation, "pager": None}


def _more(items, generation, pager, more=False):
    blocks = _blocks(items)
    return {"event": "comments-more", "blocks": blocks,
            "payloads": [{"reply_token": None} for _ in blocks], "more": more,
            "generation": generation, "pager": pager}


def _open(s, items, more=False):
    """페이저를 직접 연다 (load() 없이). 연 페이저를 돌려준다."""
    s._open_pager(_comments(items, generation=s._load_generation, more=more))
    return s._modal


def _c(author, text="본문", reply_token=None, reply_count=0):
    return {"id": author, "author": author, "text": text, "likes": None, "age": None,
            "reply_count": reply_count, "reply_token": reply_token}


class FakeFeed:
    """CommentFeed 대역. 호출마다 댓글 한 개를 주고 shown을 늘린다. 첫 댓글에는 답글 토큰이 있다."""

    created = []

    def __init__(self, url, sort="top", sort_tokens=None):
        self.url, self.sort = url, sort
        self.sort_tokens = sort_tokens or {"top": "T", "new": "N"}
        self.shown, self.pages = 0, 0
        FakeFeed.created.append(self)

    def next_page(self):
        self.pages += 1
        self.shown += 1
        token = "R1" if self.pages == 1 else None
        return [_c(f"유저{self.pages}", f"본문{self.pages}", reply_token=token,
                   reply_count=3 if token else 0)], True


class FakeReplyFeed:
    created = []

    def __init__(self, token):
        self.token, self.shown, self.pages = token, 0, 0
        FakeReplyFeed.created.append(self)

    def next_page(self):
        self.pages += 1
        self.shown += 1
        return [_c(f"답글러{self.pages}")], self.pages < 2


class ExplodingFeed(FakeFeed):
    def next_page(self):
        raise RuntimeError("boom")


@pytest.fixture
def feeds():
    FakeFeed.created = []
    FakeReplyFeed.created = []
    with patch("player.CommentFeed", FakeFeed), patch("player.ReplyFeed", FakeReplyFeed):
        yield FakeFeed.created


ALT_ON = "\x1b[?1049h"
ALT_OFF = "\x1b[?1049l"


@contextlib.contextmanager
def tty_session(client_cls=None):
    """tty=True 세션 + 가짜 stdout.

    player.py는 대체 화면/페이저를 그릴 때 sys.stdout을 명시적으로 넘기므로 그 출력만
    StringIO에 잡힌다. 인자 없이 부르는 end_status/draw_status는 tui의 기본 인자에
    묶인 진짜 stdout으로 나가서 여기 섞이지 않는다.
    """
    out = io.StringIO()
    with patch("player.MpvClient", client_cls or FakeClient), \
         patch("player.subprocess.Popen") as popen, \
         patch("player.os.remove"), \
         patch("player.terminal_mode"), \
         patch("player.KeyReader"), \
         patch("player.sys.stdin") as stdin, \
         patch("player.sys.stdout", out), \
         patch("player.terminal_size", return_value=os.terminal_size((80, 24))):
        stdin.fileno.return_value = 0
        s = PlayerSession(tty=True)
        s.start()
        s.popen = popen
        yield s, out


# --- start ---

def test_start_launches_headless_idle_mpv_with_ipc(session):
    args = session.popen.call_args[0][0]
    assert args[0] == "mpv"
    for flag in ("--no-video", "--no-terminal", "--idle=yes", "--ytdl-format=bestaudio/best",
                 "--volume=80", f"--input-ipc-server={session._socket}"):
        assert flag in args
    assert any(a.startswith("--script-opts=ytdl_hook-ytdl_path=") for a in args)
    assert not any(a.startswith("--script=") for a in args)
    assert URL not in args  # 영상은 loadfile로 넘긴다


def test_start_clears_stale_socket():
    with patch("player.MpvClient", FakeClient), patch("player.subprocess.Popen"), \
         patch("player.os.remove") as remove:
        s = PlayerSession(tty=False)
        s.start()
    remove.assert_any_call(s._socket)


def test_socket_path_is_per_process_and_not_the_old_fixed_path():
    s = PlayerSession(tty=False)
    assert s._socket != "/tmp/yp_mpv_socket"
    assert str(os.getpid()) in os.path.basename(s._socket)
    with patch("player.os.getpid", return_value=os.getpid() + 1):
        assert PlayerSession(tty=False)._socket != s._socket


def test_quit_removes_socket_file(session):
    with patch("player.os.remove") as remove:
        session.quit()
    remove.assert_any_call(session._socket)


def test_start_observes_properties_and_requests_error_logs(session):
    names = {c[2] for c in session._client.commands if c[0] == "observe_property"}
    assert names >= {"time-pos", "pause", "volume", "duration", "media-title"}
    assert ("request_log_messages", "error") in session._client.commands


def test_start_raises_player_error_and_reaps_mpv_when_socket_never_appears():
    class NeverConnects(FakeClient):
        def connect(self, timeout=5.0):
            raise player.MpvError("no socket")

    with patch("player.MpvClient", NeverConnects), patch("player.subprocess.Popen") as popen, \
         patch("player.os.remove"):
        with pytest.raises(PlayerError):
            PlayerSession(tty=False).start()
    proc = popen.return_value
    assert proc.kill.called   # 재생 전 idle mpv는 3초 기다리지 않고 바로 죽인다
    assert not any(c.kwargs.get("timeout") for c in proc.wait.call_args_list)


# --- load / reasons ---

def test_load_returns_eof_and_configures_first_client(session):
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    cmds = session._client.commands
    assert ("set", "ytdl-raw-options", "extractor-args=youtube:player_client=android") in cmds
    assert ("set", "start", "none") in cmds
    assert ("loadfile", URL) in cmds
    assert cmds.index(("set", "start", "none")) < cmds.index(("loadfile", URL))


def test_load_passes_resume_position_as_whole_seconds(session):
    session._client.events.put(_end("eof"))
    session.load(URL, start=125.7)
    assert ("set", "start", "125") in session._client.commands


def test_load_retries_with_fallback_client_on_error(session):
    session._client.events.put(_end("error"))
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    clients = [c[2] for c in session._client.commands if c[:2] == ("set", "ytdl-raw-options")]
    assert clients == ["extractor-args=youtube:player_client=android",
                       "extractor-args=youtube:player_client=web_embedded"]
    assert session._client.commands.count(("loadfile", URL)) == 2


def test_load_returns_error_when_all_clients_fail(session):
    for _ in player._ATTEMPTS:
        session._client.events.put(_end("error"))
    assert session.load(URL) == "error"


def test_attempt_order_drops_cookie_attempts_when_declined():
    with_cookies = player._attempt_order(None)
    without = player._attempt_order(None, cookies=False)
    assert any(c for _, c in with_cookies)
    assert without and not any(c for _, c in without)
    # 기억한 전략이 쿠키 칸이어도 거절했으면 기본 순서로 돈다
    assert player._attempt_order("web_safari", cookies=False) == without


def test_load_never_attaches_cookies_when_declined(session_factory):
    s = session_factory(cookies=False)
    for _ in player._ATTEMPTS:
        s._client.events.put(_end("error"))
    assert s.load(URL) == "error"
    raw = [c[2] for c in s._client.commands if c[0] == "set" and c[1] == "ytdl-raw-options"]
    assert raw and not any("cookies-from-browser" in r for r in raw)


def test_n_key_sends_stop_and_reports_next(session):
    session._client.events.put(_key("n"))
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "next"
    assert ("stop",) in session._client.commands


def test_stop_without_n_is_quit(session):
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "quit"


def test_next_request_does_not_leak_into_following_load(session):
    session._client.events.put(_key("n"))
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "next"
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "quit"


def test_q_key_sends_quit_and_reports_quit(session):
    session._client.events.put(_key("q"))
    session._client.events.put(_end("quit"))
    assert session.load(URL) == "quit"
    assert ("quit",) in session._client.commands


def test_q_key_followed_by_socket_close_is_still_quit(session):
    session._client.events.put(_key("q"))
    session._client.events.put({"event": "mpv-exited"})
    assert session.load(URL) == "quit"
    assert session.closed is True


def test_unexpected_mpv_exit_is_error_and_closes_session(session):
    session._client.events.put({"event": "mpv-exited"})
    assert session.load(URL) == "error"
    assert session.closed is True


def test_redirect_and_unknown_end_reasons_are_ignored(session):
    session._client.events.put(_end("redirect"))
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"


# --- keys ---

def test_a_key_toggles_autoplay(session):
    session._client.events.put(_key("a"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert session.autoplay is False


def test_other_keys_are_forwarded_to_mpv_as_keypress(session):
    for k in ("SPACE", "RIGHT", "9"):
        session._client.events.put(_key(k))
    session._client.events.put(_end("eof"))
    session.load(URL)
    cmds = session._client.commands
    assert ("keypress", "SPACE") in cmds and ("keypress", "RIGHT") in cmds and ("keypress", "9") in cmds


def test_g_key_opens_prompt_and_seeks_on_valid_timecode(session):
    for k in ("g", "0", "7", "1", "0", "ENTER"):
        session._client.events.put(_key(k))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert ("seek", "430", "absolute") in session._client.commands


def test_g_key_prompt_swallows_keys_until_submit(session):
    for k in ("g", "q", "ESC"):   # 프롬프트 안의 q는 종료가 아니라 글자 입력
        session._client.events.put(_key(k))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert ("quit",) not in session._client.commands


# --- properties ---

def test_time_pos_updates_position_and_ignores_none(session):
    session._on_property("time-pos", 12.5)
    assert session.position == 12.5
    session._on_property("time-pos", None)
    assert session.position == 12.5


def test_volume_and_pause_and_title_are_tracked(session):
    session._on_property("volume", 64.6)
    session._on_property("pause", True)
    session._on_property("media-title", "제목")
    session._on_property("duration", 100.0)
    assert session.volume == 65 and session.paused is True and session.title == "제목" and session.duration == 100.0


def test_position_callback_is_throttled_to_ten_seconds(session):
    clock = {"t": 100.0}
    calls = []
    with patch("player.time.monotonic", lambda: clock["t"]):
        session.on_position(calls.append)
        session._on_property("time-pos", 1.0)
        clock["t"] = 105.0
        session._on_property("time-pos", 6.0)
        clock["t"] = 111.0
        session._on_property("time-pos", 12.0)
    assert calls == [1.0, 12.0]


def test_set_next_hint_queues_redraw_event(session):
    session.set_next_hint("다음 영상")
    assert session._client.events.get_nowait() == {"event": "redraw"}
    assert session._next_hint == "다음 영상"


# --- comments ---

def test_t_key_starts_a_popular_sorted_feed_and_queues_the_first_page(session, feeds):
    session._client.events.put(_key("t"))
    session._client.events.put(_end("eof"))
    session._on_property("media-title", "테스트 제목")
    session.load(URL)
    session._comments_thread.join(timeout=2)

    assert [f.sort for f in feeds] == ["top"]
    ready = [e for e in list(session._client.events.queue) if e.get("event") == "comments-ready"]
    assert len(ready) == 1
    assert ready[0]["header"][0] == "테스트 제목"
    assert "인기순" in ready[0]["header"][2]
    assert len(ready[0]["blocks"]) == 1
    assert ready[0]["payloads"][0]["author"] == "유저1"
    assert ready[0]["pager"] is None
    assert ready[0]["more"] is True
    assert ready[0]["generation"] == session._load_generation


def test_t_key_ignored_while_fetch_running(session):
    running = MagicMock()
    running.is_alive.return_value = True
    session._comments_thread = running
    with patch("player.threading.Thread") as thread_cls:
        session._client.events.put(_key("t"))
        session._client.events.put(_end("eof"))
        session.load(URL)
    thread_cls.assert_not_called()


def test_t_key_while_previous_videos_comments_still_loading_explains_instead_of_silence(session):
    running = MagicMock()
    running.is_alive.return_value = True
    session._comments_thread = running
    session._comments_generation = 0          # 이전 load()에서 시작된 스레드
    with patch("player.threading.Thread") as thread_cls:
        session._client.events.put(_key("t"))
        session._client.events.put(_end("eof"))
        session.load(URL)                      # generation은 1이 된다
    thread_cls.assert_not_called()
    assert session._message is not None and "이전 영상" in session._message


def test_t_key_while_same_videos_comments_still_loading_says_loading(session):
    running = MagicMock()
    running.is_alive.return_value = True
    session._comments_thread = running
    session._comments_generation = session._load_generation + 1   # 이번 load()와 같은 세대
    with patch("player.threading.Thread") as thread_cls:
        session._client.events.put(_key("t"))
        session._client.events.put(_end("eof"))
        session.load(URL)
    thread_cls.assert_not_called()
    assert session._message == "댓글 불러오는 중..."


def test_t_key_on_a_new_video_starts_a_fresh_feed(session, feeds):
    stale = FakeFeed(URL)
    stale.shown = 300
    session._feed = stale
    session._client.events.put(_key("t"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    session._comments_thread.join(timeout=2)

    assert session._feed is not stale
    assert session._feed.shown == 1          # 300을 이어받지 않고 처음부터 센다


def test_comments_failure_shows_message_not_crash(session):
    with patch("player.CommentFeed", ExplodingFeed):
        session._client.events.put(_key("t"))
        session._client.events.put(_end("eof"))
        session.load(URL)
        session._comments_thread.join(timeout=2)
    failed = [e for e in list(session._client.events.queue) if e.get("event") == "comments-failed"]
    assert len(failed) == 1
    assert failed[0]["generation"] == session._load_generation


# --- 대체 화면 / 페이저 ---

def test_start_enters_the_alt_screen_and_hides_the_cursor():
    with tty_session() as (s, out):
        text = out.getvalue()
        assert text.count(ALT_ON) == 1
        assert "\x1b[?25l" in text
        assert ALT_OFF not in text


def test_a_pager_round_trip_does_not_touch_the_alt_screen():
    """재생 화면이 이미 대체 화면이므로 페이저는 그 위에 그리기만 한다."""
    with tty_session() as (s, out):
        out.truncate(0), out.seek(0)
        s._client.events.put(_comments(["댓글1", "댓글2", "댓글3"], generation=1))
        s._client.events.put(_key("j"))
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"
        text = out.getvalue()
    assert ALT_ON not in text and ALT_OFF not in text
    assert s._modal is None


def test_load_does_not_leave_the_alt_screen_so_autoplay_never_flickers():
    with tty_session() as (s, out):
        s._client.events.put(_end("eof"))
        s.load(URL)
        assert ALT_OFF not in out.getvalue()
        s.quit()
        text = out.getvalue()
    assert text.count(ALT_ON) == 1
    assert text.count(ALT_OFF) == 1


def test_alt_screen_is_left_exactly_once_when_load_raises():
    class Interrupting(FakeClient):
        """페이저가 열린 직후 두 번째 get에서 Ctrl-C가 들어온 상황."""

        def __init__(self, path):
            super().__init__(path)
            self.events = _InterruptingQueue(allow=1)

    with tty_session(Interrupting) as (s, out):
        s._client.events.put(_comments(["댓글1"], generation=1))
        with pytest.raises(KeyboardInterrupt):
            s.load(URL)
        # 계약의 절반: load()는 예외로 빠져나가도 화면을 유지한다. 이 단언이 없으면
        # load()가 화면을 나가버려도 quit()의 멱등성 덕에 아래 카운트가 통과한다.
        assert ALT_OFF not in out.getvalue()
        s.quit()
        text = out.getvalue()
    assert text.count(ALT_ON) == 1
    assert text.count(ALT_OFF) == 1
    assert "\x1b[?25h" in text
    assert s._modal is None


def test_quit_leaves_the_alt_screen_when_a_pager_is_still_open():
    with tty_session() as (s, out):
        _open(s, ["댓글1"])
        s.quit()
        text = out.getvalue()
    assert text.count(ALT_OFF) == 1
    assert s._modal is None


def test_pager_key_handling_uses_the_panel_height_not_the_whole_screen():
    """80x24 터미널에서 본문은 20줄이다. 24줄로 계산하면 스크롤이 화면과 어긋난다."""
    from tui import comments_height
    with tty_session() as (s, out):
        _open(s, [f"댓글 {i}" for i in range(100)])   # 헤더 2줄 + 100블록
        s._modal_key("SPACE")
        assert s._modal.top == comments_height(24) == 20


def test_the_progress_bar_keeps_ticking_while_the_comments_pager_is_open():
    """댓글 패널 위쪽에 진행바가 남는 것이 이 화면의 존재 이유다.

    _redraw()에 '모달이 열려 있으면 그리지 않는다' 가드를 되돌리면 시각이 멈춘다.
    """
    with tty_session() as (s, out):
        s.set_track("삼국지 리뷰", "침착맨", 300.0)
        s._client.events.put(_comments(["댓글1", "댓글2"], generation=1))
        s._client.events.put(_prop("time-pos", 65.0))
        s._client.events.put(_end("eof"))
        with patch("player._REDRAW_INTERVAL", 0.0):
            assert s.load(URL) == "eof"
        frame = out.getvalue().rsplit("\x1b[H", 1)[-1]
    assert "1:05" in frame        # 페이저가 열린 채로 시각이 갱신됐고
    assert "댓글1" in frame       # 그 프레임은 여전히 댓글 패널이다


def test_two_back_to_back_loads_never_flicker_through_the_normal_screen():
    """자동재생 체인 전체가 한 화면 세션이다. load()가 화면을 나가면 곡마다 깜빡인다."""
    with tty_session() as (s, out):
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"
        text = out.getvalue()
    assert text.count(ALT_ON) == 1
    assert ALT_OFF not in text


# --- 세대 (이전 영상의 댓글이 다음 영상 위에 뜨는 것 방지) ---

def test_current_generation_comments_open_the_pager():
    with tty_session() as (s, out):
        s._client.events.put(_comments(["댓글1"], generation=1))
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        s.load(URL)
        # 이벤트 스트림의 q가 페이저를 닫으므로 _modal은 이미 None이다. 페이저가
        # 열렸다는 증거는 화면에 찍힌 댓글 본문이다 (stale 쪽 테스트와 대칭).
        assert "댓글1" in out.getvalue()


def test_stale_comments_from_previous_video_are_dropped():
    with tty_session() as (s, out):
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"          # 1세대
        stale_gen = s._load_generation
        s._client.events.put(_comments(["이전 영상 댓글"], generation=stale_gen))
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"          # 2세대
        text = out.getvalue()
    assert "이전 영상 댓글" not in text   # 다음 영상 화면 위에 페이저가 열리지 않았다
    assert text.count(ALT_ON) == 1      # start()의 진입 한 번뿐 — 화면은 그대로다
    assert s._modal is None


def test_stale_comments_failure_does_not_flash_on_next_video():
    with tty_session() as (s, _):
        s._client.events.put(_end("eof"))
        s.load(URL)
        stale_gen = s._load_generation
        s._client.events.put({"event": "comments-failed", "generation": stale_gen})
        s._client.events.put(_end("eof"))
        s.load(URL)
        assert s._message is None


# --- quit / terminal ---

def test_quit_sends_quit_waits_for_process_and_closes_client(session):
    session.quit()
    assert ("quit",) in session._client.commands
    assert session.popen.return_value.wait.called
    assert session._client.closed is True
    assert session.closed is True


def test_tty_session_enters_and_restores_terminal_mode():
    with patch("player.MpvClient", FakeClient), patch("player.subprocess.Popen"), \
         patch("player.os.remove"), patch("player.terminal_mode") as term, \
         patch("player.KeyReader") as reader, patch("player.sys.stdin") as stdin:
        stdin.fileno.return_value = 0
        s = PlayerSession(tty=True)
        s.start()
        term.return_value.__enter__.assert_called_once()
        reader.return_value.start.assert_called_once()
        s.quit()
        reader.return_value.stop.assert_called_once()
        term.return_value.__exit__.assert_called_once()


# --- check_mpv (기존 유지) ---

def test_check_mpv_returns_true_when_installed():
    with patch("player.shutil.which", return_value="/opt/homebrew/bin/mpv"):
        assert check_mpv() is True


def test_check_mpv_returns_false_when_not_installed():
    with patch("player.shutil.which", return_value=None):
        assert check_mpv() is False


# --- 댓글 페이지네이션 (커서가 마지막 댓글에 닿으면 다음 페이지) ---

def test_reaching_the_last_comment_requests_the_next_page():
    with tty_session() as (s, out):
        s._feed = FakeFeed(URL)
        _open(s, ["댓글1"], more=True)
        s._modal_key("j")                 # 블록 하나뿐 → 이미 마지막
        s._comments_thread.join(timeout=2)
        text = out.getvalue()
    assert s._feed.pages == 1
    assert "다음 댓글 불러오는 중..." in text


def test_next_page_numbering_continues_from_what_was_already_shown():
    with tty_session() as (s, _out):
        s._feed = FakeFeed(URL)
        s._feed.shown = 100
        pager = _open(s, ["댓글1"], more=True)
        s._modal_key("G")
        s._comments_thread.join(timeout=2)
        ev = s._client.events.get_nowait()
    assert ev["event"] == "comments-more" and ev["pager"] is pager
    assert ev["blocks"][0][0].startswith(" 101. 유저1")
    assert "header" not in ev              # 이어붙일 페이지엔 헤더 없음


def test_no_next_page_request_when_the_first_page_was_the_last():
    with tty_session() as (s, _out):
        s._client.events.put(_comments(["댓글1"], generation=1, more=False))
        s._client.events.put(_key("G"))
        s._client.events.put(_end("eof"))
        with patch("player.threading.Thread") as thread_cls:
            s.load(URL)
    thread_cls.assert_not_called()


def test_no_duplicate_request_while_a_page_is_already_in_flight():
    with tty_session() as (s, _out):
        s._feed = FakeFeed(URL)
        _open(s, ["댓글1"], more=True)
        s._modal.status = "다음 댓글 불러오는 중..."
        with patch("player.threading.Thread") as thread_cls:
            s._modal_key("G")
    thread_cls.assert_not_called()


def test_next_page_is_appended_below_the_current_one():
    with tty_session() as (s, out):
        pager = _open(s, ["1페이지"], more=True)
        s._client.events.put(_more(["2페이지"], generation=1, pager=pager, more=False))
        s._client.events.put(_end("eof"))
        s.load(URL)
        text = out.getvalue()
    assert "2페이지" in text
    assert "1/2 " in text            # 커서는 그대로, 블록 수만 늘어남 ('+' 없음: 마지막 페이지)
    assert "q 닫기" in text          # 로딩 문구는 지워졌다


def test_next_page_failure_is_shown_in_the_footer_and_stops_further_requests():
    with tty_session() as (s, out):
        pager = _open(s, ["1페이지"], more=True)
        s._client.events.put({"event": "comments-more-failed", "generation": 1, "pager": pager})
        s._client.events.put(_key("G"))
        s._client.events.put(_end("eof"))
        with patch("player.threading.Thread") as thread_cls:
            s.load(URL)
        text = out.getvalue()
    assert "더 불러오지 못했습니다" in text
    thread_cls.assert_not_called()


def test_next_page_arriving_after_the_pager_closed_is_ignored():
    with tty_session() as (s, out):
        pager = _open(s, ["1페이지"], more=True)
        s._modal = None                                   # 닫혔다
        s._client.events.put(_more(["뒤늦은 2페이지"], generation=1, pager=pager))
        s._client.events.put(_end("eof"))
        assert s.load(URL) == "eof"
        text = out.getvalue()
    assert "뒤늦은 2페이지" not in text
    assert s._modal is None and len(pager.anchors) == 1   # 뒤늦은 페이지가 붙지 않았다


def test_stale_next_page_from_the_previous_video_is_dropped():
    with tty_session() as (s, out):
        s._client.events.put(_end("eof"))
        s.load(URL)                                   # 1세대
        stale_gen = s._load_generation
        pager = _open(s, ["현재 영상 댓글"], more=True)
        s._client.events.put(_more(["이전 영상 2페이지"], generation=stale_gen, pager=pager))
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        s.load(URL)                                   # 2세대
        text = out.getvalue()
    assert "이전 영상 2페이지" not in text


# --- 댓글 정렬 전환 (s) ---

def test_comment_pager_footer_advertises_the_sort_and_reply_keys():
    with tty_session() as (s, out):
        s._client.events.put(_comments(["댓글1"], generation=1))
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        s.load(URL)
    assert "s 정렬" in out.getvalue() and "Enter 답글" in out.getvalue()


def test_s_key_rebuilds_the_feed_with_the_other_sort_reusing_the_tokens():
    with tty_session() as (s, out):
        s._feed = FakeFeed(URL, sort="top", sort_tokens={"top": "T1", "new": "N1"})
        with patch("player.CommentFeed", FakeFeed):
            pager = _open(s, ["댓글1"], more=True)
            s._modal_key("s")
            s._comments_thread.join(timeout=2)
        ev = s._client.events.get_nowait()
        text = out.getvalue()
    assert s._feed.sort == "new"
    assert s._feed.sort_tokens == {"top": "T1", "new": "N1"}
    assert s._feed.shown == 1            # 처음부터 다시 센다
    assert ev["event"] == "comments-reload" and ev["pager"] is pager
    assert "최신순" in ev["header"][2]    # 헤더가 현재 정렬을 알려준다
    assert "최신순" in text               # 다시 불러오는 중임을 푸터에 표시


def test_s_key_toggles_back_to_the_popular_sort():
    with tty_session() as (s, _out):
        s._feed = FakeFeed(URL, sort="new")
        with patch("player.CommentFeed", FakeFeed):
            _open(s, ["댓글1"], more=True)
            s._modal_key("s")
            s._comments_thread.join(timeout=2)
    assert s._feed.sort == "top"


def test_s_key_ignored_while_a_page_is_in_flight():
    with tty_session() as (s, _out):
        s._feed = FakeFeed(URL, sort="top")
        _open(s, ["댓글1"], more=True)
        s._modal.status = "다음 댓글 불러오는 중..."
        with patch("player.threading.Thread") as thread_cls:
            s._modal_key("s")
    thread_cls.assert_not_called()
    assert s._feed.sort == "top"


def test_reload_replaces_the_list_and_moves_the_cursor_back_to_the_top():
    with tty_session() as (s, out):
        pager = _open(s, [f"줄{i}" for i in range(50)], more=False)
        s._client.events.put(_key("G"))
        s._client.events.put({"event": "comments-reload", "header": ["새 제목", ""],
                              "blocks": [["새 목록"]], "payloads": [{"reply_token": None}],
                              "more": True, "generation": 1, "pager": pager})
        s._client.events.put(_end("eof"))
        s.load(URL)
        text = out.getvalue()
    assert "새 목록" in text and "새 제목" in text
    assert "1/1+" in text         # 커서·스크롤이 처음으로


def test_reload_failure_is_shown_in_the_footer():
    with tty_session() as (s, out):
        pager = _open(s, ["1페이지"], more=True)
        s._client.events.put({"event": "comments-reload-failed", "generation": 1, "pager": pager})
        s._client.events.put(_end("eof"))
        s.load(URL)
    assert "정렬을 바꾸지 못했습니다" in out.getvalue()


def test_stale_reload_from_the_previous_video_is_dropped():
    with tty_session() as (s, out):
        s._client.events.put(_end("eof"))
        s.load(URL)
        stale_gen = s._load_generation
        pager = _open(s, ["현재 영상 댓글"], more=True)
        s._client.events.put({"event": "comments-reload", "header": ["x"], "blocks": [["이전 영상 최신순"]],
                              "payloads": [None], "more": True, "generation": stale_gen, "pager": pager})
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        s.load(URL)
        text = out.getvalue()
    assert "이전 영상 최신순" not in text


# --- 답글 (Enter) ---

def _open_root_pager(s, feeds):
    """t → comments-ready까지 진행해 루트 페이저가 열린 상태를 만든다."""
    s._request_comments()
    s._comments_thread.join(timeout=2)
    ev = s._client.events.get_nowait()
    assert ev["event"] == "comments-ready"
    s._open_pager(ev)
    return s._modal


def test_enter_on_a_comment_with_replies_pushes_a_reply_pager_and_fetches(feeds):
    with tty_session() as (s, out):
        root = _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        assert s._parent_pager is root
        reply_pager = s._modal
        assert reply_pager is not root
        assert reply_pager.status == "답글 불러오는 중..."
        assert "유저1" in reply_pager.header[0]
        assert FakeReplyFeed.created[0].token == "R1"
        s._comments_thread.join(timeout=2)
        ev = s._client.events.get_nowait()
        assert ev["event"] == "replies-ready" and ev["pager"] is reply_pager
        s._fill_pager(ev, replace=False)
        assert reply_pager.status is None and reply_pager.more is True
        assert "↳ 답글러1" in reply_pager.lines[reply_pager.anchors[0]]
        assert "q 되돌아가기" in out.getvalue()


def test_enter_on_a_comment_without_replies_does_nothing(feeds):
    with tty_session() as (s, _out):
        root = _open_root_pager(s, feeds)
        root.payloads[0]["reply_token"] = None
        s._modal_key("ENTER")
        assert s._modal is root and s._parent_pager is None
        assert FakeReplyFeed.created == []


def test_enter_is_ignored_while_a_request_is_in_flight(feeds):
    with tty_session() as (s, _out):
        root = _open_root_pager(s, feeds)
        root.status = "다음 댓글 불러오는 중..."
        s._modal_key("ENTER")
        assert s._modal is root and FakeReplyFeed.created == []


def test_closing_the_reply_pager_returns_to_the_root_pager_where_it_was(feeds):
    with tty_session() as (s, _out):
        root = _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        s._comments_thread.join(timeout=2)
        s._modal_key("q")
        assert s._modal is root and s._parent_pager is None
        assert s._reply_feed is None
        assert (root.top, root.cursor) == (0, 0)


def test_left_arrow_also_closes_the_reply_pager(feeds):
    with tty_session() as (s, _out):
        root = _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        s._modal_key("LEFT")
        assert s._modal is root


def test_a_late_reply_response_for_a_closed_pager_is_dropped(feeds):
    with tty_session() as (s, _out):
        _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        reply_pager = s._modal
        s._comments_thread.join(timeout=2)
        ev = s._client.events.get_nowait()
        s._modal_key("q")                      # 응답이 처리되기 전에 닫았다
        assert s._event_for_current_pager(ev) is False
        assert reply_pager.anchors == []


def test_sort_key_is_ignored_inside_the_reply_pager(feeds):
    with tty_session() as (s, _out):
        _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        s._comments_thread.join(timeout=2)
        before = len(feeds)
        s._modal_key("s")
        assert len(feeds) == before


def test_reaching_the_last_reply_fetches_the_next_reply_page(feeds):
    with tty_session() as (s, _out):
        _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        s._comments_thread.join(timeout=2)
        s._fill_pager(s._client.events.get_nowait(), replace=False)
        reply_pager = s._modal
        s._modal_key("j")                      # 블록 하나뿐 → 이미 마지막 → 다음 페이지 요청
        assert reply_pager.status == "다음 답글 불러오는 중..."
        s._comments_thread.join(timeout=2)
        ev2 = s._client.events.get_nowait()
        assert ev2["event"] == "replies-more" and ev2["pager"] is reply_pager


def test_reply_failure_is_shown_in_the_reply_pager_footer(feeds):
    with tty_session() as (s, out):
        _open_root_pager(s, feeds)
        s._modal_key("ENTER")
        reply_pager = s._modal
        s._comments_thread.join(timeout=2)
        s._client.events.get_nowait()          # 진짜 응답은 버리고 실패를 흘려 넣는다
        s._client.events.put({"event": "replies-failed", "generation": s._load_generation + 1,
                              "pager": reply_pager})     # load()가 세대를 하나 올린다
        s._client.events.put(_key("q"))
        s._client.events.put(_key("q"))
        s._client.events.put(_end("eof"))
        s.load(URL)
    assert "답글을 불러오지 못했습니다" in out.getvalue()


def test_load_finally_drops_the_whole_pager_stack(session):
    session._parent_pager = Pager(["x"])
    session._reply_feed = FakeReplyFeed("R")
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert session._modal is None and session._parent_pager is None and session._reply_feed is None


# --- 쿠키 폴백 / 전략 기억 ---

_ANDROID = "extractor-args=youtube:player_client=android"
_EMBEDDED = "extractor-args=youtube:player_client=web_embedded"
_COOKIED = ("extractor-args=youtube:player_client=web_safari,"
            "cookies-from-browser=chrome")


def _raw_options(session):
    return [c[2] for c in session._client.commands if c[:2] == ("set", "ytdl-raw-options")]


def _prop(name, data):
    return {"event": "property-change", "name": name, "data": data}


def test_load_falls_back_to_cookies_after_both_anonymous_attempts_fail(session):
    session._client.events.put(_end("error"))
    session._client.events.put(_end("error"))
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    assert _raw_options(session) == [_ANDROID, _EMBEDDED, _COOKIED]


def test_load_remembers_the_attempt_that_actually_played(session):
    session._client.events.put(_end("error"))
    session._client.events.put(_end("error"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert session.strategy == "web_safari"


def test_load_tries_the_remembered_attempt_first(session_factory):
    session = session_factory(strategy="web_safari")
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    # 기억한 전략이 앞으로 오고, 나머지는 원래 순서를 지킨다
    assert _raw_options(session) == [_COOKIED]
    session._client.events.put(_end("error"))
    session._client.events.put(_end("error"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert _raw_options(session)[1:] == [_COOKIED, _ANDROID, _EMBEDDED]


def test_remembered_attempt_that_stops_working_self_heals(session_factory):
    """쿠키가 만료돼 폴백이 죽으면 익명 경로로 되돌아가고 그걸 다시 기억한다."""
    session = session_factory(strategy="web_safari")
    session._client.events.put(_end("error"))
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    assert _raw_options(session) == [_COOKIED, _ANDROID]
    assert session.strategy == "android"


def test_unknown_remembered_strategy_falls_back_to_default_order(session_factory):
    session = session_factory(strategy="wat")
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert _raw_options(session) == [_ANDROID]


def test_quitting_before_anything_plays_does_not_record_a_strategy(session_factory):
    session = session_factory(strategy="android")
    session._client.events.put(_end("error"))
    session._client.events.put(_key("q"))
    session._client.events.put(_end("quit"))
    assert session.load(URL) == "quit"
    assert session.strategy == "android"   # 아무것도 열리지 않았으므로 그대로


def test_strategy_is_recorded_when_the_stream_opened_then_user_quit(session_factory):
    session = session_factory(strategy="android")
    session._client.events.put(_end("error"))
    session._client.events.put(_prop("duration", 210.0))
    session._client.events.put(_key("q"))
    session._client.events.put(_end("quit"))
    session.load(URL)
    assert session.strategy == "web_embedded"


# --- 카드에 띄울 메타데이터와 안내 슬롯 ---

def test_set_track_seeds_the_title_before_mpv_reports_one(session):
    session.set_track("침착맨 삼국지", "침착맨", 18379.0)
    assert session.title == "침착맨 삼국지"
    assert session.channel == "침착맨"
    assert session.track_duration == 18379.0
    assert session._state()["title"] == "침착맨 삼국지"


def test_media_title_overwrites_the_seed_but_an_empty_one_does_not(session):
    session.set_track("검색 결과 제목", "채널", 100.0)
    session._on_property("media-title", "mpv가 준 제목")
    assert session.title == "mpv가 준 제목"
    session._on_property("media-title", "")
    assert session.title == "mpv가 준 제목"


def test_notice_appears_in_state_and_clears_on_the_first_position(session):
    session.set_notice("스트림 연결 중...")
    assert session._state()["notice"] == "스트림 연결 중..."
    session._on_property("time-pos", 1.0)
    assert session._state()["notice"] is None


def test_a_null_position_does_not_clear_the_notice(session):
    session.set_notice("스트림 연결 중...")
    session._on_property("time-pos", None)
    assert session._state()["notice"] == "스트림 연결 중..."


def test_a_flash_message_wins_over_a_standing_notice(session):
    session.set_notice("스트림 연결 중...")
    session._flash("자동재생을 껐습니다")
    assert session._state()["notice"] == "자동재생을 껐습니다"


def test_state_exposes_the_timecode_prompt_for_the_last_row(session):
    assert session._state()["prompt"] is None
    session._on_key("g")
    assert "이동할 시간" in session._state()["prompt"]


def test_mpv_errors_are_buffered_instead_of_printed(session):
    session._client.events.put({"event": "log-message", "prefix": "ffmpeg",
                                "text": "Server returned 403\n"})
    session._client.events.put(_end("error"))
    session._wait_end(show_errors=True)
    assert session.errors == ["[mpv] ffmpeg: Server returned 403"]


def test_mpv_errors_are_buffered_even_while_the_pager_is_open(session):
    session._modal = Pager(["댓글1"])
    session._client.events.put({"event": "log-message", "prefix": "ffmpeg",
                                "text": "Server returned 403\n"})
    session._client.events.put(_end("error"))
    session._wait_end(show_errors=True)
    assert session.errors == ["[mpv] ffmpeg: Server returned 403"]


# --- _state()가 tui.py 렌더러의 계약을 실제로 만족하는지 ---

def test_state_satisfies_the_render_playing_contract(session):
    # 채널명이 제목의 부분문자열이면 channel 키가 빠져도 title 쪽 단언이 통과해버려
    # 검증이 공허해진다 — 겹치지 않는 문자열을 쓴다. next_hint도 마찬가지로
    # title/channel과 겹치지 않는 문구를 쓴다.
    session.set_track("삼국지 리뷰", "침착맨", 18379.0)
    session.set_notice("연결 중")
    session.set_next_hint("고전 배틀 명장면 · 주인장")
    session._on_key("g")
    frame = render_playing(session._state(), 80, 24)
    assert len(frame) == 24
    joined = "\n".join(frame)
    assert "삼국지 리뷰" in joined
    assert "침착맨" in joined
    assert "연결 중" in joined
    assert "이동할 시간" in joined
    # _state()가 track_duration을 실어 나르는지 — mpv가 아직 duration을 보고하지
    # 않은 동안(스트림 열기 전) 카드에 뜨는 시간의 유일한 출처다.
    assert "5:06:19" in joined
    # _state()가 next_hint를 실어 나르는지 — README가 광고하는 "다음 곡" 표시.
    assert "고전 배틀 명장면 · 주인장" in joined


def test_state_satisfies_the_render_comments_contract(session):
    session.set_track("삼국지 리뷰", "침착맨", 18379.0)
    pager = Pager(["댓글1", "댓글2"])
    frame = render_comments(session._state(), pager, 80, 24)
    assert len(frame) == 24
    joined = "\n".join(frame)
    assert "삼국지 리뷰" in joined
    assert "침착맨" in joined


# --- p 키: 이전 곡 ---

def test_p_key_sends_stop_and_reports_prev(session):
    session.has_prev = True
    session._client.events.put(_key("p"))
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "prev"
    assert ("stop",) in session._client.commands


def test_p_key_without_a_previous_track_does_not_stop_playback(session):
    """체인의 첫 곡에서 p를 눌러도 스트림을 끊지 않는다 — 같은 곡을 다시 여는 데
    드는 스트림 재해석 몇 초를 쓸 이유가 없다."""
    session._client.events.put(_key("p"))
    session._client.events.put(_end("eof"))
    assert session.load(URL) == "eof"
    assert ("stop",) not in session._client.commands
    # mpv로도 넘기지 않는다 — mpv의 기본 p는 일시정지라, 넘기면 "이전"을 누른
    # 사용자에게 엉뚱하게 재생이 멈춘다.
    assert ("keypress", "p") not in session._client.commands


def test_p_key_without_a_previous_track_says_so(session):
    session._client.events.put(_key("p"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert session._message == "이전 곡이 없습니다"


def test_prev_request_does_not_leak_into_the_following_load(session):
    session.has_prev = True
    session._client.events.put(_key("p"))
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "prev"
    session._client.events.put(_end("stop"))
    assert session.load(URL) == "quit"


# --- 직접 스트림 (프리페치가 미리 풀어둔 googlevideo URL) ---

from player import Stream, resolve_stream

DIRECT = "https://rr1---sn-xyz.googlevideo.com/videoplayback?itag=140"


def _ydl(side_effects):
    """player.YoutubeDL을 patch. side_effects는 호출 순서대로 돌려줄 info dict 또는 예외."""
    patcher = patch("player.YoutubeDL")
    MockYDL = patcher.start()
    inst = MockYDL.return_value.__enter__.return_value
    inst.extract_info.side_effect = list(side_effects)
    return patcher, MockYDL


def _ydl_opts(MockYDL):
    return [c.args[0] for c in MockYDL.call_args_list]


def test_resolve_stream_returns_the_first_attempt_that_yields_a_url():
    patcher, MockYDL = _ydl([{"url": DIRECT, "duration": 617.0}])
    try:
        stream = resolve_stream(URL, None)
    finally:
        patcher.stop()
    assert stream == Stream(url=DIRECT, client="android", duration=617.0)
    opts = _ydl_opts(MockYDL)
    assert len(opts) == 1
    assert opts[0]["format"] == "bestaudio/best"
    assert opts[0]["extractor_args"] == {"youtube": {"player_client": ["android"]}}
    assert "cookiesfrombrowser" not in opts[0]


def test_resolve_stream_walks_the_attempt_chain_and_attaches_cookies_on_the_last_step():
    patcher, MockYDL = _ydl([RuntimeError("bot"), RuntimeError("bot"), {"url": DIRECT, "duration": 1}])
    try:
        stream = resolve_stream(URL, None)
    finally:
        patcher.stop()
    assert stream.client == "web_safari"
    opts = _ydl_opts(MockYDL)
    assert [o["extractor_args"]["youtube"]["player_client"] for o in opts] == [["android"], ["web_embedded"], ["web_safari"]]
    assert opts[2]["cookiesfrombrowser"] == ("chrome",)
    assert "cookiesfrombrowser" not in opts[0]


def test_resolve_stream_starts_from_the_remembered_strategy():
    patcher, MockYDL = _ydl([{"url": DIRECT}])
    try:
        stream = resolve_stream(URL, "web_safari")
    finally:
        patcher.stop()
    assert stream.client == "web_safari" and stream.duration == 0.0
    assert _ydl_opts(MockYDL)[0]["cookiesfrombrowser"] == ("chrome",)


def test_resolve_stream_gives_none_when_every_attempt_fails_or_has_no_url():
    patcher, _ = _ydl([RuntimeError("a"), {"formats": []}, RuntimeError("c")])
    try:
        assert resolve_stream(URL, None) is None
    finally:
        patcher.stop()


def _commands(session, name):
    return [c for c in session._client.commands if c[0] == name]


def test_load_with_a_stream_plays_the_direct_url_without_ytdl(session):
    session._client.events.put(_end("eof"))
    assert session.load(URL, stream=Stream(DIRECT, "android", 617.0)) == "eof"
    assert ("set", "ytdl", "no") in session._client.commands
    assert _commands(session, "loadfile") == [("loadfile", DIRECT)]
    assert _commands(session, "set") and all(c[1] != "ytdl-raw-options" for c in session._client.commands if c[0] == "set")
    assert session.strategy == "android"


def test_load_falls_back_to_the_normal_chain_when_the_direct_url_fails(session):
    session._client.events.put(_end("error"))      # 직접 URL (만료 등)
    session._client.events.put(_end("eof"))        # android 시도
    assert session.load(URL, stream=Stream(DIRECT, "web_safari", 0.0)) == "eof"
    assert _commands(session, "loadfile") == [("loadfile", DIRECT), ("loadfile", URL)]
    cmds = session._client.commands
    assert cmds.index(("set", "ytdl", "yes")) < cmds.index(("loadfile", URL))
    assert _raw_options(session) == [_ANDROID]    # 폴백은 기억한 전략이 아니라 기본 순서
    assert session.strategy == "android"


def test_load_with_a_stream_applies_the_resume_position(session):
    session._client.events.put(_end("eof"))
    session.load(URL, start=90.0, stream=Stream(DIRECT, "android", 0.0))
    assert ("set", "start", "90") in session._client.commands


def test_media_title_from_a_direct_url_does_not_overwrite_the_track_title(session):
    session.set_track("진짜 제목", "채널", 600.0)
    session._client.events.put(_prop("media-title", "videoplayback"))
    session._client.events.put(_end("eof"))
    session.load(URL, stream=Stream(DIRECT, "android", 0.0))
    assert session.title == "진짜 제목"


def test_media_title_is_still_taken_when_playing_through_ytdl(session):
    session.set_track("미리 넣은 제목", "채널", 600.0)
    session._client.events.put(_prop("media-title", "mpv가 준 제목"))
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert session.title == "mpv가 준 제목"


def test_load_without_a_stream_keeps_ytdl_on(session):
    session._client.events.put(_end("eof"))
    session.load(URL)
    assert ("set", "ytdl", "yes") in session._client.commands
    assert ("set", "ytdl", "no") not in session._client.commands


def test_resolve_stream_skips_cookie_attempts_when_declined():
    patcher, MockYDL = _ydl([RuntimeError("a"), RuntimeError("b"), {"url": DIRECT}])
    try:
        assert resolve_stream(URL, None, cookies=False) is None
    finally:
        patcher.stop()
    opts = _ydl_opts(MockYDL)
    assert len(opts) == 2 and not any("cookiesfrombrowser" in o for o in opts)


def test_long_comment_text_wraps_to_the_terminal_width_instead_of_being_cut():
    """80칸 터미널에서 한글 60자(120칸)는 두 줄로 접혀 전부 보여야 한다."""
    from tui import display_width
    with tty_session() as (s, out):
        pager = _open(s, ["     " + "가" * 60])
        s._redraw(force=True)
        assert pager.width == 80
        assert all(display_width(line) <= 80 for line in pager.lines)
        assert "".join(line.strip() for line in pager.lines).count("가") == 60


def test_pager_keys_see_the_reflowed_layout_before_the_first_redraw():
    with tty_session() as (s, out):
        pager = _open(s, ["     " + "가" * 60, "x"])
        s._modal_key("j")
        assert pager.width == 80 and pager.cursor == 1
