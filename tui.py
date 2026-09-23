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


_BOLD, _DIM, _ACCENT, _RESET = "\x1b[1m", "\x1b[2m", "\x1b[36m", "\x1b[0m"
# 한 막대 안의 문자는 East Asian Width 등급이 같아야 한다(━─● 모두 A, ▮▯ 모두 N).
# 등급이 섞이면 ambiguous를 2칸으로 렌더하는 터미널(한국어 로케일에서 흔함)에서 채운
# 칸만 넓어져, 재생이 진행될수록 막대의 실제 폭이 자라고 프레임이 깨진다.
_BAR_FULL, _BAR_EMPTY, _BAR_KNOB = "━", "─", "●"
_VOL_FULL, _VOL_EMPTY = "▮", "▯"


def styles_enabled() -> bool:
    """비-tty에서는 애초에 화면을 그리지 않으므로 NO_COLOR만 보면 된다.

    no-color.org 규약은 NO_COLOR가 non-empty 값일 때만 색을 끄도록 정의한다.
    빈 문자열(NO_COLOR="")은 "설정 안 됨"과 같이 취급해 스타일을 켠 채로 둔다.
    """
    return not os.environ.get("NO_COLOR", "")


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}" if styles_enabled() else text


def dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}" if styles_enabled() else text


def accent(text: str) -> str:
    """유일한 색. 터미널 기본 팔레트의 cyan 하나만 써서 테마와의 충돌을 최소로 둔다."""
    return f"{_ACCENT}{text}{_RESET}" if styles_enabled() else text


def wrap_line(line: str, width: int) -> list[str]:
    """표시 폭 기준으로 접는다. 공백이 있으면 거기서, 없으면(한글 장문) 칸 수로 끊는다.
    이어지는 줄은 원본의 앞 공백만큼 들여쓴다. 들여쓰기만으로 폭이 차면 접지 않는다.
    """
    indent = line[:len(line) - len(line.lstrip(" "))]
    body = line[len(indent):]
    room = width - display_width(indent)
    if room <= 0 or display_width(body) <= room:
        return [line]
    out = []
    while display_width(body) > room:
        used = cut = 0
        for i, ch in enumerate(body):
            w = _char_width(ch)
            if used + w > room:
                break
            used += w
            cut = i + 1
        if cut == 0:
            break
        space = body.rfind(" ", 0, cut + 1)
        if space > 0:
            out.append(indent + body[:space])
            body = body[space + 1:].lstrip(" ")
        else:
            out.append(indent + body[:cut])
            body = body[cut:]
    if body or not out:
        out.append(indent + body)
    return out


def _fit(text: str, width: int) -> str:
    """평문을 width에 맞춰 자르고 오른쪽을 공백으로 채운다.

    스타일은 반드시 이 뒤에 입힌다. display_width()가 ANSI를 셀 줄 모르기 때문이다.
    """
    if width <= 0:
        return ""
    text = truncate(text, width)
    return text + " " * max(0, width - display_width(text))


def progress_bar(position: float, duration: float, width: int) -> str:
    """채운 칸과 손잡이(●)는 강조색, 남은 칸은 dim. 길이를 모르면 손잡이 없이 전부 남은 칸."""
    if width <= 0:
        return ""
    if duration <= 0:
        return dim(_BAR_EMPTY * width)
    ratio = min(max(position / duration, 0.0), 1.0)
    knob = int(ratio * (width - 1) + 0.5)
    rest = width - knob - 1
    return accent(_BAR_FULL * knob + _BAR_KNOB) + (dim(_BAR_EMPTY * rest) if rest > 0 else "")


_CARD_MAX_WIDTH = 72       # 박스 전체 폭 상한 (테두리 포함)
_CARD_MIN_COLS = 44        # 이보다 좁으면 박스를 포기하고 압축형
_CARD_MIN_ROWS = 15        # 이보다 낮으면 박스를 포기하고 압축형
_KEY_HINT = "q 종료   p 이전   n 다음   a 자동재생   g 이동   t 댓글"


