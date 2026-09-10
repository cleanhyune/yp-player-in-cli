# yp — AI Agent Guide

CLI tool that searches YouTube and plays audio-only via mpv. macOS only.

## Structure

```
yp.py         # Entry point: search→select→PlayerSession loop, autoplay chain, -r/--recent
searcher.py   # YouTube search via yt-dlp YoutubeDL API
selector.py   # Arrow-key selection UI via questionary (search results + recent history)
player.py     # PlayerSession: one mpv process per playback session, driven over JSON IPC
mpv_ipc.py    # mpv JSON IPC client (request/response matching + event queue). No yp knowledge
tui.py        # Terminal ownership: cbreak key reader, alt-screen card/comments renderers, timecode prompt
history.py    # ~/.config/yp/history.json (recent + resume position) and state.json (volume, autoplay)
related.py    # Next-video lookup by parsing the watch page's related-video sidebar; same-channel first
comments.py   # CommentFeed: paged root-comment fetching with cross-page dedupe + pager formatting ('t')
```

## Key Decisions

- **yt-dlp Python API** (not subprocess) — `YoutubeDL` class with `extract_flat: True` for fast search without fetching full metadata
- **`_SilentLogger`** in `searcher.py` — suppresses yt-dlp's Python version deprecation warnings
- **Autoplay via sidebar scraping, not yt-dlp** — `related.py` fetches the watch page HTML directly and parses the `ytInitialData` JSON blob for the real "related videos" sidebar (`lockupViewModel` entries under `contents.twoColumnWatchNextResults.secondaryResults...`). An earlier version used yt-dlp's `RD<video_id>` mix playlist, but that mix doesn't exist for many videos (e.g. broadcast/drama clips) — see [[autoplay_related_videos]] memory. yt-dlp deliberately doesn't expose this sidebar, so this parsing is unofficial and self-maintained: if YouTube changes the JSON shape, only fixing `related.py` (not `pip install -U yt-dlp`) will help. `fetch_next()` swallows every exception internally so a broken parse can never propagate into the autoplay loop
- **One mpv per playback session, Python owns the terminal** — `PlayerSession.start()` launches `mpv --no-video --no-terminal --idle=yes --input-ipc-server=/tmp/yp_mpv_socket` once; each video is a `loadfile` over IPC, so autoplay/`n` transitions have no process restart and volume/pause state survives. Because mpv has no terminal, `tui.KeyReader` reads stdin in cbreak mode (not raw — Ctrl+C must still raise `KeyboardInterrupt`) and forwards unknown keys to mpv via the `keypress` IPC command, so mpv's default bindings (space, arrows, 9/0, m) keep working. Intercepted keys: `q` quit, `n` next, `a` autoplay toggle, `g` timecode prompt, `t` comments pager (and inside the pager, `s` toggles comment sort).
  Python이 재생 중 터미널 전체를 소유한다: `start()`에서 대체 화면(alt screen)에 들어가
  `quit()`에서 나오므로 자동재생 체인 전체가 한 화면 세션이고 곡 전환에 깜빡임이 없다.
  `tui.render_playing()` / `render_comments()`가 화면 한 프레임을 `list[str]`로 만드는
  **순수 함수**이고 `tui.Screen`이 `\x1b[H`부터 통째로 덮어쓴다 — 직전 프레임과 같으면
  쓰지 않고, 크기가 바뀌면 앞에 `\x1b[2J`를 붙여 리사이즈를 공짜로 처리한다. 화면을
  통째로 소유하므로 예전 `StatusArea`의 커서 산술(N줄 위로, 잔여 줄 지우기)이 사라졌다.
  댓글은 별도 화면이 아니라 카드 아래 패널이라 읽는 동안에도 진행바가 흐른다 —
  `_redraw()`에 있던 "모달이 열려 있으면 그리지 않는다" 가드를 없앤 것이 그 핵심이다.
  본문 높이는 `tui.comments_height(rows)` 하나로만 계산한다 (렌더러와 키 처리가 따로
  계산하면 `Pager.at_bottom()`이 화면과 어긋난다). mpv의 OSD는 여전히 쓰지 않는다.
  - **대체 화면 안에서는 `print()`가 무의미하다** — 나갈 때 통째로 사라진다. 그래서
    `yp.py`가 재생 중 찍던 진행 안내는 카드의 "안내 슬롯"으로 갔고(`set_notice()`),
    mpv 오류는 `session.errors`에 버퍼링해 `quit()` 뒤에 찍는다. `set_notice()`는
    `set_next_hint()`와 달리 이벤트 큐를 거치지 않고 바로 그린다 — 곡과 곡 사이에는
    `_wait_end`의 루프가 돌지 않아 큐에 넣으면 아무도 꺼내지 않기 때문이다 (메인 스레드 전용).
    안내는 `time-pos`가 올 때마다(최초 1회가 아니라 매번) 지워지므로, 재생 '중'에
    `set_notice()`를 부르면 다음 time-pos 틱(~0.5초)에 바로 사라진다 — 지금은 스트림이
    열리기 전이나 곡 사이(아직 time-pos가 없는 구간)에서만 불러 문제가 안 되지만, 재생
    중 호출하는 용도로 쓰려면 최초 1회만 지우는 플래그를 먼저 추가해야 한다.
  - **스타일은 bold/dim만** — 색상은 사용자 터미널 테마와 충돌하므로 쓰지 않는다.
    `NO_COLOR`가 있으면 둘 다 끈다. 규칙 하나: **평문으로 자르고 패딩한 뒤 스타일을 입힌다.**
    `display_width()`는 ANSI를 셀 줄 모르므로 순서가 뒤집히면 CJK 제목에서 폭이 조용히 깨진다.
