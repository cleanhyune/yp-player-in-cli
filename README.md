# yp

터미널에서 유튜브를 검색하고 오디오만 재생합니다. 브라우저가 필요 없습니다.

> **Keywords:** youtube cli, youtube audio, terminal music player, youtube terminal, macos audio player, yt-dlp cli, mpv youtube, background music terminal

```
yp "lofi hip hop"
```

```
? 재생할 영상을 선택하세요:
❯ Lofi Hip Hop Radio 📚 - Beats to Relax/Study to [1:12:34]
  lofi hip hop radio 🎧 - beats to sleep/chill to [24:00:00]
  Lofi Girl - Study Mix 2024 [2:08:45]
  ...
```

### 검색 & 화살표 선택

![검색 및 선택](assets/demo-search.gif)

### `g`로 타임라인 이동

![타임라인 이동](assets/demo-seek.gif)

### `9` / `0`로 볼륨 조절

![볼륨 조절](assets/demo-volume.gif)

### `a`로 자동재생 켜기/끄기

![자동재생 토글](assets/demo-autoplay.gif)

### `q`로 종료 후 재검색

![종료 후 재검색](assets/demo-exit.gif)

## 설치

```bash
brew install cleanhyune/yp/yp
```

## 사용법

```bash
yp "검색어"           # 검색 후 화살표로 선택, 오디오 재생
yp                    # 최근 재생 목록에서 바로 고르기 (yp -r / yp --recent 도 동일)
```

재생 중 키보드 컨트롤:

| 키 | 동작 |
|---|---|
| `Space` | 일시정지 / 재개 |
| `←` / `→` | 5초 앞뒤 탐색 |
| `9` / `0` | 볼륨 조절 |
| `n` | 다음 자동재생 영상으로 바로 건너뛰기 |
| `q` | 종료 후 재검색 |
| `g` | 시간 입력 후 해당 구간으로 이동 (예: `0710` → 7:10, `012930` → 1:29:30) |
| `t` | 현재 영상의 최상위 댓글을 같은 화면에서 스크롤로 보기 (`j`/`k`/`Space`, `q`로 닫기). 100개 단위로, 맨 아래에 닿으면 다음 100개를 이어서 불러옴. 페이저 안에서 `s`로 인기순/최신순 전환 |
| `a` | 자동재생 켜기/끄기 (기본값: 켜짐, 다음 실행에도 유지) |

댓글은 답글 없이 최상위 댓글만 보여줍니다. 기본 정렬은 인기순인데 유튜브의 인기순 랭킹은 요청마다 순서가 흔들려서, 이미 본 댓글을 걸러내다 보면 두 번째 페이지부터는 100개보다 적게 나옵니다 (실측 100 → 83 → 75개). 정확히 100개씩 보고 싶으면 `s`로 최신순으로 바꾸면 됩니다 — 최신순은 순서가 안정적이라 중복도 누락도 없습니다.

영상이 끝까지 재생되면 유튜브의 "연관 동영상" 중 같은 채널의 영상을 우선해 자동으로 이어서 재생합니다. 다음 영상이 정해지면 재생 상태줄에 "다음: 제목 · 채널"이 표시되고, `n`으로 바로 넘어갈 수 있습니다. `a`로 끄거나 `q`로 직접 종료하면 새 검색어 입력 프롬프트로 돌아옵니다. `Enter`만 누르면 종료.

30초 이상 듣다가 중간에 끈 영상은 다음에 다시 재생할 때 그 위치부터 이어서 재생합니다. 최근 재생 20개는 `~/.config/yp/history.json`에 남고, `yp`를 검색어 없이 실행하면 그 목록에서 바로 고를 수 있습니다.

## 요구 사항

- macOS
- [Homebrew](https://brew.sh)

`mpv`는 설치 시 자동으로 함께 설치됩니다.

## 라이선스

MIT
