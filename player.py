from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

from yt_dlp import YoutubeDL

import debuglog
from comments import SORT_LABELS, CommentFeed, ReplyFeed, format_comment, format_header, next_sort
from mpv_ipc import MpvClient, MpvError
from ytdlp_common import SilentLogger
from tui import (KeyReader, LinePrompt, Pager, Screen, comments_height, enter_alt_screen,
                 exit_alt_screen, hide_cursor, parse_timecode, render_comments,
                 render_playing, show_cursor, terminal_mode, terminal_size)

_COOKIE_BROWSER = "chrome"
_log = debuglog.get("player")
# (player_client, 쿠키를 붙일지) 순서대로 시도한다.
#
# android가 익명으로 거의 모든 영상을 첫 시도에 열고(2~3초), web_embedded는 실패 후 폴백에
# 3~4초를 더 쓰는 경우가 많아 익명 두 칸을 먼저 둔다 (2026-09 측정).
#
# 마지막 칸은 브라우저 쿠키를 붙인 시도다. YouTube가 IP 단위로 player 엔드포인트에 봇 인증을
# 요구하기 시작하면 익명 요청은 yt-dlp 버전·player_client와 무관하게 전부
# "Sign in to confirm you're not a bot"으로 죽고, 쿠키를 붙이는 것만이 통한다 (2026-09 측정:
# 2025.10.14 / 2026.06.09 / 2026.07.04 / 2026.08.19 × 8개 클라이언트 전멸). 이때 쿠키와 함께
# 쓸 수 있는 클라이언트가 갈리는데 android/ios는 "No video formats found!", tv는 "The page
# needs to be reloaded"로 깨지고 web 계열만 살아남는다. 그중 가장 빨랐던 web_safari를 쓴다
# (7.3s vs web_embedded 8.1s / mweb 8.6s / web 9.7s).
_ATTEMPTS = (
    ("android", False),
    ("web_embedded", False),
    ("web_safari", True),
)
_OBSERVED = ("time-pos", "pause", "volume", "duration", "media-title")
_POSITION_INTERVAL = 10.0
_REDRAW_INTERVAL = 0.5
_MESSAGE_SECONDS = 3.0
_COMMENT_HINT = "j/k 이동 · Enter 답글 · s 정렬 · q 닫기"
_REPLY_HINT = "j/k 이동 · q 되돌아가기"
# 페이저를 채우는 이벤트와 그 실패 문구. 모두 generation과 pager identity 검사를 거친다.
_PAGER_FILL_EVENTS = ("comments-more", "comments-reload", "replies-ready", "replies-more")
_PAGER_FAILURES = {
    "comments-more-failed": "댓글을 더 불러오지 못했습니다",
    "comments-reload-failed": "정렬을 바꾸지 못했습니다",
    "replies-failed": "답글을 불러오지 못했습니다",
    "replies-more-failed": "답글을 더 불러오지 못했습니다",
}


class PlayerError(Exception):
    pass


def _socket_path() -> str:
    # 세션마다 다른 경로여야 한다. 고정 경로면 두 번째 yp가 첫 번째의 소켓 파일을 지운다.
    return os.path.join(tempfile.gettempdir(), f"yp_mpv_{os.getpid()}.sock")


def _raw_options(client: str, cookies: bool) -> str:
    """mpv의 ytdl-raw-options 문자열. 콤마로 나뉜 key=value 목록이다."""
    opts = f"extractor-args=youtube:player_client={client}"
    if cookies:
        opts += f",cookies-from-browser={_COOKIE_BROWSER}"
    return opts


def _attempt_order(preferred: str | None, cookies: bool = True) -> list[tuple[str, bool]]:
    """지난번에 통한 시도를 맨 앞으로 돌리고, 나머지는 _ATTEMPTS 순서를 지킨다.
    cookies=False면 쿠키를 붙이는 칸을 뺀다 — 사용자가 시청 기록 노출을 거절한 경우다.

    차단이 걸린 상태에서 매 영상마다 익명 시도 두 칸을 버리지 않기 위한 것이다 (mpv를
    거친 실측 약 5.7초 — yt-dlp 자체 실패는 3.4초고 나머지는 mpv가 시도마다 ytdl_hook을
    다시 띄우는 비용). preferred가 지금은 없는 이름이면 그냥 기본 순서로 돈다."""
    attempts = [a for a in _ATTEMPTS if cookies or not a[1]]
    front = [a for a in attempts if a[0] == preferred]
    return front + [a for a in attempts if a[0] != preferred]


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


