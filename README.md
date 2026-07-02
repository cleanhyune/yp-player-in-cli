# yp

Search YouTube and play audio-only from the terminal. No browser needed.

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

## Install

```bash
brew install cleanhyune/yp/yp
```

## Usage

```bash
yp "검색어"           # 검색 후 화살표로 선택, 오디오 재생
```

재생 중 키보드 컨트롤:

| 키 | 동작 |
|---|---|
| `Space` | 일시정지 / 재개 |
| `←` / `→` | 5초 앞뒤 탐색 |
| `9` / `0` | 볼륨 조절 |
| `q` | 종료 후 재검색 |
| `g` | 시간 입력 후 해당 구간으로 이동 (예: `0710` → 7:10, `012930` → 1:29:30) |
| `t` | 현재 영상의 인기 댓글을 새 터미널 창에 표시 |
| `a` | 자동재생 켜기/끄기 (기본값: 켜짐) |

영상이 끝까지 재생되면 유튜브의 실제 "연관 동영상"을 찾아 자동으로 이어서 재생합니다. `a`로 끄거나 `q`로 직접 종료하면 새 검색어 입력 프롬프트로 돌아옵니다. `Enter`만 누르면 종료.

## Requirements

- macOS
- [Homebrew](https://brew.sh)

`mpv`는 설치 시 자동으로 함께 설치됩니다.

## License

MIT
