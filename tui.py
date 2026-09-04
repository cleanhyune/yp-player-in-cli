from __future__ import annotations

import os
import select
import shutil
import sys
import termios
import threading
import tty
import unicodedata
from contextlib import contextmanager

from selector import format_duration

_ESCAPE_SEQUENCES = {
    b"\x1b[A": "UP", b"\x1b[B": "DOWN", b"\x1b[C": "RIGHT", b"\x1b[D": "LEFT",
    b"\x1bOA": "UP", b"\x1bOB": "DOWN", b"\x1bOC": "RIGHT", b"\x1bOD": "LEFT",
    b"\x1b[5~": "PGUP", b"\x1b[6~": "PGDWN",
    b"\x1b[H": "HOME", b"\x1b[F": "END", b"\x1b[1~": "HOME", b"\x1b[4~": "END",
}
_SINGLE_BYTES = {
    b" ": "SPACE", b"\r": "ENTER", b"\n": "ENTER",
    b"\x7f": "BS", b"\x08": "BS", b"\t": "TAB",
}


def parse_keys(buf: bytes) -> list[str]:
    """터미널에서 읽은 바이트 덩어리를 mpv 키 이름 목록으로 바꾼다."""
    keys: list[str] = []
    i = 0
    while i < len(buf):
        if buf[i:i + 1] == b"\x1b":
            for seq, name in _ESCAPE_SEQUENCES.items():
                if buf.startswith(seq, i):
                    keys.append(name)
                    i += len(seq)
                    break
            else:
                nxt = buf[i + 1:i + 2]
                if nxt == b"[":
                    # 알 수 없는 CSI 시퀀스: 파라미터/중간 바이트(0x20-0x3F)를
                    # 지나 최종 바이트(0x40-0x7E)까지 통째로 소비하고 아무 키도 내지 않는다.
                    j = i + 2
                    while j < len(buf) and 0x20 <= buf[j] <= 0x3F:
                        j += 1
                    if j < len(buf) and 0x40 <= buf[j] <= 0x7E:
                        j += 1
                    i = j
                elif nxt == b"O":
                    # 알 수 없는 SS3 시퀀스: ESC, O, 그 다음 바이트까지 소비한다.
                    i = min(i + 3, len(buf))
                else:
                    keys.append("ESC")
                    i += 1
            continue
        single = buf[i:i + 1]
        if single in _SINGLE_BYTES:
            keys.append(_SINGLE_BYTES[single])
            i += 1
            continue
        first = buf[i]
        length = 4 if first >= 0xF0 else 3 if first >= 0xE0 else 2 if first >= 0xC0 else 1
        try:
            keys.append(buf[i:i + length].decode("utf-8"))
        except UnicodeDecodeError:
            pass
        i += length
    return keys


def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    out = ""
    used = 0
    for ch in text:
        w = _char_width(ch)
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…"


def format_status(state: dict, width: int) -> str:
    icon = "⏸" if state.get("paused") else "▶"
    pos = format_duration(state.get("position") or 0)
    dur = format_duration(state.get("duration") or 0)
    fixed_parts = [
        f"{icon} {pos} / {dur}",
        f"vol {int(state.get('volume') or 0)}",
        "자동재생 " + ("켜짐" if state.get("autoplay") else "꺼짐"),
    ]
    if state.get("message"):
        fixed_parts.append(str(state["message"]))
    fixed_joined = "  ".join(fixed_parts)

    if state.get("autoplay") and state.get("next_hint"):
        remaining = width - display_width(fixed_joined) - len("  다음: ")
        if remaining >= 8:
            hint = truncate(str(state["next_hint"]), remaining)
            fixed_joined += f"  다음: {hint}"

    return truncate(fixed_joined, width)


class Pager:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.top = 0

    def visible(self, height: int) -> list[str]:
        return self.lines[self.top:self.top + height]

    def handle_key(self, key: str, height: int) -> bool:
        """키를 처리하고, 페이저를 닫아야 하면 True."""
        if key in ("q", "ESC"):
            return True
        max_top = max(0, len(self.lines) - height)
        if key in ("j", "DOWN"):
            self.top = min(self.top + 1, max_top)
        elif key in ("k", "UP"):
            self.top = max(self.top - 1, 0)
        elif key in ("SPACE", "PGDWN", "f"):
            self.top = min(self.top + height, max_top)
        elif key in ("PGUP", "b"):
            self.top = max(self.top - height, 0)
        elif key in ("g", "HOME"):
            self.top = 0
        elif key in ("G", "END"):
            self.top = max_top
        return False


