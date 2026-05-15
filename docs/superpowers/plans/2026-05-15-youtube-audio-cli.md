# YouTube Audio CLI (yp) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `yp "검색어"` 명령어로 YouTube를 검색하고 오디오만 재생하는 macOS CLI 도구를 만든다.

**Architecture:** `searcher.py`가 yt-dlp API로 YouTube를 검색하고, `selector.py`가 questionary로 화살표 선택 UI를 제공하며, `player.py`가 mpv를 subprocess로 실행해 오디오를 재생한다. `yp.py`가 이 세 컴포넌트를 루프로 묶어 재생 후 재검색 흐름을 만든다.

**Tech Stack:** Python 3.9+, yt-dlp, questionary, mpv (brew), pytest, unittest.mock

---

## File Structure

```
cli_yp/
├── yp.py                  # 진입점, 메인 루프
├── searcher.py            # YouTube 검색 (yt-dlp YoutubeDL API)
├── selector.py            # 화살표 선택 UI (questionary)
├── player.py              # mpv 오디오 재생 (subprocess)
├── requirements.txt       # Python 의존성
└── tests/
    ├── test_searcher.py
    ├── test_selector.py
    └── test_player.py
```

---

## Task 1: 프로젝트 초기 세팅

**Files:**
- Create: `requirements.txt`
- Create: `tests/__init__.py`

- [ ] **Step 1: requirements.txt 작성**

```
yt-dlp
questionary
pytest
```

- [ ] **Step 2: 의존성 설치**

```bash
pip install yt-dlp questionary pytest
```

Expected: 설치 완료 메시지, 에러 없음

- [ ] **Step 3: mpv 설치 확인**

```bash
which mpv || brew install mpv
```

Expected: `/opt/homebrew/bin/mpv` 같은 경로 출력

- [ ] **Step 4: tests 디렉토리 생성**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 5: 커밋**

```bash
git init
git add requirements.txt tests/__init__.py
git commit -m "chore: initial project setup"
```

---

## Task 2: searcher.py — YouTube 검색

**Files:**
- Create: `searcher.py`
- Create: `tests/test_searcher.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_searcher.py`:
```python
from unittest.mock import patch, MagicMock
from searcher import search

def test_search_returns_list_of_dicts():
    mock_info = {
        "entries": [
            {"title": "로파이 음악 1시간", "webpage_url": "https://youtube.com/watch?v=abc", "duration": 3600},
            {"title": "집중 BGM", "webpage_url": "https://youtube.com/watch?v=def", "duration": 1800},
        ]
    }
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("로파이")

    assert len(results) == 2
    assert results[0]["title"] == "로파이 음악 1시간"
    assert results[0]["url"] == "https://youtube.com/watch?v=abc"
    assert results[0]["duration"] == 3600

def test_search_returns_empty_list_when_no_entries():
    mock_info = {"entries": []}
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("존재하지않는검색어xyz")

    assert results == []

def test_search_skips_entries_missing_url():
    mock_info = {
        "entries": [
            {"title": "정상 영상", "webpage_url": "https://youtube.com/watch?v=abc", "duration": 100},
            {"title": "URL 없는 영상", "duration": 100},
        ]
    }
    with patch("searcher.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        results = search("테스트")

    assert len(results) == 1
    assert results[0]["title"] == "정상 영상"
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_searcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'searcher'`

- [ ] **Step 3: searcher.py 구현**

`searcher.py`:
```python
from yt_dlp import YoutubeDL


def search(query: str, max_results: int = 10) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

    entries = info.get("entries", []) or []
    results = []
    for entry in entries:
        url = entry.get("webpage_url") or entry.get("url")
        if not url:
            continue
        results.append({
            "title": entry.get("title", "제목 없음"),
            "url": url,
            "duration": entry.get("duration") or 0,
        })
    return results
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_searcher.py -v
```

Expected: 3개 테스트 모두 PASSED

- [ ] **Step 5: 커밋**

```bash
git add searcher.py tests/test_searcher.py
git commit -m "feat: add YouTube search via yt-dlp"
```

---

## Task 3: selector.py — 화살표 선택 UI

