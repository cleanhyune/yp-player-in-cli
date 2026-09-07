from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from comments import fetch_comments, format_comments
from mpv_ipc import MpvClient, MpvError
from tui import (KeyReader, LinePrompt, Pager, StatusArea, draw_pager, enter_alt_screen,
                 exit_alt_screen, format_status_lines, parse_timecode, terminal_mode,
                 terminal_size)

_IPC_SOCKET = "/tmp/yp_mpv_socket"
_PLAYER_CLIENTS = ("web_embedded", "android")
_OBSERVED = ("time-pos", "pause", "volume", "duration", "media-title")
_POSITION_INTERVAL = 10.0
_REDRAW_INTERVAL = 0.5
_MESSAGE_SECONDS = 3.0


class PlayerError(Exception):
    pass


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


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

    def __init__(self, volume: int = 100, autoplay: bool = True, tty: bool = True):
        self.volume = volume
        self.autoplay = autoplay
        self.position = 0.0
        self.duration = 0.0
        self.paused = False
        self.title = ""
        self.closed = False
        self._tty = tty
        self._client: MpvClient | None = None
        self._proc: subprocess.Popen | None = None
        self._keys: KeyReader | None = None
        self._term = None
        self._url = ""
        self._next_hint: str | None = None
        self._message: str | None = None
        self._message_until = 0.0
        self._modal: Pager | LinePrompt | None = None
        self._next_requested = False
        self._quit_requested = False
        self._position_cb: Callable[[float], None] | None = None
        self._last_position_cb = 0.0
        self._last_draw = 0.0
        self._comments_thread: threading.Thread | None = None
        self._comments_generation = 0
        self._status = StatusArea(sys.stdout)
        self._load_generation = 0

    # ----- lifecycle -----

    def start(self) -> None:
        try:
            os.remove(_IPC_SOCKET)
        except FileNotFoundError:
            pass
        self._proc = subprocess.Popen(
            ["mpv", "--no-video", "--no-terminal", "--idle=yes",
             "--ytdl-format=bestaudio/best",
             f"--script-opts=ytdl_hook-ytdl_path={_ytdlp_path()}",
             f"--volume={self.volume}",
             f"--input-ipc-server={_IPC_SOCKET}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._client = MpvClient(_IPC_SOCKET)
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

    def quit(self) -> None:
        # load()가 예외로 빠져나갔으면 대체 화면이 아직 켜져 있을 수 있다. 멱등하므로
        # 정상 경로에서 두 번 불려도 안전하고, 셸이 대체 화면에 갇히는 것을 막는다.
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
        if self._term is not None:
            self._term.__exit__(None, None, None)
            self._term = None
        self.closed = True

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

    def load(self, url: str, start: float | None = None) -> str:
        assert self._client is not None
        self._url = url
        self._next_requested = False
        self.position = 0.0
        # duration도 비운다. 안 비우면 다음 영상이 로드에 실패했을 때 이전 영상의
        # 길이가 남아 그 값이 새 영상의 기록에 잘못 들어간다.
        self.duration = 0.0
        # 영상마다 세대를 올린다. 이전 영상의 댓글 스레드가 늦게 끝나 도착한
        # comments-ready가 다음 영상 위에 페이저를 여는 것을 막는 표식이다.
        self._load_generation += 1
        reason = "error"
        try:
            for index, client in enumerate(_PLAYER_CLIENTS):
                is_final = index == len(_PLAYER_CLIENTS) - 1
                try:
                    self._client.command("set", "ytdl-raw-options",
                                         f"extractor-args=youtube:player_client={client}")
                    self._client.command("set", "start", str(int(start)) if start else "none")
                    self._client.command("loadfile", url)
                except MpvError:
                    reason = "error"
                    break
                reason = self._wait_end(show_errors=is_final)
                if reason != "error" or self.closed:
                    break
        finally:
            # 예외(Ctrl-C 포함)로 빠져나가도 대체 화면에서 반드시 나온다.
            self._leave_screen()
        return reason

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
                    return "next" if self._next_requested else "quit"
                continue  # redirect / unknown 은 무시
            if kind == "property-change":
                self._on_property(ev.get("name"), ev.get("data"))
            elif kind == "log-message":
                if show_errors and self._modal is None:
                    self._print_line(f"[mpv] {ev.get('prefix')}: {str(ev.get('text', '')).rstrip()}")
            elif kind == "key":
                self._on_key(str(ev.get("key")))
            elif kind == "comments-ready":
                if ev.get("generation") != self._load_generation:
                    continue  # 이전 영상의 댓글 — 버린다 (큐를 비우면 mpv-exited를 잃는다)
                self._open_pager(ev["lines"])
            elif kind == "comments-failed":
                if ev.get("generation") != self._load_generation:
                    continue
                self._flash("댓글을 불러올 수 없습니다")
            elif kind == "redraw":
                self._redraw(force=True)
                continue
            self._redraw()

    def _on_property(self, name, data) -> None:
        if name == "time-pos":
            if data is None:
                return  # 파일 종료 직전에 오는 null은 마지막 위치를 지우지 않도록 무시
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
            self.title = str(data or "")

    def on_position(self, callback: Callable[[float], None]) -> None:
        self._position_cb = callback
        self._last_position_cb = 0.0

    def set_next_hint(self, text: str | None) -> None:
        """prefetch 스레드에서 호출된다. 화면 출력은 메인 루프만 하므로 redraw 이벤트로 넘긴다."""
        self._next_hint = text
        if self._client is not None:
            self._client.events.put({"event": "redraw"})

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
        elif key == "a":
            self.autoplay = not self.autoplay
            self._flash("자동재생 " + ("켜짐" if self.autoplay else "꺼짐"))
        elif key == "g":
            self._modal = LinePrompt("이동할 시간 (0710 → 7:10 / 012930 → 1:29:30): ")
            if self._tty:
                self._status.draw([self._modal.render()])
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
            _, rows = terminal_size()
            if modal.handle_key(key, max(1, rows - 1)):
                self._modal = None
                if self._tty:
                    exit_alt_screen(sys.stdout)
                self._redraw(force=True)
            elif self._tty:
                draw_pager(modal, sys.stdout)
            return
        if isinstance(modal, LinePrompt):
            state = modal.handle_key(key)
            if state == "pending":
                if self._tty:
                    self._status.draw([modal.render()])
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
        assert self._client is not None
        url, title, events = self._url, self.title or self._url, self._client.events
        gen = self._load_generation

        def worker():
            try:
                comments = fetch_comments(url)
                lines = format_comments(comments, title) if comments else [title, "", "댓글이 없습니다."]
                events.put({"event": "comments-ready", "lines": lines, "generation": gen})
            except Exception:
                events.put({"event": "comments-failed", "generation": gen})

        self._flash("댓글 불러오는 중...")
        self._comments_generation = gen
        self._comments_thread = threading.Thread(target=worker, daemon=True)
        self._comments_thread.start()

    def _open_pager(self, lines: list[str]) -> None:
        if not self._tty:
            return
        self._modal = Pager(lines)
        enter_alt_screen(sys.stdout)
        draw_pager(self._modal, sys.stdout)

    # ----- screen -----

    def _flash(self, text: str) -> None:
        self._message = text
        self._message_until = time.monotonic() + _MESSAGE_SECONDS
        self._redraw(force=True)

    def _state(self) -> dict:
        if self._message and time.monotonic() > self._message_until:
            self._message = None
        return {"position": self.position, "duration": self.duration, "paused": self.paused,
                "volume": self.volume, "autoplay": self.autoplay,
                "next_hint": self._next_hint, "message": self._message}

    def _redraw(self, force: bool = False) -> None:
        if not self._tty or self._modal is not None:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < _REDRAW_INTERVAL:
            return
        self._last_draw = now
        cols, _ = terminal_size()
        self._status.draw(format_status_lines(self._state(), cols))

    def _print_line(self, text: str) -> None:
        if self._tty:
            self._status.clear()
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            self._redraw(force=True)
        else:
            print(text)

    def _leave_screen(self) -> None:
        """load()가 끝날 때 상태줄/페이저를 정리해 다음 print가 깨끗한 줄에 찍히게 한다."""
        if self._tty:
            if isinstance(self._modal, Pager):
                exit_alt_screen(sys.stdout)
            self._status.clear()
        self._modal = None
