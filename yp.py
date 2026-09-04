from __future__ import annotations

import sys
import threading
import warnings
warnings.filterwarnings("ignore")
import questionary

import history
from player import PlayerError, PlayerSession, check_mpv
from related import extract_video_id, fetch_next
from searcher import search
from selector import (NEW_SEARCH, NEXT_PAGE, PREV_PAGE, format_duration,
                      select_recent, select_video)

MAX_PAGES = 3


class _Prefetch:
    """현재 영상이 재생되는 동안 다음 자동재생 후보를 백그라운드에서 찾아둔다.

    played_ids는 메인 루프가 계속 바꾸므로 set()으로 스냅샷을 떠서 넘긴다. 결과가
    나오면 세션 상태줄에 힌트를 올리는데, 세션이 이미 닫혔으면 버린다.
    """

    def __init__(self, url: str, played_ids: set, channel: str | None, session: PlayerSession):
        self._box: dict = {}
        self._thread = threading.Thread(
            target=self._run, args=(url, set(played_ids), channel, session), daemon=True)
        self._thread.start()

    def _run(self, url, played_ids, channel, session):
        next_video = fetch_next(url, played_ids, channel)
        self._box["result"] = next_video
        if next_video and not session.closed:
            session.set_next_hint(f"{next_video['title']} · {next_video['channel']}")

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def result(self):
        self._thread.join()
        return self._box.get("result")


def _ask(prompt: str) -> str:
    return questionary.text(prompt).ask() or ""


def main():
    if not check_mpv():
        print("mpv가 설치되어 있지 않습니다. 아래 명령어로 설치하세요:")
        print("  brew install mpv")
        sys.exit(1)

    try:
        args = sys.argv[1:]
        if not args or args in (["-r"], ["--recent"]):
            _run_recent()
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


def _play_session(video: dict) -> None:
    """영상 하나로 시작해 자동재생/n 키로 이어지는 체인을 mpv 프로세스 하나에서 돌린다."""
    state = history.load_state()
    session = PlayerSession(volume=state["volume"], autoplay=state["autoplay"],
                            tty=sys.stdin.isatty())
    played_ids: set = set()
    try:
        # start()도 정리 범위 안에 둔다. 기동 중 Ctrl-C가 들어와도 finally가
        # session.quit()을 돌려 고아 mpv와 복원되지 않은 cbreak 모드를 막는다.
        try:
            session.start()
        except PlayerError as e:
            print(f"mpv를 시작할 수 없습니다: {e}")
            return
        while True:
            video_id = extract_video_id(video["url"])
            played_ids.add(video_id)
            history.record_start(video)
            session.on_position(lambda seconds, _id=video_id: history.record_position(_id, seconds))
            session.set_next_hint(None)
            prefetch = _Prefetch(video["url"], played_ids, video.get("channel"), session)

            start = history.resume_position(video_id, video.get("duration") or 0)
            if start:
                print(f"⏩ {format_duration(start)}부터 이어서 재생합니다")
            print("스트림 연결 중... (길이에 따라 수 초 걸릴 수 있습니다)")
            reason = session.load(video["url"], start=start)

            if session.duration > 0:
                # 자동재생 항목은 duration 0으로 기록됐다. mpv가 관측한 실제 길이를
                # 위치 정리보다 먼저 채워야 다음 실행의 이어보기 가드가 이를 본다.
                history.record_duration(video_id, session.duration)
            if reason == "eof":
                history.clear_position(video_id)
            elif session.position > 0:
                history.record_position(video_id, session.position)

            if reason == "error":
                print("재생에 실패했습니다.")
                break
            if reason == "quit":
                break
            if reason == "eof" and not session.autoplay:
                break

            if not prefetch.done:
                print("다음 영상을 찾는 중...")
            next_video = prefetch.result()
            if next_video is None:
                print("다음 영상을 찾지 못했습니다.")
                break
            video = {**next_video, "duration": 0}
            print(f"🔁 자동재생: {video['title']} · {video['channel']}")
    except KeyboardInterrupt:
        print("\n재생을 중단합니다.")
    finally:
        try:
            history.save_state(session.volume, session.autoplay)
        except Exception:
            pass
        session.quit()


if __name__ == "__main__":
    main()
