from __future__ import annotations

import questionary

NEXT_PAGE = "__next__"
PREV_PAGE = "__prev__"

PAGE_SIZE = 10


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


def select_video(videos: list[dict], page: int = 1, max_pages: int = 3) -> str | None:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_videos = videos[start:end]

    choices = [
        f"{v['title']} · {v['channel']} [{format_duration(v['duration'])}]"
        for v in page_videos
    ]
    label_to_url = dict(zip(choices, (v["url"] for v in page_videos)))

    if page > 1:
        choices = ["◀ 이전 페이지"] + choices
    if page < max_pages and len(videos) > end:
        choices = choices + ["다음 페이지 ▶"]

    chosen = questionary.select(
        "재생할 영상을 선택하세요:",
        choices=choices,
    ).ask()

    if chosen is None:
        return None
    if chosen == "◀ 이전 페이지":
        return PREV_PAGE
    if chosen == "다음 페이지 ▶":
        return NEXT_PAGE
    return label_to_url[chosen]
