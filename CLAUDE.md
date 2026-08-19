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
- **mpv `--no-video --ytdl-format=bestaudio/best`** — audio-only streaming with a fallback format; mpv handles all keyboard controls natively (space, arrows, 9/0, q, `g` seek, `t` comments, `a` autoplay toggle)
- **Lua seek script** — `_SEEK_SCRIPT` in `player.py`; written to a tempfile at runtime, passed via `--script`, deleted on exit. Binds `g` key to `mp.input.get()` for time-code input. Timecode format: 4 digits = MMSS, 5-6 digits = (H)HMMSS. Also registers an `end-file` handler that writes mpv's exit reason (`eof`/`stop`/`quit`/`error`) to `/tmp/yp_last_reason`, read back by `player._load_reason()`. Binds `a` to toggle autoplay, persisted to `/tmp/yp_autoplay` (`"0"`/`"1"`, default on), read back by `player.is_autoplay_enabled()`
- **Autoplay via sidebar scraping, not yt-dlp** — `related.py` fetches the watch page HTML directly and parses the `ytInitialData` JSON blob for the real "related videos" sidebar (`lockupViewModel` entries under `contents.twoColumnWatchNextResults.secondaryResults...`). An earlier version used yt-dlp's `RD<video_id>` mix playlist, but that mix doesn't exist for many videos (e.g. broadcast/drama clips) — see [[autoplay_related_videos]] memory. yt-dlp deliberately doesn't expose this sidebar, so this parsing is unofficial and self-maintained: if YouTube changes the JSON shape, only fixing `related.py` (not `pip install -U yt-dlp`) will help. `fetch_next()` swallows every exception internally so a broken parse can never propagate into the autoplay loop
- **Autoplay prefetch** — `yp.py`'s `_start_prefetch()` kicks off `related.fetch_next()` in a `daemon=True` background thread as soon as the current video starts playing, so the next video is usually already resolved by the time mpv hits EOF (no "다음 영상을 찾는 중..." pause). `played_ids` is snapshotted with `set(played_ids)` before handing it to the thread, since the main loop keeps mutating the original set concurrently
- **`from __future__ import annotations`** in `selector.py`/`related.py`/`comments.py` — required for `str | None` syntax on Python 3.9
- `duration` from yt-dlp is `float`, so `format_duration()` casts to `int` first

## Commands

**로컬 개발은 `python3` 대신 `python3.11`(또는 3.10+)로 실행할 것.** macOS 기본/Xcode `python3`는 3.9.6인데, yt-dlp가 최근 릴리즈부터 Python 3.10+를 요구하기 시작해서 3.9 환경엔 2025.10.14가 pip으로 설치 가능한 마지막 버전으로 영구히 고정된다. YouTube는 추출 로직을 자주 바꾸고 yt-dlp가 그때그때 패치를 내는 구조라, 이 오래된 버전으로 로컬 테스트하면 "The page needs to be reloaded" 같은 간헐적 추출 에러를 실제보다 훨씬 자주 만나게 된다. `brew install python@3.11`로 설치 가능.

```bash
# Run
python3.11 yp.py "검색어"

# Test
python3.11 -m pytest tests/ -v

# Install dependencies
python3.11 -m pip install yt-dlp questionary pytest
brew install mpv
```

## Data Flow

```
search(query) -> [{"title", "channel", "url", "duration"}, ...]  # 30개 한번에
select_video(videos, page, max_pages) -> url | NEXT_PAGE | PREV_PAGE | None
play(url) -> reason  (blocks until mpv exits; reason = "eof"/"stop"/"quit"/"error"/"unknown")
is_autoplay_enabled() -> bool  ("a" 키로 토글, /tmp/yp_autoplay에 저장, 기본 True)

# yp.py의 재생 분기: reason == "eof" and is_autoplay_enabled()인 동안 반복해 자동재생 체인을 이어감
# 다음 영상 조회는 현재 영상 재생 시작 시점에 백그라운드 스레드로 미리 해둠(prefetch)
fetch_next(url, played_ids) -> {"title", "channel", "url", "duration"} | None
```

## Distribution

- Main repo: https://github.com/cleanhyune/yp-player-in-cli
- Homebrew tap: https://github.com/cleanhyune/homebrew-yp (`Formula/yp.rb`)

**Releasing a new version:**
1. Commit changes, `git tag vX.X.X && git push origin vX.X.X`
2. GitHub Actions가 태그 push를 감지해 릴리즈 생성 및 `homebrew-yp` Formula 자동 업데이트까지 처리함 — 수동 배포 불필요

**새 `.py` 모듈을 추가할 때** `pyproject.toml`의 `[tool.setuptools] py-modules` 목록에도 반드시 추가할 것. v0.3.0까지 이 목록이 업데이트되지 않아 `related.py`/`comments.py`가 brew 배포판에서 누락되어, 실제 설치한 사용자는 검색만 해도 `ModuleNotFoundError`로 죽는 상태였음 (v0.3.1에서 수정). 로컬 개발 중에는 `python3 yp.py`로 직접 실행하므로 이 문제가 드러나지 않는다 — 릴리즈 전엔 `python3 -m build --sdist`로 실제 패키징 결과물에 모든 모듈이 포함되는지 확인.

## Demo GIFs

`assets/demo-*.gif`는 `assets/demo-*.tape` ([vhs](https://github.com/charmbracelet/vhs)) 스크립트로 생성됨. 재생성 시:
- `brew install vhs`, 검색어는 실제 업로드 영상만 나오는 걸로 (라이브 방송/과거 라이브 아카이브는 이 환경에서 HLS 스트림 오픈이 잘 실패함 — `python3.11 -c "from searcher import search; ..."`로 먼저 결과를 확인하고 `duration`이 있는 항목을 고를 것)
- 녹화 중 실제로 오디오가 재생되므로 `/tmp/yp_volume`을 임시로 `0`으로 덮어써서 음소거한 뒤 복원
- `vhs assets/demo-X.tape` 실행 → `assets/demo-X.gif` 생성