def _times(state: dict) -> tuple[str, str, float, float]:
    """(아이콘+현재시각, 전체시각, position, duration).

    duration은 mpv가 보고한 값을 우선하고, 아직 없으면 yp가 검색 결과에서 알고 있는
    track_duration으로 대신한다. 둘 다 없으면 0이고 화면엔 --:-- 이 뜬다.
    """
    pos = float(state.get("position") or 0)
    dur = float(state.get("duration") or 0) or float(state.get("track_duration") or 0)
    # "▶"(East Asian Width: A)와 일시정지 중 서로 바뀌어 그려지는 짝이다. "⏸"(N등급)를
    # 쓰면 등급이 갈라져, ambiguous를 2칸으로 렌더하는 터미널(한국어 로케일에서 흔함)에서
    # 토글할 때마다 이 줄의 폭이 흔들려 박스 오른쪽 테두리가 밀린다. "‖"는 A등급이라
    # "▶"와 짝이 맞는다 — 되돌리지 말 것.
    icon = "‖" if state.get("paused") else "▶"
    left = f"{icon} {format_duration(int(pos))}"
    right = format_duration(int(dur)) if dur > 0 else "--:--"
    return left, right, pos, dur


def _volume_gauge(volume: int, segments: int = 8) -> str:
    filled = min(segments, max(0, int(volume / 100 * segments + 0.5)))
    return _VOL_FULL * filled + _VOL_EMPTY * (segments - filled)


def _meta_text(state: dict) -> str:
    volume = int(state.get("volume") or 0)
    autoplay = "⟳ 자동재생 ON" if state.get("autoplay") else "  자동재생 OFF"
    return f"vol {_volume_gauge(volume)} {volume}      {autoplay}"


def _next_text(state: dict) -> str:
    hint = state.get("next_hint")
    return f"↳ 다음: {hint}" if hint else ""


def _tag_text(state: dict) -> str:
    # ♪/‖ 둘 다 A등급이라 짝이 맞는다 (_times의 ▶/‖와 같은 이유). 길이 차이는 테두리가 흡수한다.
    return "‖ PAUSED" if state.get("paused") else "♪ NOW PLAYING"


def _tail_line(state: dict, cols: int) -> str:
    """마지막 행: 시간 이동 프롬프트가 열려 있으면 그것, 아니면 키 힌트."""
    prompt = state.get("prompt")
    if prompt:
        # 커서를 숨긴 상태이므로 입력 끝을 밑줄로 표시한다.
        return _fit("  " + str(prompt) + "_", cols)
    return dim(_fit("  " + _KEY_HINT, cols))


def _card_rows(state: dict, w: int) -> list[str]:
    """박스 안쪽 11줄. 줄 수는 상태와 무관하게 고정이라 문구가 생겨도 밀리지 않는다."""
    left, right, pos, dur = _times(state)
    gap = max(1, w - display_width(left) - display_width(right))
    return [
        _fit("", w),
        bold(_fit(str(state.get("title") or "(제목 없음)"), w)),
        dim(_fit(str(state.get("channel") or ""), w)),
        _fit("", w),
        progress_bar(pos, dur, w),
        dim(_fit(left + " " * gap + right, w)),
        dim(_fit(str(state.get("notice") or ""), w)),
        _fit("", w),
        _fit(_meta_text(state), w),
        dim(_fit(_next_text(state), w)),
        _fit("", w),
    ]


