from __future__ import annotations

import questionary


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


def select_video(videos: list[dict]) -> str | None:
    choices = [
        f"{v['title']} [{format_duration(v['duration'])}]"
        for v in videos
    ]
    label_to_url = {
        f"{v['title']} [{format_duration(v['duration'])}]": v["url"]
        for v in videos
    }

    chosen = questionary.select(
        "재생할 영상을 선택하세요:",
        choices=choices,
    ).ask()

    if chosen is None:
        return None
    return label_to_url[chosen]
