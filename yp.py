import sys
import threading
import warnings
warnings.filterwarnings("ignore")
import questionary
from searcher import search
from selector import select_video, NEXT_PAGE, PREV_PAGE
from player import play, check_mpv, is_autoplay_enabled
from related import fetch_next, extract_video_id


def get_first_query() -> str:
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    return questionary.text("검색어를 입력하세요:").ask() or ""


def _start_prefetch(url, played_ids):
    if not is_autoplay_enabled():
        return None, None
    box = {}
    thread = threading.Thread(
        target=lambda: box.__setitem__("result", fetch_next(url, set(played_ids))),
        daemon=True,
    )
    thread.start()
    return thread, box


def main():
    if not check_mpv():
        print("mpv가 설치되어 있지 않습니다. 아래 명령어로 설치하세요:")
        print("  brew install mpv")
        sys.exit(1)

    try:
        _run(get_first_query())
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)


def _run(query: str):
    while query:
        print(f"\n'{query}' 검색 중...")
        try:
            videos = search(query)
        except Exception as e:
            print(f"검색 오류: {e}")
            query = questionary.text("다시 검색하세요:").ask() or ""
            continue

        if not videos:
            print("검색 결과가 없습니다.")
            query = questionary.text("다시 검색하세요:").ask() or ""
            continue

        MAX_PAGES = 3
        page = 1
        while True:
            result = select_video(videos, page=page, max_pages=MAX_PAGES)
            if result == NEXT_PAGE:
                page = min(page + 1, MAX_PAGES)
            elif result == PREV_PAGE:
                page = max(page - 1, 1)
            elif result is None:
                query = questionary.text("다시 검색하세요:").ask() or ""
                break
            else:
                played_ids = {extract_video_id(result)}
                thread, box = _start_prefetch(result, played_ids)
                print("스트림 연결 중... (길이에 따라 수 초 걸릴 수 있습니다)")
                try:
                    reason = play(result)
                    while reason == "eof" and is_autoplay_enabled():
                        if thread is None:
                            break
                        thread.join()
                        next_video = box.get("result")
                        if next_video is None:
                            break
                        result = next_video["url"]
                        played_ids.add(extract_video_id(result))
                        print(f"🔁 자동재생: {next_video['title']} · {next_video['channel']}")
                        thread, box = _start_prefetch(result, played_ids)
                        reason = play(result)
                except KeyboardInterrupt:
                    print("\n재생을 중단합니다.")
                print()
                query = questionary.text("다음 검색어 (엔터로 종료):").ask() or ""
                break


if __name__ == "__main__":
    main()
