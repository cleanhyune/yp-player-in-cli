# yp — AI Agent Guide

CLI tool that searches YouTube and plays audio-only via mpv. macOS only.

## Structure

```
yp.py         # Entry point: search→select→PlayerSession loop, autoplay chain, -r/--recent
searcher.py   # YouTube search via yt-dlp YoutubeDL API
selector.py   # Arrow-key selection UI via questionary (search results + recent history)
player.py     # PlayerSession: one mpv process per playback session, driven over JSON IPC
mpv_ipc.py    # mpv JSON IPC client (request/response matching + event queue). No yp knowledge
tui.py        # Terminal ownership: cbreak key reader, status line, comments pager, timecode prompt
history.py    # ~/.config/yp/history.json (recent + resume position) and state.json (volume, autoplay)
related.py    # Next-video lookup by parsing the watch page's related-video sidebar; same-channel first
comments.py   # CommentFeed: paged root-comment fetching with cross-page dedupe + pager formatting ('t')
```

## Key Decisions

- **yt-dlp Python API** (not subprocess) — `YoutubeDL` class with `extract_flat: True` for fast search without fetching full metadata
- **`_SilentLogger`** in `searcher.py` — suppresses yt-dlp's Python version deprecation warnings
- **Autoplay via sidebar scraping, not yt-dlp** — `related.py` fetches the watch page HTML directly and parses the `ytInitialData` JSON blob for the real "related videos" sidebar (`lockupViewModel` entries under `contents.twoColumnWatchNextResults.secondaryResults...`). An earlier version used yt-dlp's `RD<video_id>` mix playlist, but that mix doesn't exist for many videos (e.g. broadcast/drama clips) — see [[autoplay_related_videos]] memory. yt-dlp deliberately doesn't expose this sidebar, so this parsing is unofficial and self-maintained: if YouTube changes the JSON shape, only fixing `related.py` (not `pip install -U yt-dlp`) will help. `fetch_next()` swallows every exception internally so a broken parse can never propagate into the autoplay loop
- **One mpv per playback session, Python owns the terminal** — `PlayerSession.start()` launches `mpv --no-video --no-terminal --idle=yes --input-ipc-server=/tmp/yp_mpv_socket` once; each video is a `loadfile` over IPC, so autoplay/`n` transitions have no process restart and volume/pause state survives. Because mpv has no terminal, `tui.KeyReader` reads stdin in cbreak mode (not raw — Ctrl+C must still raise `KeyboardInterrupt`) and forwards unknown keys to mpv via the `keypress` IPC command, so mpv's default bindings (space, arrows, 9/0, m) keep working. Intercepted keys: `q` quit, `n` next, `a` autoplay toggle, `g` timecode prompt, `t` comments pager (and inside the pager, `s` toggles comment sort). The status line and pager are drawn by Python; mpv's own OSD is never used
- **Single event queue** — `MpvClient.events` receives mpv events, key events from `KeyReader`, `comments-ready/failed` plus `comments-more/-failed` and `comments-reload/-failed` from the comments thread, and `redraw` from the prefetch thread. `PlayerSession.load()` consumes only this queue. This is what makes "`end-file reason=stop` right after `n` means `next`, otherwise `quit`" a safe interpretation
- **Per-file options via `set` before `loadfile`** — player-client fallback (`android` → `web_embedded`) and resume position are applied with `set ytdl-raw-options ...` / `set start <sec>|none` immediately before each `loadfile`, not as `loadfile` positional options (whose positional layout changed in mpv 0.38)
- **No more /tmp state files or Lua** — volume and autoplay persist in `~/.config/yp/state.json`; playback history and resume positions in `history.json` (atomic write via `os.replace`). Resume applies when the saved position is ≥30s and <95% of duration. `time-pos` `null` at end-of-file is ignored so the last real position isn't wiped before it's recorded
- **mpv errors surface via IPC** — `request_log_messages error`; log lines are printed only on the final fallback attempt (same effect as the old per-attempt `--msg-level`)
- **Comment paging re-walks the list, and "top" sort is unstable** — yt-dlp exposes only `max_comments` caps, no continuation cursor, so `CommentFeed.next_page()` re-fetches `offset + page_size` parents and slices. YouTube's `comment_sort=top` ranking is reshuffled on every request (measured: 2/40 to 34/40 positions unchanged between two identical calls), so the slice alone shows the same comment twice — `CommentFeed` filters by `(author, text)` against everything already shown. Cost of that: pages get shorter as you go (measured 100 → 83 → 75) and some comments are never reachable. `comment_sort=new` is perfectly stable (40/40), so `s` in the pager toggles sort by building a fresh `CommentFeed` and replacing the pager's contents. Replies are not fetched at all (`max_replies=0`), which also keeps the re-walk cheap: ~1.5s fixed + ~2.3s per 100 parents
- **The pager owns paging state, `player.py` decides** — `tui.Pager` stays a dumb line scroller plus three fields: `more` (another page may exist), `status` (footer override; non-`None` also means "a request is in flight, don't send another") and `hint`. `PlayerSession._modal_key` is what checks `more and status is None and at_bottom(height)` and fires the next-page fetch, so `tui.py` needs no knowledge of comments
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
select_recent(items) -> url | NEW_SEARCH | None

session = PlayerSession(volume, autoplay, tty); session.start()
session.load(url, start) -> "eof" | "quit" | "next" | "error"   # blocks until the file ends
session.autoplay / session.volume / session.position           # read after load()
session.quit()

# yp._play_session: reason이 "eof"(autoplay on) 또는 "next"인 동안 prefetch 결과로 load()를 반복
fetch_next(url, played_ids, current_channel) -> {"title", "channel", "url"} | None   # 같은 채널 우선

feed = CommentFeed(url, sort="top"|"new")      # 'top'은 요청마다 순서가 흔들려 중복 제거 필요
feed.next_page() -> (comments, more)           # 최상위 댓글만, 100개 단위. feed.shown = 표시 누적
format_comments(comments, title=None, start_index=1, note=None) -> [줄]   # title 없으면 헤더 생략
history.record_start / record_position / clear_position / resume_position / recent / load_state / save_state
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
- 녹화 중 실제로 오디오가 재생되므로 `~/.config/yp/state.json`의 `"volume"`을 임시로 `0`으로 바꿔 음소거한 뒤 복원
- `vhs assets/demo-X.tape` 실행 → `assets/demo-X.gif` 생성