**Files:**
- Create: `selector.py`
- Create: `tests/test_selector.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_selector.py`:
```python
from unittest.mock import patch
from selector import format_duration, select_video

def test_format_duration_seconds():
    assert format_duration(90) == "1:30"

def test_format_duration_hours():
    assert format_duration(3661) == "1:01:01"

def test_format_duration_zero():
    assert format_duration(0) == "0:00"

def test_select_video_returns_url_of_chosen():
    videos = [
        {"title": "로파이 음악", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
        {"title": "집중 BGM", "url": "https://youtube.com/watch?v=def", "duration": 1800},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = "로파이 음악 [1:00:00]"
        result = select_video(videos)

    assert result == "https://youtube.com/watch?v=abc"

def test_select_video_returns_none_when_cancelled():
    videos = [
        {"title": "로파이 음악", "url": "https://youtube.com/watch?v=abc", "duration": 3600},
    ]
    with patch("selector.questionary.select") as mock_select:
        mock_select.return_value.ask.return_value = None
        result = select_video(videos)

    assert result is None
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_selector.py -v
```

Expected: `ModuleNotFoundError: No module named 'selector'`

- [ ] **Step 3: selector.py 구현**

`selector.py`:
```python
import questionary


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def select_video(videos: list[dict]) -> str | None:
    choices = [
        f"{v['title']} [{format_duration(v['duration'])}]"
        for v in videos
    ]
    label_to_url = {
        f"{v['title']} [{format_duration(v['duration'])}]": v["url"]
        for v in videos
    }

    chosen = questionary.select(
        "재생할 영상을 선택하세요:",
        choices=choices,
    ).ask()

    if chosen is None:
        return None
    return label_to_url[chosen]
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_selector.py -v
```

Expected: 4개 테스트 모두 PASSED

- [ ] **Step 5: 커밋**

```bash
git add selector.py tests/test_selector.py
git commit -m "feat: add arrow-key video selector UI"
```

---

## Task 4: player.py — mpv 오디오 재생

**Files:**
- Create: `player.py`
- Create: `tests/test_player.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_player.py`:
```python
from unittest.mock import patch, call
from player import play, check_mpv

def test_play_calls_mpv_with_correct_args():
    url = "https://youtube.com/watch?v=abc"
    with patch("player.subprocess.run") as mock_run:
        play(url)

    mock_run.assert_called_once_with(
        ["mpv", "--no-video", "--ytdl-format=bestaudio", url],
        check=False,
    )

def test_check_mpv_returns_true_when_installed():
    with patch("player.shutil.which", return_value="/opt/homebrew/bin/mpv"):
        assert check_mpv() is True

def test_check_mpv_returns_false_when_not_installed():
    with patch("player.shutil.which", return_value=None):
        assert check_mpv() is False
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_player.py -v
```

Expected: `ModuleNotFoundError: No module named 'player'`

- [ ] **Step 3: player.py 구현**

`player.py`:
```python
import shutil
import subprocess


def check_mpv() -> bool:
    return shutil.which("mpv") is not None


def play(url: str) -> None:
    subprocess.run(
        ["mpv", "--no-video", "--ytdl-format=bestaudio", url],
        check=False,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_player.py -v
```

Expected: 3개 테스트 모두 PASSED

- [ ] **Step 5: 커밋**

```bash
git add player.py tests/test_player.py
git commit -m "feat: add mpv audio player wrapper"
```

---

## Task 5: yp.py — 메인 루프

**Files:**
- Create: `yp.py`

- [ ] **Step 1: yp.py 구현**

`yp.py`:
```python
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

        play(url)

        query = questionary.text("\n다음 검색어 (엔터로 종료):").ask() or ""


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        sys.exit(0)
```

- [ ] **Step 2: 동작 확인 (수동 테스트)**

```bash
python yp.py "lofi music"
```

Expected:
1. `'lofi music' 검색 중...` 출력
2. 영상 목록이 화살표 선택 UI로 표시
3. 선택하면 mpv가 실행되며 오디오 재생
4. `q` 누르면 재생 종료 후 다음 검색어 프롬프트 표시
5. 엔터 누르면 종료

- [ ] **Step 3: 전체 테스트 통과 확인**

```bash
pytest tests/ -v
```

Expected: 10개 테스트 모두 PASSED

- [ ] **Step 4: alias 추가 (선택)**

```bash
echo 'alias yp="python /Users/johyun/side-project/cli_yp/yp.py"' >> ~/.zshrc
source ~/.zshrc
```

- [ ] **Step 5: 최종 커밋**

```bash
git add yp.py
git commit -m "feat: add main loop - search, select, play, repeat"
```