- **Single event queue** — `MpvClient.events` receives mpv events, key events from `KeyReader`, `comments-ready/failed` plus `comments-more/-failed` and `comments-reload/-failed` from the comments thread, and `redraw` from the prefetch thread. `PlayerSession.load()` consumes only this queue. This is what makes "`end-file reason=stop` right after `n` means `next`, otherwise `quit`" a safe interpretation
- **Per-file options via `set` before `loadfile`** — the attempt chain and resume position are applied with `set ytdl-raw-options ...` / `set start <sec>|none` immediately before each `loadfile`, not as `loadfile` positional options (whose positional layout changed in mpv 0.38)
- **The attempt chain ends in a cookie attempt; the winner is remembered but re-probed daily** — `_ATTEMPTS` is `(android, no cookies)` → `(web_embedded, no cookies)` → `(web_safari, cookies-from-browser=chrome)`, and `load()` retries the next entry whenever `end-file reason=error` comes back. The last entry exists because YouTube now demands bot attestation on the player endpoint per-IP: when that kicks in, **every** anonymous request dies with `Sign in to confirm you're not a bot` regardless of yt-dlp version or client (2026-09: verified across 2025.10.14 / 2026.06.09 / 2026.07.04 / 2026.08.19 × 8 clients — all identical), and only cookies get through. Cookies narrow the usable clients, though: `android`/`ios` then fail with `No video formats found!` and `tv` with `The page needs to be reloaded`, so only the web family works — `web_safari` was fastest (7.3s vs web_embedded 8.1s / mweb 8.6s / web 9.7s). Note there is deliberately **no error-string matching** for the bot message: the existing retry-on-error loop already covers it and won't rot when yt-dlp rewords the error
- **Who owns what in that chain** — `PlayerSession.strategy` holds the `player_client` that last actually opened a stream, and `_attempt_order()` rotates it to the front. The signal for "this attempt worked" is `reason == "eof" or self.duration > 0` — duration arriving is the only proof the stream opened, so pressing `q` mid-resolution isn't recorded as a success. `player.py` knows nothing about clocks: **`yp._play_session` owns the re-probe policy**, discarding the remembered strategy when `state["probed"] != _today()` so that once a day the chain runs from the top again. Without that, a session that got blocked once would sit on the slower cookie path forever even after YouTube unblocked the IP — and keep attaching the user's account cookies (watch-history side effect) for no reason. The probe is placed at `PlayerSession` construction, i.e. right after the user picks a track and is already waiting for a stream, never mid-autoplay-chain. Cost of a wasted probe while still blocked: **~5.7s** end-to-end through mpv (2026-09 measured: 13.3s resolve for the full chain vs 7.6s straight to `web_safari`; raw yt-dlp failures are only 1.3s + 2.1s, the rest is mpv respawning ytdl_hook per attempt). When *not* blocked the probe costs nothing, since `android` succeeds on the first try. `strategy`/`probed` live in `state.json`; deleting them forces an immediate re-probe
- **No more /tmp state files or Lua** — volume and autoplay persist in `~/.config/yp/state.json`; playback history and resume positions in `history.json` (atomic write via `os.replace`). Resume applies when the saved position is ≥30s and <95% of duration. `time-pos` `null` at end-of-file is ignored so the last real position isn't wiped before it's recorded
- **mpv errors surface via IPC** — `request_log_messages error`; log lines are printed only on the final fallback attempt (same effect as the old per-attempt `--msg-level`)
- **Comment paging re-walks the list, and "top" sort is unstable** — yt-dlp exposes only `max_comments` caps, no continuation cursor, so `CommentFeed.next_page()` re-fetches `offset + page_size` parents and slices. YouTube's `comment_sort=top` ranking is reshuffled on every request (measured: 2/40 to 34/40 positions unchanged between two identical calls), so the slice alone shows the same comment twice — `CommentFeed` filters by `(author, text)` against everything already shown. Cost of that: pages get shorter as you go (measured 100 → 83 → 75) and some comments are never reachable. `comment_sort=new` is perfectly stable (40/40), so `s` in the pager toggles sort by building a fresh `CommentFeed` and replacing the pager's contents. Replies are not fetched at all (`max_replies=0`), which also keeps the re-walk cheap: ~1.5s fixed + ~2.3s per 100 parents
- **Comment age comes from `_time_text`, never from `timestamp`** — YouTube sends only relative wording ("5 hours ago"); yt-dlp's `timestamp` is that wording back-computed and then quantized to the hour or to midnight UTC, so the real difference lands *under* the stated unit (measured: "5 hours ago" → `22:00:00`, 4.79h before now) and dividing it out shifts every value down by one. YouTube's own buckets don't match clean divisions either — its "days" bucket runs to at least 12 days, so a 7-day week boundary turns "11 days ago" into "1주 전". `format_age()` therefore translates the wording directly and returns `None` on anything it can't parse, so a yt-dlp change hides the age rather than showing a wrong one. Verified against 200 real comments across both sorts: zero mismatches
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

