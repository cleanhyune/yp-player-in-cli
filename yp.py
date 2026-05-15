import sys
import questionary
from searcher import search
from selector import select_video
from player import play, check_mpv


def get_first_query() -> str:
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    return questionary.text("검색어를 입력하세요:").ask() or ""


def main():
    if not check_mpv():
        print("mpv가 설치되어 있지 않습니다. 아래 명령어로 설치하세요:")
        print("  brew install mpv")
        sys.exit(1)

    query = get_first_query()

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

        url = select_video(videos)
        if url is None:
            query = questionary.text("다시 검색하세요:").ask() or ""
            continue

        print("스트림 연결 중... (길이에 따라 수 초 걸릴 수 있습니다)")
        play(url)
        print()

        query = questionary.text("다음 검색어 (엔터로 종료):").ask() or ""


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)
