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
    parts = [
        f"{icon} {pos} / {dur}",
        f"vol {int(state.get('volume') or 0)}",
        "자동재생 " + ("켜짐" if state.get("autoplay") else "꺼짐"),
    ]
    if state.get("autoplay") and state.get("next_hint"):
        parts.append(f"다음: {state['next_hint']}")
    if state.get("message"):
        parts.append(str(state["message"]))
    return truncate("  ".join(parts), width)


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
    if not digits.isdigit():
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
