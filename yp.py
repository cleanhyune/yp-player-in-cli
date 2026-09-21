from __future__ import annotations

import sys
import threading
import warnings
from datetime import date
warnings.filterwarnings("ignore")
import questionary

import debuglog
import history
from channel import ChannelQueue, resolve_channel
from player import PlayerError, PlayerSession, check_mpv, resolve_stream
from related import extract_video_id, fetch_next
from searcher import search
from selector import (NEW_SEARCH, NEXT_PAGE, PREV_PAGE, format_duration,
                      select_recent, select_video)

MAX_PAGES = 3
CHANNEL_MAX_PAGES = 30   # 선택기 페이지(10개) 기준. 채널 목록은 필요할 때 30개씩 더 받는다


def _hint(video: dict) -> str:
    """카드 아래 '↳ 다음 곡' 한 줄. 조회 결과와 체인 재사용이 같은 문구를 쓰도록 모은다."""
    return f"{video['title']} · {video['channel']}"


def _today() -> str:
    return date.today().isoformat()


class _Prefetch:
    """현재 영상이 재생되는 동안 다음 곡을 찾고(find_next, 기본은 사이드바) 스트림 URL까지 풀어둔다.

    played_ids는 메인 루프가 계속 바꾸므로 set()으로 스냅샷을 떠서 넘긴다. 결과가
    나오면 세션 상태줄에 힌트를 올리는데, 세션이 이미 닫혔으면 버린다.
    """

    def __init__(self, url: str, played_ids: set, channel: str | None, session: PlayerSession,
                 find_next=None):
        self._box: dict = {}
        ids = set(played_ids)
        find_next = find_next or (lambda: fetch_next(url, ids, channel))
        self._thread = threading.Thread(target=self._run, args=(find_next, session), daemon=True)
        self._thread.start()

    def _run(self, find_next, session):
        next_video = find_next()
        if next_video is None:
            self._box["result"] = None
            return
        # 힌트는 스트림을 푸는 1~2초를 기다리지 않고 곡을 찾은 즉시 올린다.
        if not session.closed:
            session.set_next_hint(_hint(next_video))
        # 직접 URL을 미리 풀어 곡 전환의 ytdl_hook(~1.9초)을 없앤다. None이면 load()가 예전 경로로 간다.
        stream = resolve_stream(next_video["url"], session.strategy, session.cookies)
        self._box["result"] = {**next_video, "stream": stream,
                               "duration": stream.duration if stream else 0}

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def result(self):
        self._thread.join()
        return self._box.get("result")


def _ask(prompt: str) -> str:
    return questionary.text(prompt).ask() or ""


def _ask_cookies() -> bool:
    print("YouTube가 봇 인증을 요구하면 yp는 마지막 수단으로 Chrome에 저장된 쿠키로 재시도할 수 있습니다.")
    print("로그인된 Chrome이면 yp로 들은 영상이 그 계정의 YouTube 시청 기록에 남을 수 있습니다.")
    print("이 선택은 한 번만 묻고 ~/.config/yp/state.json의 cookies 값으로 저장됩니다.")
    answer = questionary.confirm("Chrome 쿠키 사용을 허용할까요?", default=False).ask()
    return bool(answer)


def main():
    if not check_mpv():
        print("mpv가 설치되어 있지 않습니다. 아래 명령어로 설치하세요:")
        print("  brew install mpv")
        sys.exit(1)

    log_path = debuglog.setup()
    if log_path:
        print(f"디버그 로그: {log_path}")

    try:
        args = sys.argv[1:]
        if not args or args in (["-r"], ["--recent"]):
            _run_recent()
        elif args[0] in ("-c", "--channel") and len(args) > 1:
            _run_channel(" ".join(args[1:]))
        else:
            _run(" ".join(args))
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)


def _run_recent():
    items = history.recent()
    if not items:
        _run(_ask("검색어를 입력하세요:"))
        return
    chosen = select_recent(items)
    if chosen is None:
        return
    if chosen == NEW_SEARCH:
        _run(_ask("검색어를 입력하세요:"))
        return
    video = next(v for v in items if v["url"] == chosen)
    _play_session(video)
    print()
    _run(_ask("다음 검색어 (엔터로 종료):"))


