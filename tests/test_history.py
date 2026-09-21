import json
import os

import pytest

import history

URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "yp"


def _video(url=URL_A, title="영상A", channel="채널A", duration=600.0):
    return {"title": title, "channel": channel, "url": url, "duration": duration}


def test_record_start_creates_file_and_item(config_dir):
    history.record_start(_video())
    items = history.recent()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "aaaaaaaaaaa"
    assert item["title"] == "영상A" and item["channel"] == "채널A" and item["url"] == URL_A
    assert item["duration"] == 600.0 and item["position"] == 0.0
    assert "last_played_at" in item
    assert (config_dir / "history.json").exists()


def test_record_start_moves_existing_to_front_and_keeps_position():
    history.record_start(_video(URL_A))
    history.record_position("aaaaaaaaaaa", 120.0)
    history.record_start(_video(URL_B, title="영상B"))
    history.record_start(_video(URL_A))
    items = history.recent()
    assert [i["id"] for i in items] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert items[0]["position"] == 120.0


def test_recent_is_capped_at_max_items():
    for i in range(history.MAX_ITEMS + 5):
        history.record_start(_video(f"https://www.youtube.com/watch?v={i:011d}"))
    assert len(history.recent(limit=100)) == history.MAX_ITEMS
    assert history.recent(limit=3)[0]["id"] == f"{history.MAX_ITEMS + 4:011d}"


def test_record_position_and_clear():
    history.record_start(_video())
    history.record_position("aaaaaaaaaaa", 45.5)
    assert history.recent()[0]["position"] == 45.5
    history.clear_position("aaaaaaaaaaa")
    assert history.recent()[0]["position"] == 0.0


def test_record_position_for_unknown_id_is_noop():
    history.record_position("zzzzzzzzzzz", 10.0)
    assert history.recent() == []


def test_record_start_ignores_url_without_video_id():
    history.record_start(_video(url="https://example.com/"))
    assert history.recent() == []


def test_resume_position_thresholds():
    history.record_start(_video(duration=1000.0))
    history.record_position("aaaaaaaaaaa", 10.0)
    assert history.resume_position("aaaaaaaaaaa", 1000.0) is None      # 30초 미만
    history.record_position("aaaaaaaaaaa", 300.0)
    assert history.resume_position("aaaaaaaaaaa", 1000.0) == 300.0
    history.record_position("aaaaaaaaaaa", 960.0)
    assert history.resume_position("aaaaaaaaaaa", 1000.0) is None      # 95% 이후
    assert history.resume_position("aaaaaaaaaaa", 0) == 960.0          # 길이 모르면 비율 검사 생략
    assert history.resume_position("zzzzzzzzzzz", 1000.0) is None


def test_corrupt_history_is_treated_as_empty(config_dir):
    config_dir.mkdir(parents=True)
    (config_dir / "history.json").write_text("{not json", encoding="utf-8")
    assert history.recent() == []
    history.record_start(_video())
    assert len(history.recent()) == 1


def test_writes_are_atomic_no_temp_left_behind(config_dir):
    history.record_start(_video())
    leftovers = [n for n in os.listdir(config_dir) if n.startswith(".tmp")]
    assert leftovers == []
    data = json.loads((config_dir / "history.json").read_text(encoding="utf-8"))
    assert "items" in data


def test_state_defaults_and_roundtrip(config_dir):
    assert history.load_state() == {"volume": 100, "autoplay": True,
                                    "strategy": None, "probed": None, "cookies": None}
    history.save_state(volume=65, autoplay=False, strategy="web_safari", probed="2026-09-09",
                       cookies=True)
    assert history.load_state() == {"volume": 65, "autoplay": False,
                                    "strategy": "web_safari", "probed": "2026-09-09",
                                    "cookies": True}
    assert (config_dir / "state.json").exists()


def test_state_with_garbage_values_falls_back_to_defaults(config_dir):
    config_dir.mkdir(parents=True)
    (config_dir / "state.json").write_text(
        '{"volume": "loud", "autoplay": 1, "strategy": 7, "probed": 20260909, "cookies": "yes"}',
        encoding="utf-8")
    assert history.load_state() == {"volume": 100, "autoplay": True,
                                    "strategy": None, "probed": None, "cookies": None}


def test_state_written_by_an_older_yp_has_no_strategy(config_dir):
    """0.7.0 이전 state.json — 없는 키는 None이어야 하고, probed가 없으면 재탐색을 유발한다."""
    config_dir.mkdir(parents=True)
    (config_dir / "state.json").write_text('{"volume": 40, "autoplay": false}', encoding="utf-8")
    assert history.load_state() == {"volume": 40, "autoplay": False,
                                    "strategy": None, "probed": None, "cookies": None}


def test_record_duration_updates_item():
    history.record_start(_video(duration=0.0))
    assert history.recent()[0]["duration"] == 0.0
    history.record_duration("aaaaaaaaaaa", 1234.5)
    assert history.recent()[0]["duration"] == 1234.5


def test_record_duration_for_unknown_id_is_noop():
    history.record_duration("zzzzzzzzzzz", 100.0)
    assert history.recent() == []


def test_record_duration_restores_resume_ratio_guard():
    history.record_start(_video(duration=0.0))                   # 자동재생 항목은 길이를 모른다
    history.record_position("aaaaaaaaaaa", 990.0)
    assert history.resume_position("aaaaaaaaaaa", 0) == 990.0    # 길이를 모르면 가드가 꺼진다
    history.record_duration("aaaaaaaaaaa", 1000.0)
    item = history.recent()[0]
    assert history.resume_position("aaaaaaaaaaa", item["duration"]) is None


def test_non_dict_history_items_are_ignored(config_dir):
    config_dir.mkdir(parents=True)
    (config_dir / "history.json").write_text(
        json.dumps({"items": [1, "nope", None, {"id": "aaaaaaaaaaa", "title": "영상A"}]}),
        encoding="utf-8")
    assert history.recent() == [{"id": "aaaaaaaaaaa", "title": "영상A"}]
    history.record_start(_video())      # 예전엔 여기서 AttributeError로 죽었다
    assert [i["id"] for i in history.recent()] == ["aaaaaaaaaaa"]
