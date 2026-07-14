from yt_dlp import YoutubeDL

from ytdlp_common import SilentLogger


def search(query: str, max_results: int = 30) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "logger": SilentLogger(),
        "http_headers": {"Accept-Language": "ko-KR,ko;q=0.9"},
        "extractor_args": {"youtube": {"lang": ["ko"]}},
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
            "channel": entry.get("channel") or entry.get("uploader") or "알 수 없음",
            "url": url,
            "duration": entry.get("duration") or 0,
        })
    return results