session = PlayerSession(volume, autoplay, tty, strategy); session.start()   # 대체 화면 진입
session.set_track(title, channel, duration)   # 카드에 미리 올릴 메타데이터 (mpv의 media-title은 늦게 온다)
session.set_notice(text | None)               # 안내 슬롯. time-pos가 올 때마다 지워진다(첫 번째만이 아님). 메인 스레드 전용
session.load(url, start) -> "eof" | "quit" | "next" | "error"   # blocks until the file ends
session.autoplay / session.volume / session.position           # read after load()
session.strategy                                               # 실제로 스트림을 연 player_client
session.errors                                # 버퍼된 mpv 오류. quit() 뒤에 출력할 것
session.quit()                                # 대체 화면 이탈

render_playing(state, cols, rows) -> list[str]                 # 길이는 항상 rows
render_comments(state, pager, cols, rows) -> list[str]         # 길이는 항상 rows
comments_height(rows) -> int                                   # 페이저 본문 높이. 유일한 출처
progress_bar(position, duration, width) -> str                 # ANSI 제거 시 폭이 정확히 width

# yp._play_session: reason이 "eof"(autoplay on) 또는 "next"인 동안 prefetch 결과로 load()를 반복
fetch_next(url, played_ids, current_channel) -> {"title", "channel", "url"} | None   # 같은 채널 우선