@dataclass(frozen=True)
class Stream:
    """미리 풀어둔 오디오 스트림. url은 googlevideo 직접 주소라 mpv가 ytdl_hook 없이 연다
    (~0.5초 vs YouTube URL ~1.9초). client는 그 URL을 낸 player_client — 성공 시 strategy로 기억."""
    url: str
    client: str
    duration: float = 0.0


def resolve_stream(url: str, strategy: str | None, cookies: bool = True) -> Stream | None:
    """load()와 같은 _attempt_order를 걷어 직접 URL을 푼다. mpv를 거치지 않아 실패한 시도가
    곡 사이 공백으로 드러나지 않는다. 어떤 예외도 삼켜 None — 그러면 load()가 예전 경로로 간다."""
    for client, use_cookies in _attempt_order(strategy, cookies):
        opts = {
            "quiet": True, "no_warnings": True, "logger": SilentLogger(),
            "format": "bestaudio/best",
            "extractor_args": {"youtube": {"player_client": [client]}},
        }
        if use_cookies:
            opts["cookiesfrombrowser"] = (_COOKIE_BROWSER,)
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            _log.debug("스트림 사전 해석 실패 client=%s cookies=%s", client, use_cookies, exc_info=True)
            continue
        direct = (info or {}).get("url")
        if direct:
            return Stream(url=direct, client=client, duration=float(info.get("duration") or 0.0))
    return None


def _ytdlp_path() -> str:
    """Prefer a standalone Homebrew-managed yt-dlp, which the user keeps up to
    date via `brew upgrade` independently of yp's release cadence -- YouTube
    extraction breaks and gets patched upstream often enough that this matters.
    Fall back to the yt-dlp bundled alongside this interpreter (e.g. Homebrew's
    yp venv), which is pinned at yp's build time and can lag behind."""
    for brew_path in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
        if os.path.exists(brew_path):
            return brew_path
    bundled = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.exists(bundled):
        return bundled
    return "yt-dlp"


