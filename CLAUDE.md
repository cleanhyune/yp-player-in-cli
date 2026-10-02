# yp — AI Agent Guide

CLI tool that searches YouTube and plays audio-only via mpv. macOS only.

## Structure

```
yp.py         # Entry point: search→select→PlayerSession loop, autoplay chain (n/p로 앞뒤 이동), -r/--recent, -c/--channel
searcher.py   # YouTube 검색: innertube youtubei/v1/search 파싱 (제목·채널·길이·업로드 시기 age). yt-dlp 안 씀
selector.py   # Arrow-key selection UI via questionary (search results + recent history). 항목은 두 줄: 제목 / 흐린 '채널 · 길이 · 시기'
player.py     # PlayerSession: one mpv process per playback session, driven over JSON IPC
mpv_ipc.py    # mpv JSON IPC client (request/response matching + event queue). No yp knowledge
tui.py        # Terminal ownership: cbreak key reader, alt-screen card/comments renderers, timecode prompt
history.py    # ~/.config/yp/history.json (recent + resume position) and state.json (volume, autoplay)
related.py    # Next-video lookup by parsing the watch page's related-video sidebar; same-channel first
channel.py    # -c 채널 모드: 채널 해석(이름/@핸들/URL) + 최신 업로드 30개 단위 페이징 + ChannelQueue(재생 순서)
innertube.py  # watch 페이지 ytInitialData fetch + youtubei/v1/next·search POST. searcher/related/comments가 공유. yp 지식 없음
comments.py   # CommentFeed/ReplyFeed: innertube 커서 페이징 + 블록 포맷터 ('t'로 댓글, Enter로 답글)
debuglog.py   # YP_DEBUG가 켜졌을 때만 삼킨 예외를 ~/.config/yp/debug.log에 남기는 로거. yp 지식 없음
```

## Key Decisions