def _boxed_card(state: dict, cols: int, height: int) -> list[str]:
    outer = min(cols - 4, _CARD_MAX_WIDTH)
    inner = outer - 2
    margin = " " * ((cols - outer) // 2)
    rows = _card_rows(state, inner - 4)
    tag = f" {_tag_text(state)} "
    side = max(0, inner - display_width(tag))
    box = [margin + "╭" + "─" * (side // 2) + accent(tag) + "─" * (side - side // 2) + "╮"]
    box += [margin + "│  " + row + "  │" for row in rows]
    box.append(margin + "╰" + "─" * inner + "╯")
    return [""] * max(0, (height - len(box)) // 2) + box


def _compact_card(state: dict, cols: int) -> list[str]:
    """박스를 그릴 자리가 없을 때의 4줄. 마지막 행 키 힌트는 호출자가 붙인다."""
    w = max(0, cols - 2)
    left, right, pos, dur = _times(state)
    head = str(state.get("title") or "(제목 없음)")
    channel = state.get("channel")
    if channel:
        head += f" · {channel}"
    notice = state.get("notice") or _next_text(state)
    return [
        bold(_fit(" ♪ " + head, cols)),
        " " + progress_bar(pos, dur, w),
        dim(_fit(f" {left} / {right}   {_meta_text(state)}", cols)),
        dim(_fit(" " + str(notice), cols)),
    ]


def render_playing(state: dict, cols: int, rows: int) -> list[str]:
    """재생 화면 한 프레임. 반환 줄 수는 항상 rows와 같다.

    각 줄은 이미 cols 안에 맞춰져 있으므로 호출자가 다시 자르면 안 된다 (ANSI가 깨진다).
    """
    if rows <= 0:
        return []
    body_height = rows - 1
    if rows < _CARD_MIN_ROWS or cols < _CARD_MIN_COLS:
        body = _compact_card(state, cols)
    else:
        body = _boxed_card(state, cols, body_height)
    body = body[:body_height]
    body += [""] * (body_height - len(body))
    return body + [_tail_line(state, cols)]


def comments_height(rows: int) -> int:
    """댓글 본문 높이. 헤더 2 + 구분선 1 + 푸터 1을 뺀다.

    player.py의 키 처리도 반드시 이 함수를 써야 한다. 두 군데서 따로 계산하면
    Pager.at_bottom()이 화면과 어긋나 다음 페이지 요청이 엉뚱한 데서 튄다.
    """
    return max(1, rows - 4)


def _comments_header(state: dict, cols: int) -> list[str]:
    left, right, pos, dur = _times(state)
    head = str(state.get("title") or "")
    channel = state.get("channel")
    if channel:
        head += f" · {channel}"
    times = f"  {left} / {right}"
    # 막대는 최소 4칸을 원한다. 하지만 선두 공백 1칸을 뺀 나머지 폭을 넘으면 안 된다 —
    # 상한을 두지 않으면 cols가 작을 때 "공백 1 + 막대 최소 4"가 이미 cols를 넘어서고,
    # 뒤따르는 _fit(times, ...)는 음수 폭을 0으로 클램프할 뿐 그 초과분을 되돌리지
    # 못해 이 줄이 "반환 줄은 항상 cols 안" 계약을 어기게 된다.
    wanted = max(4, cols - 2 - display_width(times))
    bar_width = max(0, min(wanted, cols - 1))
    return [
        bold(_fit(" ♪ " + head, cols)),
        (" " if cols > 0 else "") + progress_bar(pos, dur, bar_width) + dim(_fit(times, cols - 1 - bar_width)),
    ]


def render_comments(state: dict, pager: "Pager", cols: int, rows: int) -> list[str]:
    """댓글 패널 한 프레임. 위쪽에 곡 제목과 진행바가 남아 시간이 계속 흐르는 게 보인다.

    선택 블록은 평문으로 자르고 패딩한 뒤 bold를 입히고, 첫 줄의 앞 한 칸을 ▶로 바꾼다.
    """
    if rows <= 0:
        return []
    height = comments_height(rows)
    rng = pager.selected_range()
    body = []
    for i, line in enumerate(pager.visible(height), pager.top):
        selected = rng is not None and rng[0] <= i < rng[1]
        if selected and i == rng[0] and line.startswith(" "):
            line = "▶" + line[1:]
        text = _fit(line, cols)
        body.append(bold(text) if selected else text)
    body += [""] * (height - len(body))
    n = len(pager.anchors)
    count = f"{pager.cursor + 1}/{n}" if n else "0/0"
    footer = f"-- {count}{'+' if pager.more else ''}  {pager.status or pager.hint} --"
    lines = _comments_header(state, cols) + [dim("─" * cols)] + body + [dim(_fit(footer, cols))]
    lines = lines[:rows]
    return lines + [""] * (rows - len(lines))


class Pager:
    """블록(댓글 한 개) 단위 커서를 가진 줄 페이저. 블록 내용은 모른다.

    lines는 header + 블록들을 평탄화한 것이고 anchors[i]는 블록 i의 첫 줄 번호다.
    커서는 항상 화면 안에 있다: j/k는 블록을 따라 화면을 끌고, space/PgDn은 화면을 넘긴 뒤
    커서를 새 화면의 첫 블록으로 끌어온다.
    """
    DEFAULT_HINT = "j/k 이동 · q 닫기"

    def __init__(self, header: list[str] | None = None, more: bool = False, hint: str | None = None):
        self.header: list[str] = list(header or [])
        # header/_blocks가 원본(논리 줄)이고 lines/anchors는 width로 접은 파생물이다.
        # width가 None이면 접지 않는다. reflow()가 터미널 폭을 받아 다시 만든다.
        self._blocks: list[list[str]] = []
        self.width: int | None = None
        self.lines: list[str] = list(self.header)
        self.anchors: list[int] = []
        self.payloads: list = []
        self.cursor = 0
        self.top = 0
        self.hint = hint or self.DEFAULT_HINT
        # more: 뒤에 더 불러올 페이지가 있을 수 있음. status: 푸터에 띄울 한 줄
        # (요청 중/실패). status가 None이 아니면 소유자는 새 요청을 보내지 않는다.
        self.more = more
        self.status: str | None = None

    def _wrap(self, lines: list[str]) -> list[str]:
        if self.width is None:
            return list(lines)
        return [out for line in lines for out in wrap_line(line, self.width)]

    def add_items(self, blocks: list[list[str]], payloads: list | None = None) -> None:
        """블록을 뒤에 붙인다. 스크롤과 커서는 그대로."""
        if payloads is None:
            payloads = [None] * len(blocks)
        for block, payload in zip(blocks, payloads):
            self._blocks.append(list(block))
            self.anchors.append(len(self.lines))
            self.payloads.append(payload)
            self.lines.extend(self._wrap(block))

    def replace_items(self, header: list[str], blocks: list[list[str]],
                      payloads: list | None = None) -> None:
        """내용을 통째로 갈아끼운다 (정렬 전환). 커서와 스크롤은 처음으로."""
        self.header = list(header)
        self.lines = self._wrap(self.header)
        self._blocks, self.anchors, self.payloads = [], [], []
        self.cursor = self.top = 0
        self.add_items(blocks, payloads)

    def reflow(self, width: int, height: int) -> None:
        """터미널 폭에 맞춰 줄을 다시 접는다. 커서(블록)는 그대로, 화면 안에 남긴다."""
        if width == self.width:
            return
        self.width = width
        self.lines = self._wrap(self.header)
        self.anchors = []
        for block in self._blocks:
            self.anchors.append(len(self.lines))
            self.lines.extend(self._wrap(block))
        self._ensure_visible(height)

    def visible(self, height: int) -> list[str]:
        return self.lines[self.top:self.top + height]

    def selected_payload(self):
        return self.payloads[self.cursor] if self.anchors else None

    def selected_range(self) -> tuple[int, int] | None:
        """선택 블록의 줄 범위 [start, end). 블록이 없으면 None."""
        if not self.anchors:
            return None
        start = self.anchors[self.cursor]
        end = self.anchors[self.cursor + 1] if self.cursor + 1 < len(self.anchors) else len(self.lines)
        return start, end

    def at_last_item(self) -> bool:
        return bool(self.anchors) and self.cursor == len(self.anchors) - 1

    def _max_top(self, height: int) -> int:
        return max(0, len(self.lines) - height)

    def _ensure_visible(self, height: int) -> None:
        rng = self.selected_range()
        if rng is None:
            return
        start, end = rng
        if start < self.top:
            self.top = start
        elif end > self.top + height:
            self.top = end - height
        if end - start > height:
            self.top = start   # 화면보다 큰 블록은 첫 줄을 맨 위에
        self.top = max(0, min(self.top, self._max_top(height)))

    def _cursor_to_view(self, height: int) -> None:
        """첫 줄이 화면에 보이는 첫 블록으로. 없으면(긴 블록 한가운데) 화면 위 마지막 블록."""
        if not self.anchors:
            return
        lo, hi = self.top, self.top + height
        for i, anchor in enumerate(self.anchors):
            if lo <= anchor < hi:
                self.cursor = i
                return
        before = [i for i, anchor in enumerate(self.anchors) if anchor < lo]
        self.cursor = before[-1] if before else 0

    def handle_key(self, key: str, height: int) -> str | None:
        """키를 처리한다. "close"면 페이저를 닫고, "open"이면 선택 블록을 열라는 뜻."""
        if key in ("q", "ESC", "LEFT"):
            return "close"
        if key == "ENTER":
            return "open"
        n = len(self.anchors)
        if key in ("j", "DOWN"):
            if n:
                self.cursor = min(self.cursor + 1, n - 1)
                self._ensure_visible(height)
        elif key in ("k", "UP"):
            if n:
                self.cursor = max(self.cursor - 1, 0)
                self._ensure_visible(height)
        elif key in ("SPACE", "PGDWN", "f"):
            self.top = min(self.top + height, self._max_top(height))
            self._cursor_to_view(height)
        elif key in ("PGUP", "b"):
            self.top = max(self.top - height, 0)
            self._cursor_to_view(height)
        elif key in ("g", "HOME"):
            self.top = self.cursor = 0
        elif key in ("G", "END"):
            if n:
                self.cursor = n - 1
            self.top = self._max_top(height)
            self._ensure_visible(height)
        return None


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


class Screen:
    """대체 화면 전체를 소유하는 프레임 라이터.

    커서 산술이 없다. 화면이 통째로 우리 것이므로 매번 홈으로 가서 덮어쓰면 되고,
    직전 프레임과 문자열이 같으면 아예 쓰지 않는다. 크기가 바뀌면 앞에 화면 지우기를
    붙여 이전 크기의 잔상을 없앤다 — 리사이즈가 공짜로 처리된다.
    """

    def __init__(self, out=None):
        self._out = out if out is not None else sys.stdout
        self._last: str | None = None
        self._size: tuple[int, int] | None = None

    def draw(self, lines: list[str], cols: int, rows: int) -> None:
        frame = "\x1b[H" + "\r\n".join(line + "\x1b[K" for line in lines)
        resized = self._size != (cols, rows)
        if resized:
            self._size = (cols, rows)
        if not resized and frame == self._last:
            return
        self._last = frame
        if resized:
            frame = "\x1b[2J" + frame
        self._out.write(frame)
        self._out.flush()

    def reset(self) -> None:
        """대체 화면에 갓 들어왔을 때처럼, 다음 draw가 반드시 쓰도록 캐시를 버린다."""
        self._last = None
        self._size = None


def hide_cursor(out=sys.stdout) -> None:
    out.write("\x1b[?25l")
    out.flush()


def show_cursor(out=sys.stdout) -> None:
    out.write("\x1b[?25h")
    out.flush()


def enter_alt_screen(out=sys.stdout) -> None:
    out.write("\x1b[?1049h\x1b[H")
    out.flush()


def exit_alt_screen(out=sys.stdout) -> None:
    out.write("\x1b[?1049l")
    out.flush()
