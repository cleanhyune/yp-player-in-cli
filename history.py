from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from related import extract_video_id

MAX_ITEMS = 20
RESUME_MIN_SECONDS = 30
RESUME_MAX_RATIO = 0.95


def _config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "yp")


def _history_path() -> str:
    return os.path.join(_config_dir(), "history.json")


def _state_path() -> str:
    return os.path.join(_config_dir(), "state.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: str, data: dict) -> None:
    """임시 파일에 쓴 뒤 os.replace로 교체해 도중에 죽어도 기존 파일이 깨지지 않게 한다."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _items() -> list[dict]:
    items = _read_json(_history_path()).get("items")
    return items if isinstance(items, list) else []


def _save_items(items: list[dict]) -> None:
    _write_json(_history_path(), {"items": items})


def record_start(video: dict) -> None:
    video_id = extract_video_id(video.get("url", ""))
    if not video_id:
        return
    items = _items()
    existing = next((i for i in items if i.get("id") == video_id), None)
    position = float(existing.get("position") or 0.0) if existing else 0.0
    items = [i for i in items if i.get("id") != video_id]
    items.insert(0, {
        "id": video_id,
        "title": video.get("title", "제목 없음"),
        "channel": video.get("channel", "알 수 없음"),
        "url": video["url"],
        "duration": float(video.get("duration") or 0),
        "last_played_at": datetime.now().isoformat(timespec="seconds"),
        "position": position,
    })
    _save_items(items[:MAX_ITEMS])


def record_position(video_id: str, seconds: float) -> None:
    items = _items()
    for item in items:
        if item.get("id") == video_id:
            item["position"] = float(seconds)
            _save_items(items)
            return


def clear_position(video_id: str) -> None:
    record_position(video_id, 0.0)


def recent(limit: int = MAX_ITEMS) -> list[dict]:
    return _items()[:limit]


def resume_position(video_id: str, duration: float) -> float | None:
    item = next((i for i in _items() if i.get("id") == video_id), None)
    if item is None:
        return None
    position = float(item.get("position") or 0.0)
    if position < RESUME_MIN_SECONDS:
        return None
    if duration and position >= duration * RESUME_MAX_RATIO:
        return None
    return position


def load_state() -> dict:
    data = _read_json(_state_path())
    volume = data.get("volume", 100)
    autoplay = data.get("autoplay", True)
    if not isinstance(volume, int) or isinstance(volume, bool):
        volume = 100
    if not isinstance(autoplay, bool):
        autoplay = True
    return {"volume": volume, "autoplay": autoplay}


def save_state(volume: int, autoplay: bool) -> None:
    _write_json(_state_path(), {"volume": int(volume), "autoplay": bool(autoplay)})
