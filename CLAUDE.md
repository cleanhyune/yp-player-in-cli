# yp — AI Agent Guide

CLI tool that searches YouTube and plays audio-only via mpv. macOS only.

## Structure

```
yp.py         # Entry point, main search→select→play→autoplay loop
searcher.py   # YouTube search via yt-dlp YoutubeDL API
selector.py   # Arrow-key selection UI via questionary
player.py     # mpv subprocess wrapper, tracks mpv exit reason
related.py    # Next-video lookup by parsing the watch page's related-video sidebar
comments.py   # Fetches top comments, shows them in a new terminal window (on-demand, 't' key in mpv)
```

## Key Decisions

- **yt-dlp Python API** (not subprocess) — `YoutubeDL` class with `extract_flat: True` for fast search without fetching full metadata
- **`_SilentLogger`** in `searcher.py` — suppresses yt-dlp's Python version deprecation warnings
- **mpv `--no-video --ytdl-format=bestaudio/best`** — audio-only streaming with a fallback format; mpv handles all keyboard controls natively (space, arrows, 9/0, q, `g` seek, `t` comments)
- **Lua seek script** — `_SEEK_SCRIPT` in `player.py`; written to a tempfile at runtime, passed via `--script`, deleted on exit. Binds `g` key to `mp.input.get()` for time-code input. Timecode format: 4 digits = MMSS, 5-6 digits = (H)HMMSS. Also registers an `end-file` handler that writes mpv's exit reason (`eof`/`stop`/`quit`/`error`) to `/tmp/yp_last_reason`, read back by `player._load_reason()`
- **Autoplay via sidebar scraping, not yt-dlp** — `related.py` fetches the watch page HTML directly and parses the `ytInitialData` JSON blob for the real "related videos" sidebar (`lockupViewModel` entries under `contents.twoColumnWatchNextResults.secondaryResults...`). An earlier version used yt-dlp's `RD<video_id>` mix playlist, but that mix doesn't exist for many videos (e.g. broadcast/drama clips) — see [[autoplay_related_videos]] memory. yt-dlp deliberately doesn't expose this sidebar, so this parsing is unofficial and self-maintained: if YouTube changes the JSON shape, only fixing `related.py` (not `pip install -U yt-dlp`) will help. `fetch_next()` swallows every exception internally so a broken parse can never propagate into the autoplay loop
- **`from __future__ import annotations`** in `selector.py`/`related.py`/`comments.py` — required for `str | None` syntax on Python 3.9
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
search(query) -> [{"title", "channel", "url", "duration"}, ...]  # 30개 한번에
select_video(videos, page, max_pages) -> url | NEXT_PAGE | PREV_PAGE | None
play(url) -> reason  (blocks until mpv exits; reason = "eof"/"stop"/"quit"/"error"/"unknown")

# yp.py의 재생 분기: reason == "eof"인 동안 아래를 반복해 자동재생 체인을 이어감
fetch_next(url, played_ids) -> {"title", "channel", "url", "duration"} | None
```

## Distribution

- Main repo: https://github.com/cleanhyune/yp-player-in-cli
- Homebrew tap: https://github.com/cleanhyune/homebrew-yp (`Formula/yp.rb`)

**Releasing a new version:**
1. Commit changes, `git tag vX.X.X && git push origin vX.X.X`
2. GitHub Actions가 태그 push를 감지해 릴리즈 생성 및 `homebrew-yp` Formula 자동 업데이트까지 처리함 — 수동 배포 불필요