def _run_channel(query: str):
    """채널의 최신 업로드를 고르고, 자동재생은 사이드바 대신 그 목록 순서(최신 → 과거)를 따른다."""
    print(f"\n'{query}' 채널 찾는 중...")
    channel = resolve_channel(query)
    if channel is None:
        print("채널을 찾지 못했습니다.")
        return
    handle = f" ({channel['handle']})" if channel.get("handle") else ""
    print(f"채널: {channel['name']}{handle}")
    queue = ChannelQueue(channel)
    page = 1
    while True:
        # 다음 페이지 ▶ 버튼이 나오려면 다음 선택기 페이지 첫 항목까지 받아둬야 한다.
        queue.ensure(page * 10 + 1)
        if not queue.videos:
            print("채널에 영상이 없습니다.")
            return
        result = select_video(queue.videos, page=page, max_pages=CHANNEL_MAX_PAGES,
                              message=f"채널 '{channel['name']}' 최신 영상:")
        if result == NEXT_PAGE:
            page = min(page + 1, CHANNEL_MAX_PAGES)
        elif result == PREV_PAGE:
            page = max(page - 1, 1)
        elif result is None:
            return
        else:
            video = next(v for v in queue.videos if v["url"] == result)
            _play_session(video, find_next=queue.next_after, end_note="채널 영상을 모두 들었습니다.")
            print()


def _run(query: str):
    while query:
        print(f"\n'{query}' 검색 중...")
        try:
            videos = search(query)
        except Exception as e:
            print(f"검색 오류: {e}")
            query = _ask("다시 검색하세요:")
            continue

        if not videos:
            print("검색 결과가 없습니다.")
            query = _ask("다시 검색하세요:")
            continue

        page = 1
        while True:
            result = select_video(videos, page=page, max_pages=MAX_PAGES)
            if result == NEXT_PAGE:
                page = min(page + 1, MAX_PAGES)
            elif result == PREV_PAGE:
                page = max(page - 1, 1)
            elif result is None:
                query = _ask("다시 검색하세요:")
                break
            else:
                video = next(v for v in videos if v["url"] == result)
                _play_session(video)
                print()
                query = _ask("다음 검색어 (엔터로 종료):")
                break


