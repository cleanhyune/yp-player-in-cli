# yp — AI Agent Guide

CLI tool that searches YouTube and plays audio-only via mpv. macOS only.

## Structure

```
yp.py         # Entry point, main search→select→play loop
searcher.py   # YouTube search via yt-dlp YoutubeDL API
selector.py   # Arrow-key selection UI via questionary
player.py     # mpv subprocess wrapper
```

## Key Decisions

- **yt-dlp Python API** (not subprocess) — `YoutubeDL` class with `extract_flat: True` for fast search without fetching full metadata
- **`_SilentLogger`** in `searcher.py` — suppresses yt-dlp's Python version deprecation warnings
- **mpv `--no-video --ytdl-format=bestaudio`** — audio-only streaming; mpv handles all keyboard controls natively (space, arrows, 9/0, q)
- **Lua seek script** — `_SEEK_SCRIPT` in `player.py`; written to a tempfile at runtime, passed via `--script`, deleted on exit. Binds `g` key to `mp.input.get()` for time-code input. Timecode format: 4 digits = MMSS, 5-6 digits = (H)HMMSS
- **`from __future__ import annotations`** in `selector.py` — required for `str | None` syntax on Python 3.9
- `duration` from yt-dlp is `float`, so `format_duration()` casts to `int` first

## Commands

```bash
# Run
python3 yp.py "검색어"

# Test
python3 -m pytest tests/ -v

# Install dependencies
python3 -m pip install yt-dlp questionary pytest
brew install mpv
```

## Data Flow

```
search(query) -> [{"title", "url", "duration"}, ...]  # 30개 한번에
select_video(videos, page, max_pages) -> url | NEXT_PAGE | PREV_PAGE | None
play(url) -> None  (blocks until mpv exits)
```

## Distribution

- Main repo: https://github.com/cleanhyune/yp-player-in-cli
- Homebrew tap: https://github.com/cleanhyune/homebrew-yp (`Formula/yp.rb`)

**Releasing a new version:**
1. Commit changes, `git tag vX.X.X && git push origin vX.X.X`
2. GitHub Actions가 태그 push를 감지해 릴리즈 생성 및 `homebrew-yp` Formula 자동 업데이트까지 처리함 — 수동 배포 불필요