class PlayerSession:
    """재생 세션 하나 = mpv 프로세스 하나.

    mpv는 --no-terminal --idle=yes로 소리만 내고, 키 입력·상태줄·댓글 페이저는 Python이
    맡는다. MpvClient의 이벤트 큐 하나에 mpv 이벤트(end-file, property-change, log-message),
    KeyReader의 키 이벤트, 댓글 스레드의 완료 이벤트가 모두 들어오고 load()가 그 큐를
    소비한다. 상태가 한곳에 있어야 'n 뒤에 오는 end-file stop은 next다' 같은 해석이 안전하다.
    """

    def __init__(self, volume: int = 100, autoplay: bool = True, tty: bool = True,
                 strategy: str | None = None, cookies: bool = True):
        self.volume = volume
        self.autoplay = autoplay
        self.cookies = cookies
        # 돌아갈 이전 곡이 있는지. 재생목록은 yp가 쥐고 있으므로 player는 이 불리언
        # 하나만 본다. 매 load() 전에 yp가 세운다.
        self.has_prev = False
        # 지난 세션에서 실제로 스트림을 연 시도의 player_client. load()가 갱신하고
        # yp가 state.json에 넣는다.
        self.strategy = strategy
        self.position = 0.0
        self.duration = 0.0
        self.paused = False
        self.title = ""
        # yp가 재생 전에 아는 메타데이터. mpv의 media-title은 스트림이 열린 뒤에야
        # 오므로, 이게 없으면 '스트림 연결 중' 몇 초 동안 카드가 비어 있다.
        self.channel = ""
        self.track_duration = 0.0
        # 대체 화면 안에서 찍으면 나갈 때 사라진다. 여기 모아뒀다가 yp가 화면을
        # 나온 뒤 출력한다.
        self.errors: list[str] = []
        self.closed = False
        self._tty = tty
        self._client: MpvClient | None = None
        self._proc: subprocess.Popen | None = None
        self._keys: KeyReader | None = None
        self._term = None
        self._url = ""
        # 직접 URL 재생 중이면 mpv의 media-title은 'videoplayback' 같은 파일명이라 무시해야 한다.
        self._direct = False
        self._next_hint: str | None = None
        self._message: str | None = None
        self._message_until = 0.0
        # _message(3초 후 소멸)와 달리 스트림이 열릴 때까지 유지되는 안내.
        self._notice: str | None = None
        self._modal: Pager | LinePrompt | None = None
        self._next_requested = False
        self._prev_requested = False
        self._quit_requested = False
        self._position_cb: Callable[[float], None] | None = None
        self._last_position_cb = 0.0
        self._last_draw = 0.0
        self._comments_thread: threading.Thread | None = None
        self._comments_generation = 0
        self._feed: CommentFeed | None = None
        # 답글을 보는 동안 루트 페이저를 보관한다 (스크롤·커서 보존). None이면 루트 화면.
        self._parent_pager: Pager | None = None
        self._reply_feed: ReplyFeed | None = None
        self._screen = Screen(sys.stdout)
        self._in_screen = False
        self._load_generation = 0
        self._socket = _socket_path()

    # ----- lifecycle -----

    def start(self) -> None:
        self._remove_socket()
        self._proc = subprocess.Popen(
            ["mpv", "--no-video", "--no-terminal", "--idle=yes",
             "--ytdl-format=bestaudio/best",
             f"--script-opts=ytdl_hook-ytdl_path={_ytdlp_path()}",
             f"--volume={self.volume}",
             f"--input-ipc-server={self._socket}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._client = MpvClient(self._socket)
        try:
            self._client.connect(timeout=5.0)
            for index, name in enumerate(_OBSERVED, start=1):
                self._client.observe(index, name)
            self._client.request_log_messages("error")
        except MpvError as e:
            # 아직 아무것도 재생하지 않은 idle mpv라 얌전히 기다릴 이유가 없다.
            # 오류 메시지 뒤에 3초가 붙는 것을 막으려고 바로 죽인다.
            self._terminate_process(kill=True)
            raise PlayerError(str(e))
        if self._tty:
            self._term = terminal_mode()
            self._term.__enter__()
            self._keys = KeyReader(sys.stdin.fileno(), self._client.events)
            self._keys.start()
            # 화면 진입은 mpv 연결과 터미널 모드가 모두 성공한 뒤에 한다. 실패하면
            # PlayerError 메시지가 평범한 화면에 정상 출력돼야 한다.
            enter_alt_screen(sys.stdout)
            hide_cursor(sys.stdout)
            self._screen.reset()
            self._in_screen = True

    def quit(self) -> None:
        # 대체 화면 이탈은 여기 하나로 모인다. load()는 화면을 나가지 않으므로
        # 정상 종료든 Ctrl-C로 빠져나온 경로든 복구는 전부 여기서 일어난다. 멱등하므로
        # 두 번 불려도 안전하고, 셸이 대체 화면에 갇히는 것을 막는다.
        self._leave_screen()
        if self._keys is not None:
            self._keys.stop()
            self._keys = None
        if self._client is not None and not self._client.closed and not self.closed:
            try:
                self._client.command("quit", timeout=1.0)
            except MpvError:
                pass
        self._terminate_process()
        if self._client is not None:
            self._client.close()
        self._remove_socket()
        if self._term is not None:
            self._term.__exit__(None, None, None)
            self._term = None
        self.closed = True

    def _remove_socket(self) -> None:
        try:
            os.remove(self._socket)
        except FileNotFoundError:
            pass

    def _terminate_process(self, kill: bool = False) -> None:
        if self._proc is None:
            return
        if kill:
            self._proc.kill()
            self._proc.wait()
            return
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    # ----- playback -----

    def load(self, url: str, start: float | None = None, stream: Stream | None = None) -> str:
        """url을 끝까지 재생하고 끝난 사유를 돌려준다. stream(프리페치가 푼 직접 URL)이 있으면
        ytdl 없이 그것을 먼저 열고, 만료 등으로 실패하면 ytdl을 켜 YouTube URL 시도 체인으로 간다."""
        assert self._client is not None
        self._url = url
        self._next_requested = False
        self._prev_requested = False
        self.position = 0.0
        # duration도 비운다. 안 비우면 다음 영상이 로드에 실패했을 때 이전 영상의
        # 길이가 남아 그 값이 새 영상의 기록에 잘못 들어간다.
        self.duration = 0.0
        # 영상마다 세대를 올린다. 이전 영상의 댓글 스레드가 늦게 끝나 도착한
        # comments-ready가 다음 영상 위에 페이저를 여는 것을 막는 표식이다.
        self._load_generation += 1
        reason = "error"
        try:
            if stream is not None:
                reason = self._play_direct(stream, start)
                if reason != "error" or self.closed:
                    return reason
            attempts = _attempt_order(self.strategy, self.cookies)
            for index, (client, cookies) in enumerate(attempts):
                is_final = index == len(attempts) - 1
                try:
                    self._client.command("set", "ytdl", "yes")
                    self._client.command("set", "ytdl-raw-options",
                                         _raw_options(client, cookies))
                    self._client.command("set", "start", str(int(start)) if start else "none")
                    self._client.command("loadfile", url)
                except MpvError:
                    reason = "error"
                    break
                reason = self._wait_end(show_errors=is_final)
                # duration이 들어왔다는 것만이 스트림이 실제로 열렸다는 확실한 증거다.
                # 해석 중에 사용자가 q를 눌러 끝난 경우를 성공으로 기억하면 안 된다.
                if reason == "eof" or self.duration > 0:
                    self.strategy = client
                if reason != "error" or self.closed:
                    break
        finally:
            # 화면에서 나가지는 않는다. 자동재생 체인 전체가 한 화면 세션이라
            # 곡이 넘어갈 때 깜빡이지 않는다. 이탈은 quit()이 책임진다.
            self._modal = None
            self._parent_pager = None
            self._reply_feed = None
        return reason

    def _play_direct(self, stream: Stream, start: float | None) -> str:
        assert self._client is not None
        self._direct = True
        try:
            try:
                self._client.command("set", "ytdl", "no")
                self._client.command("set", "start", str(int(start)) if start else "none")
                self._client.command("loadfile", stream.url)
            except MpvError:
                return "error"
            reason = self._wait_end(show_errors=False)
            if reason == "eof" or self.duration > 0:
                self.strategy = stream.client
            return reason
        finally:
            self._direct = False

    def _wait_end(self, show_errors: bool) -> str:
        assert self._client is not None
        self._redraw(force=True)
        while True:
            try:
                ev = self._client.events.get(timeout=0.5)
            except queue.Empty:
                self._redraw()
                continue
            kind = ev.get("event")
            if kind == "mpv-exited":
                self.closed = True
                return "quit" if self._quit_requested else "error"
            if kind == "end-file":
                reason = ev.get("reason")
                if reason == "eof":
                    return "eof"
                if reason == "error":
                    return "error"
                if reason == "quit":
                    return "quit"
                if reason == "stop":
                    # mpv는 왜 멈췄는지 말해주지 않는다. 직전에 어떤 키가 눌렸는지로
                    # 의미를 정한다.
                    if self._next_requested:
                        return "next"
                    if self._prev_requested:
                        return "prev"
                    return "quit"
                continue  # redirect / unknown 은 무시
            if kind == "property-change":
                self._on_property(ev.get("name"), ev.get("data"))
            elif kind == "log-message":
                if show_errors:
                    self.errors.append(
                        f"[mpv] {ev.get('prefix')}: {str(ev.get('text', '')).rstrip()}")
            elif kind == "key":
                self._on_key(str(ev.get("key")))
            elif kind == "comments-ready":
                if ev.get("generation") != self._load_generation:
                    continue  # 이전 영상의 댓글 — 버린다 (큐를 비우면 mpv-exited를 잃는다)
                self._open_pager(ev)
            elif kind == "comments-failed":
                if ev.get("generation") != self._load_generation:
                    continue
                self._flash("댓글을 불러올 수 없습니다")
            elif kind in _PAGER_FILL_EVENTS:
                if not self._event_for_current_pager(ev):
                    continue
                self._fill_pager(ev, replace=kind == "comments-reload")
            elif kind in _PAGER_FAILURES:
                if not self._event_for_current_pager(ev):
                    continue
                self._show_pager_status(_PAGER_FAILURES[kind], more=False)
            elif kind == "redraw":
                self._redraw(force=True)
                continue
            self._redraw()

    def _on_property(self, name, data) -> None:
        if name == "time-pos":
            if data is None:
                return  # 파일 종료 직전에 오는 null은 마지막 위치를 지우지 않도록 무시
            # 소리가 나기 시작했다 = '스트림 연결 중' 류의 안내는 역할이 끝났다.
            self._notice = None
            self.position = float(data)
            now = time.monotonic()
            if self._position_cb is not None and now - self._last_position_cb >= _POSITION_INTERVAL:
                self._last_position_cb = now
                self._position_cb(self.position)
        elif name == "duration":
            self.duration = float(data or 0)
        elif name == "pause":
            self.paused = bool(data)
        elif name == "volume":
            if data is not None:
                self.volume = int(round(float(data)))
        elif name == "media-title":
            # 빈 값이나 직접 URL의 파일명('videoplayback')으로 set_track의 제목을 덮어쓰면 안 된다.
            if data and not self._direct:
                self.title = str(data)

    def on_position(self, callback: Callable[[float], None]) -> None:
        self._position_cb = callback
        self._last_position_cb = 0.0

    def set_next_hint(self, text: str | None) -> None:
        """prefetch 스레드에서 호출된다. 화면 출력은 메인 루프만 하므로 redraw 이벤트로 넘긴다."""
        self._next_hint = text
        if self._client is not None:
            self._client.events.put({"event": "redraw"})

    def set_track(self, title: str, channel: str | None = None,
                  duration: float | None = None) -> None:
        """재생 전에 아는 메타데이터를 카드에 미리 올린다. 메인 스레드에서만 부른다."""
        self.title = title or ""
        self.channel = channel or ""
        self.track_duration = float(duration or 0)
        self._redraw(force=True)

    def set_notice(self, text: str | None) -> None:
        """안내 슬롯에 문구를 세운다. time-pos가 올 때마다(첫 번째뿐 아니라) 지워진다.

        지금은 이게 안전하다 — 안내는 스트림이 열리기 전이나 곡과 곡 사이에만
        세워지고, 그 동안은 mpv가 아직 time-pos를 보내지 않기 때문이다. 하지만
        재생 '중'에 안내를 세우는 호출이 생기면 다음 time-pos 틱(~0.5초)에 바로
        지워진다 — 최초 1회만 지우도록 플래그를 두는 건 지금은 쓸 곳이 없어
        미룬 복잡도다.

        set_next_hint와 달리 이벤트 큐를 거치지 않고 바로 그린다. 곡과 곡 사이
        ('다음 영상을 찾는 중...')에는 _wait_end의 루프가 돌지 않아 큐에 넣어봐야
        아무도 꺼내주지 않기 때문이다. 그래서 메인 스레드 전용이다.
        """
        self._notice = text
        self._redraw(force=True)

    # ----- keys -----

    def _on_key(self, key: str) -> None:
        if self._modal is not None:
            self._modal_key(key)
            return
        if key == "q":
            self._quit_requested = True
            self._send("quit", timeout=1.0)
        elif key == "n":
            self._next_requested = True
            self._send("stop")
        elif key == "p":
            # mpv의 기본 p는 일시정지다. 돌아갈 곳이 없어도 mpv로 넘기지 않고
            # 삼켜야 "이전"을 누른 사용자에게 엉뚱하게 재생이 멈추지 않는다.
            if not self.has_prev:
                self._flash("이전 곡이 없습니다")
                return
            self._prev_requested = True
            self._send("stop")
        elif key == "a":
            self.autoplay = not self.autoplay
            self._flash("자동재생을 " + ("켰습니다" if self.autoplay else "껐습니다"))
        elif key == "g":
            self._modal = LinePrompt("이동할 시간 (0710 → 7:10 / 012930 → 1:29:30): ")
            self._redraw(force=True)
        elif key == "t":
            self._request_comments()
        else:
            self._send("keypress", key)

    def _send(self, *args, timeout: float = 5.0) -> None:
        assert self._client is not None
        try:
            self._client.command(*args, timeout=timeout)
        except MpvError:
            pass

    def _modal_key(self, key: str) -> None:
        modal = self._modal
        if isinstance(modal, Pager):
            cols, rows = terminal_size()
            height = comments_height(rows)
            modal.reflow(cols, height)
            in_replies = self._parent_pager is not None
            if key == "s":
                if not in_replies and modal.status is None and self._feed is not None:
                    self._switch_comment_sort()
                return
            action = modal.handle_key(key, height)
            if action == "close":
                self._close_pager()
                return
            if action == "open":
                payload = modal.selected_payload() or {}
                if not in_replies and modal.status is None and payload.get("reply_token"):
                    self._open_replies(payload, modal.cursor + 1)
                return
            feed = self._reply_feed if in_replies else self._feed
            if modal.more and modal.status is None and feed is not None and modal.at_last_item():
                self._request_more(in_replies)
                return
            self._redraw(force=True)
            return
        if isinstance(modal, LinePrompt):
            state = modal.handle_key(key)
            if state == "pending":
                self._redraw(force=True)
                return
            self._modal = None
            if state == "submit":
                seconds = parse_timecode(modal.text)
                if seconds is None:
                    self._flash("잘못된 형식 (예: 0710, 012930)")
                else:
                    self._send("seek", str(seconds), "absolute")
            self._redraw(force=True)

    # ----- comments -----

    def _request_comments(self) -> None:
        if self._comments_thread is not None and self._comments_thread.is_alive():
            if self._comments_generation != self._load_generation:
                self._flash("이전 영상 댓글을 아직 불러오는 중입니다. 잠시 후 다시 눌러주세요")
            else:
                self._flash("댓글 불러오는 중...")
            return
        self._feed = CommentFeed(self._url)
        self._flash("댓글 불러오는 중...")
        self._fetch_page(self._feed, None, "comments-ready", "comments-failed")

    def _request_more(self, in_replies: bool) -> None:
        """커서가 마지막 블록에 닿았을 때 다음 페이지를 요청한다. status가 중복 요청을 막는다."""
        pager = self._modal
        if in_replies:
            self._show_pager_status("다음 답글 불러오는 중...")
            self._fetch_page(self._reply_feed, pager, "replies-more", "replies-more-failed", reply=True)
        else:
            self._show_pager_status("다음 댓글 불러오는 중...")
            self._fetch_page(self._feed, pager, "comments-more", "comments-more-failed")

    def _switch_comment_sort(self) -> None:
        """정렬을 토글한다. 순서가 통째로 달라지므로 목록을 처음부터 다시 만든다.
        이미 배운 정렬 토큰을 넘겨 watch 페이지를 다시 받지 않는다."""
        assert self._feed is not None
        sort = next_sort(self._feed.sort)
        self._feed = CommentFeed(self._url, sort=sort, sort_tokens=self._feed.sort_tokens)
        self._show_pager_status(f"{SORT_LABELS[sort]}으로 다시 불러오는 중...")
        self._fetch_page(self._feed, self._modal, "comments-reload", "comments-reload-failed")

    def _open_replies(self, payload: dict, index: int) -> None:
        """루트 페이저를 보관하고 답글 페이저를 그 위에 올린다. 첫 페이지는 비동기로 채워진다."""
        pager = Pager(header=format_comment(payload, index) + ["─" * 44], hint=_REPLY_HINT)
        pager.status = "답글 불러오는 중..."
        self._parent_pager, self._modal = self._modal, pager
        self._reply_feed = ReplyFeed(payload["reply_token"])
        self._fetch_page(self._reply_feed, pager, "replies-ready", "replies-failed", reply=True)
        self._redraw(force=True)

    def _close_pager(self) -> None:
        """답글 화면이면 루트 페이저로, 루트면 카드로. 화면에는 남아 있는다 — 그림만 바뀐다."""
        if self._parent_pager is not None:
            self._modal, self._parent_pager, self._reply_feed = self._parent_pager, None, None
        else:
            self._modal = None
        self._redraw(force=True)

    def _fetch_page(self, feed, pager: Pager | None, ok_event: str, fail_event: str,
                    reply: bool = False) -> None:
        """feed의 다음 페이지를 워커 스레드에서 받아 블록으로 만들어 이벤트 큐에 넣는다.

        pager는 응답이 돌아갈 페이저다 (첫 루트 페이지는 아직 없어 None). 수신 측이
        `self._modal is ev["pager"]`로 확인해 닫힌 페이저로 온 응답을 버린다.
        """
        assert self._client is not None and feed is not None
        title, events, gen = self.title or self._url, self._client.events, self._load_generation
        first_root = not reply and feed.shown == 0

        def worker():
            try:
                start = feed.shown + 1
                comments, more = feed.next_page()
                blocks = [format_comment(c, i, reply=reply) for i, c in enumerate(comments, start)]
                ev = {"event": ok_event, "blocks": blocks, "payloads": comments, "more": more,
                      "generation": gen, "pager": pager}
                if first_root:
                    if comments:
                        note = f"{SORT_LABELS[feed.sort]}  ·  s 키로 {SORT_LABELS[next_sort(feed.sort)]}"
                        ev["header"] = format_header(title, note)
                    else:
                        ev["header"] = [title, "", "댓글이 없습니다."]
                events.put(ev)
            except Exception:
                _log.debug("댓글 조회 실패 (%s)", fail_event, exc_info=True)
                events.put({"event": fail_event, "generation": gen, "pager": pager})

        self._comments_generation = gen
        self._comments_thread = threading.Thread(target=worker, daemon=True)
        self._comments_thread.start()

    def _event_for_current_pager(self, ev: dict) -> bool:
        return ev.get("generation") == self._load_generation and ev.get("pager") is self._modal

    def _fill_pager(self, ev: dict, replace: bool) -> None:
        pager = self._modal
        if not isinstance(pager, Pager):
            return
        blocks, payloads = ev.get("blocks") or [], ev.get("payloads") or []
        if replace:
            pager.replace_items(ev.get("header") or pager.header, blocks, payloads)
        else:
            pager.add_items(blocks, payloads)
        pager.status = None
        pager.more = bool(ev.get("more"))
        self._redraw(force=True)

    def _show_pager_status(self, text: str, more: bool | None = None) -> None:
        pager = self._modal
        if not isinstance(pager, Pager):
            return
        pager.status = text
        if more is not None:
            pager.more = more
        self._redraw(force=True)

    def _open_pager(self, ev: dict) -> None:
        if not self._tty:
            return
        # 이미 대체 화면 안이므로 enter_alt_screen을 다시 부르지 않는다.
        pager = Pager(header=ev.get("header") or [], more=bool(ev.get("more")), hint=_COMMENT_HINT)
        pager.add_items(ev.get("blocks") or [], ev.get("payloads") or [])
        self._modal = pager
        self._redraw(force=True)

    # ----- screen -----

    def _flash(self, text: str) -> None:
        self._message = text
        self._message_until = time.monotonic() + _MESSAGE_SECONDS
        self._redraw(force=True)

    def _state(self) -> dict:
        if self._message and time.monotonic() > self._message_until:
            self._message = None
        prompt = self._modal.render() if isinstance(self._modal, LinePrompt) else None
        # notice가 message를 우선하므로 렌더러는 슬롯 하나만 보면 된다. message 키는
        # 하위 호환을 위해 남긴다.
        return {"position": self.position, "duration": self.duration, "paused": self.paused,
                "volume": self.volume, "autoplay": self.autoplay,
                "next_hint": self._next_hint, "message": self._message,
                "title": self.title, "channel": self.channel,
                "track_duration": self.track_duration,
                "notice": self._message or self._notice, "prompt": prompt}

    def _redraw(self, force: bool = False) -> None:
        # 모달이 열려 있어도 그린다. 댓글 패널 위쪽에 진행바가 남아, 읽는 동안에도
        # 시간이 흐르는 게 보이는 것이 이 화면의 존재 이유다.
        if not self._tty or not self._in_screen:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < _REDRAW_INTERVAL:
            return
        self._last_draw = now
        cols, rows = terminal_size()
        state = self._state()
        if isinstance(self._modal, Pager):
            self._modal.reflow(cols, comments_height(rows))
            lines = render_comments(state, self._modal, cols, rows)
        else:
            # LinePrompt는 카드를 덮지 않는다. 마지막 행만 프롬프트가 차지한다.
            lines = render_playing(state, cols, rows)
        self._screen.draw(lines, cols, rows)

    def _leave_screen(self) -> None:
        """대체 화면에서 나가 원래 터미널을 복구한다. 멱등하다."""
        self._modal = None
        if self._in_screen:
            show_cursor(sys.stdout)
            exit_alt_screen(sys.stdout)
            self._in_screen = False