def _play_session(video: dict, find_next=None, end_note: str = "다음 영상을 찾지 못했습니다.") -> None:
    """영상 하나로 시작해 자동재생/n/p로 앞뒤를 오가는 체인을 mpv 프로세스 하나에서 돌린다.

    find_next(url) -> dict | None이 있으면 다음 곡을 사이드바 대신 그것으로 고른다 (채널 모드)."""
    state = history.load_state()
    # 하루에 한 번은 기억한 전략을 버리고 player의 기본 순서로 다시 탐색한다. YouTube가
    # IP 차단을 풀었을 때 느리고 쿠키까지 쓰는 경로에 영구히 머무르지 않기 위한 것이다.
    # 차단이 여전하면 익명 시도 두 칸이 지나가는 값만 물리고(2026-09 측정: android 1.3s +
    # web_embedded 2.1s) 곧바로 쿠키 경로로 떨어지며, 차단이 없으면 첫 시도가 성공하므로
    # 재탐색 비용은 0이다. 곡을 고른 직후라 사용자가 이미 스트림을 기다리는 시점이고,
    # 자동재생 체인 중간에는 끼지 않는다.
    today = _today()
    strategy = state["strategy"] if state["probed"] == today else None
    tty = sys.stdin.isatty()
    cookies = state["cookies"]
    if cookies is None:
        # 아직 대체 화면에 들어가기 전이라 평범한 프롬프트를 띄울 수 있는 마지막 지점이다.
        cookies = _ask_cookies() if tty else False
    session = PlayerSession(volume=state["volume"], autoplay=state["autoplay"],
                            tty=tty, strategy=strategy, cookies=cookies)
    # 지나온 곡을 그대로 들고 있는 재생목록. n/자동재생은 index를 올리고 p는 내린다.
    # 뒤로 갔다 다시 앞으로 와도 같은 곡으로 돌아오도록 자르지 않고 append만 한다.
    chain: list[dict] = [video]
    index = 0
    # 체인 끝에서 돌린 다음 곡 조회. p로 지나쳤다가 n으로 돌아와도 다시 조회하지 않는다.
    prefetches: dict[int, _Prefetch] = {}
    # 이 세션에서 한 번이라도 재생한 체인 위치. 되감아 돌아온 곡에 이어보기를 다시
    # 적용하지 않기 위한 것이다 — 방금 듣다 넘긴 지점으로 되돌리면 "이전 곡"이 아니다.
    seen: set[int] = set()
    played_ids: set = set()
    # 재생 중에는 대체 화면 안이라 print가 나갈 때 사라진다. 종료 사유는 여기 모았다가
    # session.quit()으로 화면을 나온 뒤에 찍는다.
    notes: list[str] = []
    try:
        # start()도 정리 범위 안에 둔다. 기동 중 Ctrl-C가 들어와도 finally가
        # session.quit()을 돌려 고아 mpv와 복원되지 않은 cbreak 모드를 막는다.
        try:
            session.start()
        except PlayerError as e:
            notes.append(f"mpv를 시작할 수 없습니다: {e}")
        else:
            while True:
                video = chain[index]
                video_id = extract_video_id(video["url"])
                played_ids.add(video_id)
                history.record_start(video)
                session.on_position(
                    lambda seconds, _id=video_id: history.record_position(_id, seconds))
                session.has_prev = index > 0
                session.set_next_hint(None)
                session.set_track(video["title"], video.get("channel"), video.get("duration"))

                prefetch = None
                if index + 1 < len(chain):
                    # 되감아 돌아온 자리다. 다음 곡을 이미 아니까 조회 없이 힌트만 세운다.
                    session.set_next_hint(_hint(chain[index + 1]))
                else:
                    prefetch = prefetches.get(index)
                    if prefetch is None:
                        prefetch = _Prefetch(video["url"], played_ids, video.get("channel"), session,
                                             find_next=(lambda url=video["url"]: find_next(url)) if find_next else None)
                        prefetches[index] = prefetch
                    elif prefetch.done and prefetch.result() is not None:
                        # 이미 끝난 조회를 재사용하는 경우 _Prefetch가 힌트를 다시
                        # 올려주지 않으므로 여기서 세운다.
                        session.set_next_hint(_hint(prefetch.result()))

                first_play = index not in seen
                seen.add(index)
                start = history.resume_position(
                    video_id, video.get("duration") or 0) if first_play else None
                notice = "스트림 연결 중..."
                if start:
                    notice = f"⏩ {format_duration(start)}부터 이어서 · {notice}"
                session.set_notice(notice)
                reason = session.load(video["url"], start=start, stream=video.get("stream"))

                if session.duration > 0:
                    # 자동재생 항목은 duration 0으로 기록됐다. mpv가 관측한 실제 길이를
                    # 위치 정리보다 먼저 채워야 다음 실행의 이어보기 가드가 이를 본다.
                    history.record_duration(video_id, session.duration)
                if reason == "eof":
                    history.clear_position(video_id)
                elif session.position > 0:
                    history.record_position(video_id, session.position)

                if reason == "error":
                    notes.append("재생에 실패했습니다.")
                    break
                if reason == "quit":
                    break
                if reason == "prev":
                    # player가 has_prev로 막으므로 index가 0 아래로 내려갈 일은 없다.
                    index -= 1
                    continue
                if reason == "eof" and not session.autoplay:
                    break

                if index + 1 < len(chain):
                    index += 1
                    continue
                if not prefetch.done:
                    session.set_notice("다음 영상을 찾는 중...")
                next_video = prefetch.result()
                if next_video is None:
                    notes.append(end_note)
                    break
                # 다음 곡 제목은 카드로 바로 올라가므로 따로 알릴 필요가 없다.
                chain.append({"duration": 0, **next_video})
                index += 1
    except KeyboardInterrupt:
        notes.append("재생을 중단합니다.")
    finally:
        try:
            history.save_state(session.volume, session.autoplay, session.strategy, today, cookies)
        except Exception:
            pass
        session.quit()

    # 여기부터는 원래 터미널이다.
    for line in session.errors:
        print(line)
    for line in notes:
        print(line)


if __name__ == "__main__":
    main()
