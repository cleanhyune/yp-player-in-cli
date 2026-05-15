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

재생이 끝나면 새 검색어 입력 프롬프트로 돌아옵니다. `Enter`만 누르면 종료.

## Requirements

- macOS
- [Homebrew](https://brew.sh)

`mpv`는 설치 시 자동으로 함께 설치됩니다.

## License

MIT