- **yt-dlp Python API** (not subprocess) — `YoutubeDL` class with `extract_flat: True` for channel listing (`channel.py`) and stream resolution (`player.py`); `ytdlp_common.SilentLogger` suppresses its Python version deprecation warnings
- **Search comes from innertube, not yt-dlp, so the upload age can be shown** — `searcher.search` POSTs `youtubei/v1/search` (`innertube.search`, `hl=ko`) and parses `videoRenderer` entries (`videoId`, `title.runs`, `ownerText.runs`, `lengthText`, `publishedTimeText`); `lockupViewModel`/`shortsLockupViewModel` (playlists, shorts) are skipped. yt-dlp's flat search only turns *English* `publishedTimeText` ("3 years ago") into a timestamp, and only with `youtubetab:approximate_date`; with the project's `lang: ko` (Korean titles, commit 7df050e) it comes back as "3년 전", the parse fails and the raw text is dropped (2026-09 verified). So `age` is YouTube's own relative string ("5일 전", "1년 전") shown verbatim, like comment ages — search results never carry an absolute date; that would cost one watch-page fetch per video. Pages are 20 (first) + ~18 per `continuationItemRenderer` token, so 30 results take 2 requests (~2.1s measured, same as yt-dlp's `ytsearch30`); the token sits *beside* the `itemSectionRenderer`, not inside it, and `_walk` finds both by key so the first-page and continuation shapes need no separate code. Like `related.py`, this parse is self-maintained: a YouTube reshape means fixing `searcher._parse_page`, not `pip install -U yt-dlp`
- **Autoplay via sidebar scraping, not yt-dlp** — `related.py` fetches the watch page HTML directly and parses the `ytInitialData` JSON blob for the real "related videos" sidebar (`lockupViewModel` entries under `contents.twoColumnWatchNextResults.secondaryResults...`). An earlier version used yt-dlp's `RD<video_id>` mix playlist, but that mix doesn't exist for many videos (e.g. broadcast/drama clips) — see [[autoplay_related_videos]] memory. yt-dlp deliberately doesn't expose this sidebar, so this parsing is unofficial and self-maintained: if YouTube changes the JSON shape, only fixing `related.py` (not `pip install -U yt-dlp`) will help. `fetch_next()` swallows every exception internally so a broken parse can never propagate into the autoplay loop
- **`n`/`p`는 재생목록 위의 이동이지 '다음/이전 추천'이 아니다** — `yp._play_session`이 지나온 곡을 `chain: list[dict]` + `index`로 들고 있고, `n`/자동재생은 index를 올리며(체인 끝일 때만 `_Prefetch`로 다음 곡을 뽑아 append) `p`는 내린다. 체인을 자르지 않기 때문에 `p` 뒤의 `n`은 떠났던 바로 그 곡으로 돌아온다 — 별도 로직이 아니라 자료구조에서 따라온다. 세 가지가 여기 붙어 있다: (1) player는 재생목록을 모르고 `session.has_prev` 불리언만 본다. 첫 곡에서 `p`는 스트림을 끊지 않고 메시지만 띄운다 (같은 곡을 다시 여는 스트림 재해석 2~3초를 아끼고, mpv 기본 `p`=일시정지가 새어나가지 않게 삼킨다). (2) `history.resume_position`은 **세션 안에서 그 자리를 처음 재생할 때만** 적용한다(`seen: set[int]`) — 되감아 돌아왔는데 방금 넘긴 지점으로 되돌리면 "이전 곡"이 아니다. `history`의 기록 자체는 그대로 남아 다음 *실행*의 이어보기는 살아 있다. (3) 체인 끝에서 돈 `_Prefetch`는 `prefetches: dict[index, _Prefetch]`에 남겨 `p`로 지나쳤다 `n`으로 돌아와도 같은 조회를 두 번 하지 않는다 (`_Prefetch`는 완료 시 스스로 힌트를 올리므로, 재사용할 때는 `_hint()`로 직접 세워줘야 한다)
- **Channel mode replaces the sidebar with the channel's upload order** — `yp -c <이름|@핸들|URL>` resolves the channel in `channel.resolve_channel` (a name goes through `ytsearch5` and takes the most common `channel_id` among the hits; `@handle`/URL reads the channel page with `playlist_items=1:1`), lists uploads via yt-dlp flat extraction of `/channel/<id>/videos` in `PAGE_SIZE=30` chunks (`playlist_items="31:60"` etc., 0.5–1s each), and hands `_play_session` a `find_next=queue.next_after` so autoplay/`n` walk **down the list (newest → oldest)** instead of the related-video sidebar; when the channel runs out the session ends with "채널 영상을 모두 들었습니다." `_Prefetch(url, played_ids, channel, session, find_next=None)` is the only seam: the default `find_next` is the old `fetch_next` sidebar lookup, and the stream pre-resolution (stage 2) applies to both modes unchanged. `ChannelQueue.ensure(n)` fetches pages lazily; `_run_channel` keeps one selector page ahead (`ensure(page*10+1)`) so `select_video`'s "다음 페이지 ▶" button appears, and a fetch failure marks the channel exhausted rather than raising. After a session ends, channel mode returns to the same list, not the search prompt
- **Autoplay prefetch resolves the stream too, so track changes skip ytdl_hook** — `yp._Prefetch` has two stages: `fetch_next` (sidebar lookup, hint goes up immediately) and then `player.resolve_stream(url, session.strategy)`, which walks the same `_attempt_order` chain through the yt-dlp Python API (`bestaudio/best`, `cookiesfrombrowser=chrome` on the cookie step) and returns a `Stream(url, client, duration)` with the direct googlevideo URL. `PlayerSession.load(url, start, stream)` then does `loadfile stream.url` first — mpv opens a direct URL in ~0.5s versus ~1.9s for a YouTube URL through ytdl_hook, and end-to-end (`n` pressed → next track audible, pty-driven A/B, 3 trials each) the gap went 1.9–2.6s → 0.7–1.1s (2026-09 measured), and when YouTube is blocking, the ~5.7s-per-attempt retries happen in the background during the previous track instead of in the gap. If the direct URL fails (`end-file reason=error`, e.g. expired after ~6h), `load()` falls through to the old YouTube-URL chain, so nothing is lost. Success records `stream.client` as `strategy`. **The `ytdl` option is never toggled at runtime.** An earlier version sent `set ytdl no` before a direct URL and `set ytdl yes` before the chain, and that broke `p` back to a track without a stream: mpv implements `ytdl` as load/unload of the built-in `ytdl_hook.lua` (`player/scripting.c`), hook registration is asynchronous, so the `loadfile` right after `set ytdl yes` ran without the hook and died with `Failed to recognize file format` (5/5 with a ~2s gap, and when the gap was tens of ms mpv skipped the reload entirely because the old script hadn't exited yet — 2026-09-29 measured on mpv 0.41). The first attempt always died and the failure only surfaced when `web_embedded` and the cookie attempt also failed, which is why it looked intermittent. Instead `ytdl` stays on and `--script-opts=ytdl_hook-exclude=googlevideo.com/` keeps the hook off direct URLs in both `on_load` and `on_load_fail`, so a dead direct URL fails in ~0.2s without spawning yt-dlp. Two details: `resolve_stream` swallows every exception (a `None` stream just means the old path), and while a direct URL plays, `_direct` makes `_on_property` ignore `media-title` — mpv reports the file name `videoplayback` there, which would clobber the title `set_track` put on the card. The first track of a session has no prefetch and still goes through ytdl_hook; true 0s gaps would need mpv-side playlist prefetch and are out of scope
- **One mpv per playback session, Python owns the terminal** — `PlayerSession.start()` launches `mpv --no-video --no-terminal --idle=yes --input-ipc-server=<tmp>/yp_mpv_<pid>.sock` once (per-process path — a fixed path let a second `yp` unlink the first one's socket; `quit()` removes it); each video is a `loadfile` over IPC, so autoplay/`n` transitions have no process restart and volume/pause state survives. Because mpv has no terminal, `tui.KeyReader` reads stdin in cbreak mode (not raw — Ctrl+C must still raise `KeyboardInterrupt`) and forwards unknown keys to mpv via the `keypress` IPC command, so mpv's default bindings (space, arrows, 9/0, m) keep working. Intercepted keys: `q` quit, `n` next, `p` prev, `a` autoplay toggle, `g` timecode prompt, `t` comments pager, `(`/`)` volume ±10 (Shift+9/0 — the terminal delivers only the shifted character, so this is the only way to see Shift; sent as `add volume`, mpv clamps the range) (and inside the pager, `s` toggles comment sort).
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
  - **스타일은 bold/dim + 강조색 하나(`accent`, 터미널 기본 팔레트 cyan)** — 다색은 사용자
    터미널 테마와 충돌하므로 쓰지 않는다. 강조색은 진행바의 채운 칸·손잡이(●)와 상단 테두리의
    `♪ NOW PLAYING`/`‖ PAUSED` 띠에만 쓴다 (2026-09-23 사용자 요청으로 허용). `NO_COLOR`가
    있으면 셋 다 끈다. 규칙 하나: **평문으로 자르고 패딩한 뒤 스타일을 입힌다.**
    `display_width()`는 ANSI를 셀 줄 모르므로 순서가 뒤집히면 CJK 제목에서 폭이 조용히 깨진다.
  - **카드 스킨의 글리프는 한 막대 안에서 EAW 등급이 같아야 한다** — 진행바 `━─●`(모두 A),
    볼륨 게이지 `▮▯`(모두 N), 상단 띠 `♪`/`‖`(모두 A). 테두리는 `╭╮╰╯`(A). 다른 글리프로
    바꿀 때 `unicodedata.east_asian_width`를 먼저 확인할 것 — `tests/test_tui.py`가 짝을 검사한다.
- **Single event queue** — `MpvClient.events` receives mpv events, key events from `KeyReader`, `comments-ready/-failed`, `comments-more/-failed`, `comments-reload/-failed`, `replies-ready/-failed` and `replies-more/-failed` from the comments thread, and `redraw` from the prefetch thread. `PlayerSession.load()` consumes only this queue. This is what makes "`end-file reason=stop` right after `n` means `next`, otherwise `quit`" a safe interpretation
- **Per-file options via `set` before `loadfile`** — the attempt chain and resume position are applied with `set ytdl-raw-options ...` / `set start <sec>|none` immediately before each `loadfile`, not as `loadfile` positional options (whose positional layout changed in mpv 0.38)
- **The attempt chain ends in a cookie attempt; the winner is remembered but re-probed daily** — `_ATTEMPTS` is `(android, no cookies)` → `(web_embedded, no cookies)` → `(web_safari, cookies-from-browser=chrome)`, and `load()` retries the next entry whenever `end-file reason=error` comes back. The last entry exists because YouTube now demands bot attestation on the player endpoint per-IP: when that kicks in, **every** anonymous request dies with `Sign in to confirm you're not a bot` regardless of yt-dlp version or client (2026-09: verified across 2025.10.14 / 2026.06.09 / 2026.07.04 / 2026.08.19 × 8 clients — all identical), and only cookies get through. Cookies narrow the usable clients, though: `android`/`ios` then fail with `No video formats found!` and `tv` with `The page needs to be reloaded`, so only the web family works — `web_safari` was fastest (7.3s vs web_embedded 8.1s / mweb 8.6s / web 9.7s). Note there is deliberately **no error-string matching** for the bot message: the existing retry-on-error loop already covers it and won't rot when yt-dlp rewords the error
- **Cookie use is opt-in, asked once** — `state.json` carries `cookies: bool | None`. `None` means never asked: `yp._play_session` asks `_ask_cookies()` (questionary confirm, default No) right after the user picks a track, which is the last point before the alt screen, and saves the answer with the rest of the state; without a tty it silently picks `False`. `PlayerSession(cookies=...)` and `resolve_stream(url, strategy, cookies)` both go through `_attempt_order(preferred, cookies)`, which drops the `web_safari`+cookies entry when declined — so a remembered `strategy="web_safari"` degrades to the default anonymous order instead of re-attaching cookies. Editing `cookies` in `state.json` (or deleting the key) is the only way to change the answer later
- **Swallowed exceptions are logged, never printed** — `related.fetch_next`, `player.resolve_stream`, the comments worker and `channel` catch everything by design (a broken parse must never reach the playback loop), which makes a YouTube JSON reshape look like "autoplay just stopped". `debuglog.get(name)` gives each module a `yp.<name>` logger and every such `except` calls `_log.debug(..., exc_info=True)`. `debuglog.setup()` in `main()` reads `YP_DEBUG`: unset/`0` attaches a `NullHandler`, `1` writes to `~/.config/yp/debug.log`, any other value is used as the path. It is a file and not stderr because the terminal is in the alt screen while playing
- **Who owns what in that chain** — `PlayerSession.strategy` holds the `player_client` that last actually opened a stream, and `_attempt_order()` rotates it to the front. The signal for "this attempt worked" is `reason == "eof" or self.duration > 0` — duration arriving is the only proof the stream opened, so pressing `q` mid-resolution isn't recorded as a success. `player.py` knows nothing about clocks: **`yp._play_session` owns the re-probe policy**, discarding the remembered strategy when `state["probed"] != _today()` so that once a day the chain runs from the top again. Without that, a session that got blocked once would sit on the slower cookie path forever even after YouTube unblocked the IP — and keep attaching the user's account cookies (watch-history side effect) for no reason. The probe is placed at `PlayerSession` construction, i.e. right after the user picks a track and is already waiting for a stream, never mid-autoplay-chain. Cost of a wasted probe while still blocked: **~5.7s** end-to-end through mpv (2026-09 measured: 13.3s resolve for the full chain vs 7.6s straight to `web_safari`; raw yt-dlp failures are only 1.3s + 2.1s, the rest is mpv respawning ytdl_hook per attempt). When *not* blocked the probe costs nothing, since `android` succeeds on the first try. `strategy`/`probed` live in `state.json`; deleting them forces an immediate re-probe
- **No more /tmp state files or Lua** — volume and autoplay persist in `~/.config/yp/state.json`; playback history and resume positions in `history.json` (atomic write via `os.replace`). Resume applies when the saved position is ≥30s and <95% of duration. `time-pos` `null` at end-of-file is ignored so the last real position isn't wiped before it's recorded
- **mpv errors surface via IPC** — `request_log_messages error`; log lines are printed only on the final fallback attempt (same effect as the old per-attempt `--msg-level`)
- **Comments come from innertube, not yt-dlp** — `comments.py` gets the comment-section continuation token from the watch page's `ytInitialData` (shared `innertube.fetch_initial_data`, the same fetch `related.py` uses) and POSTs it to `youtubei/v1/next` (`innertube.next_continuation`, WEB client context, no API key). yt-dlp was dropped for comments because its comment dict has no reply count and there is no "replies of *this* comment" option — replies are fetched parent 1..k in order, one request each, so opening the 20th comment's replies would cost 20 requests. innertube gives 20 root comments per page (0.7s, ~2s including the watch page), each with `toolbar.replyCount` and a reply continuation token, and 10 replies per page (0.4s) with a further token (2026-09 measured). Requests use `hl=ko`, so `publishedTime` arrives already translated ("6일 전", "6년 전(수정됨)") and is shown verbatim — there is no age-translation layer anymore. Like counts are YouTube's own abbreviations ("31만", "6.4천") and are also shown verbatim (`likes: str | None`), never parsed back to integers. Page-to-page overlap measured 0 with real cursors, but `CommentFeed` still dedupes by comment id as insurance (yt-dlp has seen pinned comments repeat under newest-first). This parsing is unofficial like `related.py`: if YouTube reshapes the JSON, fix `comments._parse_page` — `pip install -U yt-dlp` won't help. Field-level parse failures degrade to `None`/`0` per field; only a response with neither comment items nor payloads raises, which the worker turns into a `-failed` event
- **Sort tokens are learned once and handed to the next feed** — the first `next` response's `commentsHeaderRenderer` sort menu carries both the Top and Newest continuation tokens. `CommentFeed.sort_tokens` keeps them and `_switch_comment_sort` passes them to the replacement `CommentFeed(url, sort=new, sort_tokens=...)`, so toggling `s` skips the watch page. A feed created with `sort="new"` and no tokens fetches the Top page once just to learn them
- **Pager is a block cursor; `player.py` owns the stack** — `tui.Pager` holds `header` lines plus blocks (`add_items(blocks, payloads)` records `anchors[i]` = first line of block i, `payloads[i]` = whatever the owner attached; the pager never looks inside). **Wrapping happens in the pager, not the formatter**: `format_comment` emits one logical line per paragraph (5-space indent, no width), and `Pager.reflow(cols, height)` re-derives `lines`/`anchors` from the stored `header`/`_blocks` with `tui.wrap_line` (display-width aware, break at the last space or hard-break CJK runs, continuation lines keep the leading indent). `player._redraw` and `_modal_key` call it before rendering or handling a key, so a terminal resize re-wraps on the next frame and the cursor (a block index) survives unchanged. The old formatter cut at 76 *characters*, which for Korean is ~152 cells and was silently truncated by `_fit`. `cursor` is a block index and is always on screen: `j/k` move by block and drag `top` minimally (a block taller than the screen aligns its first line to the top), `space/PgDn` scroll by a screen and pull the cursor to the first block whose first line is visible. `handle_key` returns `"close"` (`q`/`ESC`/`←`), `"open"` (`Enter`) or `None`. The next-page trigger is `more and status is None and at_last_item()` — no line arithmetic, so `comments_height` matters only for what the cursor drags into view. `render_comments` bolds the selected block's lines (after `_fit`, per the style rule) and swaps the first line's leading space for `▶`; the footer counts blocks (`2/20+`, `+` = more). Replies are a second `Pager` pushed on top: `PlayerSession._parent_pager` keeps the root pager (scroll and cursor intact) while `_modal` is the reply pager, `_feed`/`_reply_feed` are the matching feeds, and `_close_pager` pops. Every fill/failure event carries the `pager` object it was fetched for and is applied only when `self._modal is ev["pager"]` (plus the load-generation check) — a response for a pager the user already closed, or for a different reply thread, is dropped. `Enter` on a comment whose `reply_token` is `None` does nothing (its meta line has no `답글 N`), and is also ignored while `status` is set (a fetch is in flight — the thread slot is single)
- **`from __future__ import annotations`** in `selector.py`/`related.py`/`innertube.py`/`comments.py` — required for `str | None` syntax on Python 3.9
- `duration` from yt-dlp is `float`, so `format_duration()` casts to `int` first

## Commands

**로컬 개발은 `python3` 대신 `python3.11`(또는 3.10+)로 실행할 것.** macOS 기본/Xcode `python3`는 3.9.6인데, yt-dlp가 최근 릴리즈부터 Python 3.10+를 요구하기 시작해서 3.9 환경엔 2025.10.14가 pip으로 설치 가능한 마지막 버전으로 영구히 고정된다. YouTube는 추출 로직을 자주 바꾸고 yt-dlp가 그때그때 패치를 내는 구조라, 이 오래된 버전으로 로컬 테스트하면 "The page needs to be reloaded" 같은 간헐적 추출 에러를 실제보다 훨씬 자주 만나게 된다. `brew install python@3.11`로 설치 가능.

```bash
# Run
python3.11 yp.py "검색어"

# Test
python3.11 -m pytest tests/ -v

# 삼킨 예외 보기 (자동재생이 이유 없이 끊길 때)
YP_DEBUG=1 python3.11 yp.py "검색어"   # → ~/.config/yp/debug.log

# Install dependencies
python3.11 -m pip install yt-dlp questionary pytest
brew install mpv
```

## Data Flow

```
search(query) -> [{"title", "channel", "url", "duration", "age": "5일 전"|None}, ...]  # 30개 한번에(innertube 2요청). age는 YouTube 표기 그대로
# select_video/select_recent 항목은 questionary.Choice 하나에 '\n'으로 두 줄을 넣는다 (제목 / 흐린 메타). 항목 사이에는 빈 Separator 한 줄 (한 항목 = 3행, 10개면 30행).
#   Choice.value는 예전 그대로 '제목 · 채널 [길이]' 한 줄이라 url 역참조와 테스트가 화면 형식에 묶이지 않는다
select_video(videos, page, max_pages, message=...) -> url | NEXT_PAGE | PREV_PAGE | None
select_recent(items) -> url | NEW_SEARCH | None

session = PlayerSession(volume, autoplay, tty, strategy, cookies); session.start()   # 대체 화면 진입. cookies=False면 쿠키 시도를 뺀다
session.set_track(title, channel, duration)   # 카드에 미리 올릴 메타데이터 (mpv의 media-title은 늦게 온다)
session.set_notice(text | None)               # 안내 슬롯. time-pos가 올 때마다 지워진다(첫 번째만이 아님). 메인 스레드 전용
session.has_prev = bool                       # 돌아갈 이전 곡이 있는지. 매 load() 전에 yp가 세운다
session.load(url, start, stream=None) -> "eof" | "quit" | "next" | "prev" | "error"   # blocks until the file ends
                # stream: 프리페치가 푼 Stream(url, client, duration). 있으면 ytdl 없이 직접 URL을 먼저 연다
resolve_stream(url, strategy, cookies=True) -> Stream | None  # yt-dlp Python API로 시도 체인을 걸어 직접 URL을 푼다. 예외는 삼킨다(YP_DEBUG면 기록)
session.autoplay / session.volume / session.position           # read after load()
session.strategy                                               # 실제로 스트림을 연 player_client
session.errors                                # 버퍼된 mpv 오류. quit() 뒤에 출력할 것
session.quit()                                # 대체 화면 이탈

render_playing(state, cols, rows) -> list[str]                 # 길이는 항상 rows
render_comments(state, pager, cols, rows) -> list[str]         # 길이는 항상 rows
comments_height(rows) -> int                                   # 페이저 본문 높이. 유일한 출처
progress_bar(position, duration, width) -> str                 # ANSI 제거 시 폭이 정확히 width

resolve_channel(query) -> {"id", "name", "handle"} | None   # 이름 → ytsearch5 최다 채널, @핸들/URL → 채널 페이지
channel_videos(channel_id, page, name=None) -> [search와 같은 모양]   # 30개 단위
queue = ChannelQueue(channel); queue.ensure(n); queue.next_after(url) -> dict | None; queue.exhausted
# yp._play_session(video, find_next=None, end_note=...): chain(list) + index로 재생목록을 들고 돈다. "eof"(autoplay on)/"next"는
#   index를 올리고(체인 끝이면 prefetch 결과를 append), "prev"는 내린다
fetch_next(url, played_ids, current_channel) -> {"title", "channel", "url"} | None   # 같은 채널 우선
#   _Prefetch.result() -> 위 dict + {"stream": Stream | None, "duration": float}   # 2단계: 스트림까지 풀어 붙인다

feed = CommentFeed(url, sort="top"|"new", sort_tokens=None)   # innertube 커서 페이징. 첫 페이지가 sort_tokens를 배운다
feed.next_page() -> (comments, more)           # 최상위 댓글, YouTube가 주는 대로 20개씩. feed.shown = 표시 누적
                # comments 항목: {"id", "author", "text", "likes": "31만"|None, "age": "6일 전"|None,
                #                 "reply_count": int, "reply_token": str|None}   likes/age는 YouTube 표기 그대로
replies = ReplyFeed(reply_token); replies.next_page() -> (replies, more)   # 답글 10개씩. 항목 모양은 같고 reply_token=None
format_header(title, note=None) -> [줄]        # 페이저 머리 (제목, ━, 정렬 안내, 빈 줄)
format_comment(c, index, reply=False) -> [줄]  # 블록 한 개. reply=True면 작성자 앞에 ↳. 첫 칸은 ▶ 자리. 본문은 문단 단위 논리 줄(접지 않음)
pager = Pager(header, more, hint); pager.add_items(blocks, payloads); pager.replace_items(header, blocks, payloads)
pager.reflow(cols, height)                     # 논리 줄을 터미널 폭에 맞춰 접어 lines/anchors를 다시 만든다. 그리기·키 처리 전에 player가 부른다
wrap_line(line, width) -> [줄]                 # 표시 폭 기준. 공백 우선, 없으면 칸 수로. 이어지는 줄은 앞 공백만큼 들여쓴다
pager.handle_key(key, height) -> "close" | "open" | None;  pager.selected_payload();  pager.at_last_item()
history.record_start / record_position / clear_position / resume_position / recent
history.load_state() -> {"volume", "autoplay", "strategy", "probed", "cookies"}
                # strategy = 지난번 스트림을 연 player_client | None
                # probed   = strategy를 마지막으로 재탐색한 날짜(ISO) | None. 오늘이 아니면 재탐색
                # cookies  = Chrome 쿠키 허용 여부 | None(아직 묻지 않음 → yp가 첫 재생 전 1회 질문)
history.save_state(volume, autoplay, strategy=None, probed=None, cookies=None)
debuglog.setup(env=None) -> 로그 파일 경로 | None   # YP_DEBUG=1 → ~/.config/yp/debug.log, 그 외 값은 경로
debuglog.get(name) -> logging.Logger            # 'yp.<name>'. 삼킨 예외마다 .debug(..., exc_info=True)
yp._today() -> "YYYY-MM-DD"                    # 재탐색 판정용. 테스트는 이것만 고정한다
```

## Distribution

- Main repo: https://github.com/cleanhyune/yp-player-in-cli
- Homebrew tap: https://github.com/cleanhyune/homebrew-yp (`Formula/yp.rb`)

CI: `.github/workflows/test.yml` runs pytest on 3.10/3.11/3.12 and builds the sdist on every push to `main` and every PR, and fails if any top-level `.py` module is missing from the tarball — the same check that would have caught the v0.3.0 packaging bug below. `tests/` is tracked (it was git-ignored until 2026-09-21; `docs/` still is).

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