class LinePrompt:
    def __init__(self, label: str):
        self.label = label
        self.text = ""

    def handle_key(self, key: str) -> str:
        if key == "ENTER":
            return "submit"
        if key == "ESC":
            return "cancel"
        if key == "BS":
            self.text = self.text[:-1]
        elif len(key) == 1:
            self.text += key
        return "pending"

    def render(self) -> str:
        return self.label + self.text


def parse_timecode(s: str) -> int | None:
    """4자리 = MMSS, 5~6자리 = (H)HMMSS. 콜론은 무시. 예: 0710 -> 430, 012930 -> 5370."""
    digits = s.strip().replace(":", "")
    if not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) == 4:
        digits = "00" + digits
    elif len(digits) == 5:
        digits = "0" + digits
    elif len(digits) != 6:
        return None
    h, m, sec = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    if m >= 60 or sec >= 60:
        return None
    return h * 3600 + m * 60 + sec


@contextmanager
def terminal_mode(fd: int | None = None):
    """cbreak 모드로 들어간다. raw가 아니라 cbreak인 이유는 Ctrl+C가 KeyboardInterrupt로
    살아 있어야 기존 '재생을 중단합니다' 흐름이 유지되기 때문이다."""
    if fd is None:
        fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _ends_with_partial_escape(buf: bytes) -> bool:
    """buf가 아직 끝나지 않은 이스케이프 시퀀스로 끝나는지 판단한다.

    읽기 경계가 CSI/SS3 시퀀스 중간에 떨어지면 다음 read를 기다려 이어붙여야
    시퀀스를 잃어버리지 않는다 (그렇지 않으면 최종 바이트만 평범한 키로 새어 나간다).
    """
    idx = buf.rfind(b"\x1b")
    if idx == -1:
        return False
    tail = buf[idx:]
    if tail == b"\x1b":
        return True
    second = tail[1:2]
    if second == b"[":
        j = 2
        while j < len(tail) and 0x20 <= tail[j] <= 0x3F:
            j += 1
        if j < len(tail) and 0x40 <= tail[j] <= 0x7E:
            return False
        return True
    if second == b"O":
        return len(tail) < 3
    return False


class KeyReader:
    """stdin을 select로 폴링해 키 이벤트 {"event": "key", "key": name}를 큐에 넣는 스레드."""

    def __init__(self, fd: int, events, poll_interval: float = 0.2):
        self._fd = fd
        self._events = events
        self._poll = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], self._poll)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                buf = os.read(self._fd, 64)
            except OSError:
                return
            if not buf:
                return
            while _ends_with_partial_escape(buf):
                # 이스케이프 시퀀스가 아직 끝나지 않았다: 다음 조각을 잠깐 기다려 이어붙인다.
                ready, _, _ = select.select([self._fd], [], [], 0.05)
                if not ready:
                    break
                more = os.read(self._fd, 64)
                if not more:
                    break
                buf += more
            for key in parse_keys(buf):
                self._events.put({"event": "key", "key": key})


def terminal_size() -> os.terminal_size:
    return shutil.get_terminal_size((80, 24))


def draw_status(text: str, out=sys.stdout) -> None:
    out.write("\r\x1b[2K" + text)
    out.flush()


def end_status(out=sys.stdout) -> None:
    """상태줄을 지운다. 줄바꿈을 넣지 않으므로 다음 print가 그 자리에 찍힌다."""
    out.write("\r\x1b[2K")
    out.flush()


def enter_alt_screen(out=sys.stdout) -> None:
    out.write("\x1b[?1049h\x1b[H")
    out.flush()


def exit_alt_screen(out=sys.stdout) -> None:
    out.write("\x1b[?1049l")
    out.flush()


def draw_pager(pager: Pager, out=sys.stdout) -> None:
    cols, rows = terminal_size()
    height = max(1, rows - 1)
    body = [truncate(line, cols) for line in pager.visible(height)]
    last = min(pager.top + height, len(pager.lines))
    footer = f"-- {pager.top + 1}-{last}/{len(pager.lines)}  j/k 스크롤 · space 페이지 · q 닫기 --"
    out.write("\x1b[2J\x1b[H" + "\r\n".join(body) + "\r\n" + truncate(footer, cols))
    out.flush()