feed = CommentFeed(url, sort="top"|"new")      # 'top'은 요청마다 순서가 흔들려 중복 제거 필요
feed.next_page() -> (comments, more)           # 최상위 댓글만, 100개 단위. feed.shown = 표시 누적
                # comments 항목: {"author", "text", "like_count", "age"}  age는 "7년 전" | None
format_age(time_text) -> "7년 전" | None       # yt-dlp의 _time_text를 번역 (timestamp 쓰지 말 것)
format_comments(comments, title=None, start_index=1, note=None) -> [줄]   # title 없으면 헤더 생략
history.record_start / record_position / clear_position / resume_position / recent
history.load_state() -> {"volume", "autoplay", "strategy", "probed"}
                # strategy = 지난번 스트림을 연 player_client | None
                # probed   = strategy를 마지막으로 재탐색한 날짜(ISO) | None. 오늘이 아니면 재탐색
history.save_state(volume, autoplay, strategy=None, probed=None)
yp._today() -> "YYYY-MM-DD"                    # 재탐색 판정용. 테스트는 이것만 고정한다
```

## Distribution

- Main repo: https://github.com/cleanhyune/yp-player-in-cli
- Homebrew tap: https://github.com/cleanhyune/homebrew-yp (`Formula/yp.rb`)

**Releasing a new version:**
1. Commit changes, `git tag vX.X.X && git push origin vX.X.X`
2. GitHub Actions가 태그 push를 감지해 릴리즈 생성 및 `homebrew-yp` Formula 자동 업데이트까지 처리함 — 수동 배포 불필요

**새 `.py` 모듈을 추가할 때** `pyproject.toml`의 `[tool.setuptools] py-modules` 목록에도 반드시 추가할 것. v0.3.0까지 이 목록이 업데이트되지 않아 `related.py`/`comments.py`가 brew 배포판에서 누락되어, 실제 설치한 사용자는 검색만 해도 `ModuleNotFoundError`로 죽는 상태였음 (v0.3.1에서 수정). 로컬 개발 중에는 `python3 yp.py`로 직접 실행하므로 이 문제가 드러나지 않는다 — 릴리즈 전엔 `python3 -m build --sdist`로 실제 패키징 결과물에 모든 모듈이 포함되는지 확인.

## Demo GIFs

`assets/demo.gif` 하나만 둔다 — `assets/demo.tape` ([vhs](https://github.com/charmbracelet/vhs)) 스크립트로 생성됨. 검색 → 선택 → 카드 재생 → `q` 복귀만 담은 ~14초 클립이다 (기능별로 쪼갠 5개 GIF를 v0.8.0에서 이걸로 대체했다). 재생성 시:
- `brew install vhs`, 검색어는 실제 업로드 영상만 나오는 걸로 (라이브 방송/과거 라이브 아카이브는 이 환경에서 HLS 스트림 오픈이 잘 실패함 — `python3.11 -c "from searcher import search; ..."`로 먼저 결과를 확인하고 `duration`이 있는 항목을 고를 것)
- **음소거는 `state.json`의 `volume`이 아니라 시스템 출력으로 해야 한다.** 카드가 `vol N`을 그대로 화면에 띄우므로 yp 볼륨을 0으로 두면 GIF에 `vol 0`이 박힌다. macOS는 `osascript -e "set volume output muted true"` → 녹화 → `... muted false`
- 검색이 끝날 때까지의 8초 남짓은 tape에서 `Hide`/`Show`로 잘라낸다. 안 자르면 클립의 절반이 빈 화면이 된다
- `vhs assets/demo.tape` 실행 → `assets/demo.gif` 생성. 생성 후 `ffmpeg -i assets/demo.gif -vf fps=1 /tmp/f%02d.png`로 프레임을 뽑아 **눈으로 확인할 것** — 녹화가 성공해도 화면이 옳다는 보장은 없다
