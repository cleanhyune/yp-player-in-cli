from __future__ import annotations

import questionary

NEXT_PAGE = "__next__"
PREV_PAGE = "__prev__"
NEW_SEARCH = "__new_search__"

PAGE_SIZE = 10

_STYLE = questionary.Style([("choice-channel", "fg:#6c7078")])


def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "0:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _choice(v: dict, value: str) -> questionary.Choice:
    """제목 한 줄, 그 아래 흐린 메타 한 줄. 두 번째 줄의 3칸 들여쓰기는 ' » ' 포인터 폭이다."""
    meta = f"\n   {v['channel']} · {format_duration(v['duration'])}"
    if v.get("age"):
        meta += f" · {v['age']}"
    return questionary.Choice(
        title=[("class:text", v["title"]), ("class:choice-channel", meta)],
        value=value,
    )


# Esc 뒤 Enter는 prompt_toolkit emacs 바인딩(escape enter = 입력 확정)에 걸려 questionary가
# 빈 문자열을 돌려준다. 목록에 없는 값이면 같은 목록을 다시 띄운다 — select_video/select_recent 공통.
def _spaced(choices: list) -> list:
    out: list = []
    for c in choices:
        if out:
            out.append(questionary.Separator(" "))
        out.append(c)
    return out


def select_video(videos: list[dict], page: int = 1, max_pages: int = 3,
                 message: str = "재생할 영상을 선택하세요:") -> str | None:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_videos = videos[start:end]

    labels = [
        f"{v['title']} · {v['channel']} [{format_duration(v['duration'])}]"
        for v in page_videos
    ]
    label_to_url = dict(zip(labels, (v["url"] for v in page_videos)))

    choices = _spaced([_choice(v, label) for v, label in zip(page_videos, labels)])
    if page > 1:
        choices = ["◀ 이전 페이지", questionary.Separator(" ")] + choices
    if page < max_pages and len(videos) > end:
        choices = choices + [questionary.Separator(" "), "다음 페이지 ▶"]

    while True:
        chosen = questionary.select(message, choices=choices, style=_STYLE).ask()
        if chosen is None:
            return None
        if chosen == "◀ 이전 페이지":
            return PREV_PAGE
        if chosen == "다음 페이지 ▶":
            return NEXT_PAGE
        if chosen in label_to_url:
            return label_to_url[chosen]


def select_recent(items: list[dict]) -> str | None:
    """최근 재생 목록에서 하나를 고른다. 맨 위의 '새로 검색'을 고르면 NEW_SEARCH를 반환."""
    new_search_label = "🔍 새로 검색"
    labels = [
        f"{v['title']} · {v['channel']} [{format_duration(v['duration'])}]"
        for v in items
    ]
    label_to_url = dict(zip(labels, (v["url"] for v in items)))

    choices: list = [new_search_label, questionary.Separator(" ")]
    choices += _spaced([_choice(v, label) for v, label in zip(items, labels)])

    while True:
        chosen = questionary.select("최근 재생:", choices=choices, style=_STYLE).ask()
        if chosen is None:
            return None
        if chosen == new_search_label:
            return NEW_SEARCH
        if chosen in label_to_url:
            return label_to_url[chosen]
