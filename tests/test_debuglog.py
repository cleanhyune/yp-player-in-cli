import logging

import debuglog


def test_disabled_when_env_missing_or_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for env in ({}, {"YP_DEBUG": ""}, {"YP_DEBUG": "0"}):
        assert debuglog.setup(env) is None
        debuglog.get("x").debug("버려진다", exc_info=True)
    assert not (tmp_path / "yp" / "debug.log").exists()


def test_one_writes_to_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = debuglog.setup({"YP_DEBUG": "1"})
    assert path == str(tmp_path / "yp" / "debug.log")
    try:
        raise ValueError("원인")
    except ValueError:
        debuglog.get("related").debug("연관 영상 조회 실패", exc_info=True)
    text = (tmp_path / "yp" / "debug.log").read_text(encoding="utf-8")
    assert "yp.related 연관 영상 조회 실패" in text
    assert "ValueError: 원인" in text


def test_other_value_is_a_path(tmp_path):
    target = tmp_path / "custom" / "yp.log"
    assert debuglog.setup({"YP_DEBUG": str(target)}) == str(target)
    debuglog.get("player").debug("기록")
    assert "기록" in target.read_text(encoding="utf-8")


def test_setup_replaces_previous_handlers(tmp_path):
    debuglog.setup({"YP_DEBUG": str(tmp_path / "a.log")})
    debuglog.setup({"YP_DEBUG": str(tmp_path / "b.log")})
    assert len(logging.getLogger("yp").handlers) == 1
    debuglog.setup({})
