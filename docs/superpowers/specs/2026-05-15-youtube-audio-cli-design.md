# YouTube Audio CLI (yp) — Design Spec

**Date:** 2026-05-15  
**Status:** Approved  
**Platform:** macOS only

---

## Overview

A terminal CLI tool that searches YouTube and plays only the audio stream, without opening a browser. Designed for discreet listening at work.

**Usage:**
```bash
yp "검색어"
```

---

## Architecture

```
cli_yp/
├── yp.py            # 진입점, 메인 루프
├── searcher.py      # YouTube 검색 (yt-dlp Python API)
├── selector.py      # 화살표 선택 UI (questionary)
├── player.py        # mpv 오디오 재생
└── requirements.txt
```

### Dependencies

| 도구 | 설치 | 역할 |
|------|------|------|
| `yt-dlp` | `pip install yt-dlp` | YouTube 검색 + 스트림 URL 추출 |
| `questionary` | `pip install questionary` | 화살표 키 선택 UI |
| `mpv` | `brew install mpv` | 오디오 재생 (키보드 컨트롤 내장) |

---

## Component Design

### `searcher.py`
- `yt-dlp`의 `YoutubeDL` Python API 사용
- 검색 쿼리: `ytsearch10:<검색어>` (최대 10개 결과)
- 각 결과에서 추출: `title`, `webpage_url`, `duration`
- 반환 타입: `list[dict]` — `[{"title": str, "url": str, "duration": int}, ...]`

### `selector.py`
- `questionary.select()`로 인터랙티브 선택 UI 렌더링
- 표시 형식: `제목 [HH:MM:SS]`
- 선택된 항목의 `url` 반환

### `player.py`
- `subprocess.run()`으로 `mpv --no-video --ytdl-format=bestaudio <url>` 실행
- mpv 내장 키보드 컨트롤:
  - `Space` — 일시정지 / 재개
  - `←` / `→` — 5초 탐색
  - `9` / `0` — 볼륨 조절
  - `q` — 재생 종료
- mpv 프로세스 종료 시 메인 루프로 제어권 반환

### `yp.py` (메인 루프)
- 첫 실행: `sys.argv[1]`을 검색어로 사용
- 재생 후: 새 검색어 입력 프롬프트 표시 (빈 입력 또는 `Ctrl+C`로 종료)
- 루프: 검색 → 선택 → 재생 → 검색 반복

---

## Error Handling

| 상황 | 처리 |
|------|------|
| `mpv` 미설치 | 시작 시 체크 → `brew install mpv` 안내 후 종료 |
| 검색 결과 없음 | 안내 메시지 출력 → 재검색 프롬프트 |
| `Ctrl+C` | 어느 단계에서든 `KeyboardInterrupt` 캐치 → 클린 종료 |
| yt-dlp 오류 | 에러 메시지 출력 → 재검색 프롬프트 |

---

## Installation

```bash
# 1. mpv 설치
brew install mpv

# 2. Python 의존성 설치
pip install yt-dlp questionary

# 3. 실행
python yp.py "로파이 음악"

# 4. 편의를 위해 alias 추가 (선택)
echo 'alias yp="python /Users/johyun/side-project/cli_yp/yp.py"' >> ~/.zshrc
```

---

## Non-Goals

- Windows / Linux 지원 없음
- 재생목록(플레이리스트) 기능 없음
- 다운로드 기능 없음 (스트리밍만)
- YouTube 외 다른 플랫폼 지원 없음
