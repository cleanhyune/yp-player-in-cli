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


_BOLD, _DIM, _RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
# 두 문자의 East Asian Width 등급이 같아야 한다(둘 다 A). 등급이 섞이면
# ambiguous를 2칸으로 렌더하는 터미널(한국어/일본어 로케일에서 흔함)에서 채운 칸만
# 넓어져, 재생이 진행될수록 막대의 실제 폭이 자라고 프레임이 깨진다.
_BAR_FULL, _BAR_EMPTY = "█", "▒"


def styles_enabled() -> bool:
    """비-tty에서는 애초에 화면을 그리지 않으므로 NO_COLOR만 보면 된다."""
    return os.environ.get("NO_COLOR") is None


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}" if styles_enabled() else text


def dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}" if styles_enabled() else text


def _fit(text: str, width: int) -> str:
    """평문을 width에 맞춰 자르고 오른쪽을 공백으로 채운다.

    스타일은 반드시 이 뒤에 입힌다. display_width()가 ANSI를 셀 줄 모르기 때문이다.
    """
    if width <= 0:
        return ""
    text = truncate(text, width)
    return text + " " * max(0, width - display_width(text))


def progress_bar(position: float, duration: float, width: int) -> str:
    """채운 칸은 기본색, 남은 칸은 dim. 길이를 모르면(duration<=0) 전부 남은 칸."""
    if width <= 0:
        return ""
    if duration <= 0:
        return dim(_BAR_EMPTY * width)
    ratio = min(max(position / duration, 0.0), 1.0)
    filled = int(round(ratio * width))
    if filled <= 0:
        return dim(_BAR_EMPTY * width)
    if filled >= width:
        return _BAR_FULL * width
    return _BAR_FULL * filled + dim(_BAR_EMPTY * (width - filled))


def format_status(state: dict, width: int) -> str:
    """제어줄: 재생 상태·시간·볼륨·자동재생·일시 메시지. 다음 영상 힌트는 별도 줄(format_next_line)."""
    icon = "⏸" if state.get("paused") else "▶"
    pos = format_duration(state.get("position") or 0)
    dur = format_duration(state.get("duration") or 0)
    parts = [
        f"{icon} {pos} / {dur}",
        f"vol {int(state.get('volume') or 0)}",
        "자동재생 " + ("켜짐" if state.get("autoplay") else "꺼짐"),
    ]
    if state.get("message"):
        parts.append(str(state["message"]))
    return truncate("  ".join(parts), width)


def format_next_line(hint: str, width: int) -> str:
    return truncate(f"다음: {hint}", width)


def format_status_lines(state: dict, width: int) -> list[str]:
    """상태 영역 전체. 자동재생이 켜져 있고 다음 영상이 정해졌으면 둘째 줄에 힌트가 폭 전체를 쓴다."""
    lines = [format_status(state, width)]
    if state.get("autoplay") and state.get("next_hint"):
        lines.append(format_next_line(str(state["next_hint"]), width))
    return lines


class Pager:
    DEFAULT_HINT = "j/k 스크롤 · space 페이지 · q 닫기"

    def __init__(self, lines: list[str], more: bool = False, hint: str | None = None):
        self.lines = lines
        self.top = 0
        self.hint = hint or self.DEFAULT_HINT
        # more: 뒤에 더 불러올 페이지가 있을 수 있음. status: 푸터에 띄울 한 줄
        # (요청 중/실패). status가 None이 아니면 소유자는 새 요청을 보내지 않는다.
        self.more = more
        self.status: str | None = None

    def visible(self, height: int) -> list[str]:
        return self.lines[self.top:self.top + height]

    def append(self, lines: list[str]) -> None:
        """스크롤 위치를 유지한 채 뒤에 줄을 이어붙인다."""
        self.lines.extend(lines)

    def at_bottom(self, height: int) -> bool:
        return self.top >= max(0, len(self.lines) - height)

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
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=self._poll * 2)

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
            while not self._stop.is_set() and _ends_with_partial_escape(buf):
                # 이스케이프 시퀀스가 아직 끝나지 않았다: 다음 조각을 잠깐 기다려 이어붙인다.
                try:
                    ready, _, _ = select.select([self._fd], [], [], 0.05)
                except (OSError, ValueError):
                    return
                if not ready:
                    break
                try:
                    more = os.read(self._fd, 64)
                except OSError:
                    return
                if not more:
                    break
                buf += more
            for key in parse_keys(buf):
                self._events.put({"event": "key", "key": key})


def terminal_size() -> os.terminal_size:
    return shutil.get_terminal_size((80, 24))


class StatusArea:
    """여러 줄 상태 영역. 마지막으로 그린 줄 수를 기억해 다시 그릴 때 같은 자리에 덮어쓴다.

    그린 뒤 커서는 마지막 줄 끝에 남는다. 다시 그릴 땐 커서를 (줄 수 - 1)만큼 올려 첫 줄부터
    덮어쓰고, 줄 수가 줄었으면 남은 줄을 지운 뒤 커서를 새 마지막 줄로 되돌린다.
    clear()는 영역을 전부 지우고 커서를 첫 줄 맨 앞에 두어 다음 print가 그 자리에 찍히게 한다.
    """

    def __init__(self, out=None):
        self._out = out if out is not None else sys.stdout
        self.lines_drawn = 0

    def draw(self, lines: list[str]) -> None:
        seq = ""
        if self.lines_drawn > 1:
            seq += f"\x1b[{self.lines_drawn - 1}A"
        for index, line in enumerate(lines):
            if index:
                seq += "\n"
            seq += "\r\x1b[2K" + line
        extra = self.lines_drawn - len(lines)
        if extra > 0:
            seq += "\n\r\x1b[2K" * extra
            seq += f"\x1b[{extra}A"
        self._out.write(seq)
        self._out.flush()
        self.lines_drawn = len(lines)

    def clear(self) -> None:
        if self.lines_drawn == 0:
            return
        seq = ""
        if self.lines_drawn > 1:
            seq += f"\x1b[{self.lines_drawn - 1}A"
        seq += "\r\x1b[2K"
        seq += "\n\r\x1b[2K" * (self.lines_drawn - 1)
        if self.lines_drawn > 1:
            seq += f"\x1b[{self.lines_drawn - 1}A"
        seq += "\r"
        self._out.write(seq)
        self._out.flush()
        self.lines_drawn = 0


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
    hint = pager.status or pager.hint
    footer = f"-- {pager.top + 1}-{last}/{len(pager.lines)}  {hint} --"
    out.write("\x1b[2J\x1b[H" + "\r\n".join(body) + "\r\n" + truncate(footer, cols))
    out.flush()
