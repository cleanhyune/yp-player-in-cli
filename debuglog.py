"""YP_DEBUG가 켜져 있을 때만 삼킨 예외를 파일에 남긴다.

재생 중에는 터미널이 대체 화면이라 stderr에 찍으면 카드가 깨진다. 그래서 파일이다.
YP_DEBUG=1 이면 ~/.config/yp/debug.log, 다른 값이면 그 값을 경로로 쓴다."""
from __future__ import annotations

import logging
import os

ENV = "YP_DEBUG"
_ROOT = "yp"


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT}.{name}")


def default_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "yp", "debug.log")


def setup(env: dict | None = None) -> str | None:
    """환경변수를 읽어 로거를 구성하고, 켜졌으면 로그 파일 경로를 돌려준다."""
    value = (os.environ if env is None else env).get(ENV, "").strip()
    root = logging.getLogger(_ROOT)
    root.handlers.clear()
    if not value or value == "0":
        root.addHandler(logging.NullHandler())
        root.propagate = True
        return None
    path = default_path() if value == "1" else os.path.expanduser(value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    root.propagate = False
    return path
