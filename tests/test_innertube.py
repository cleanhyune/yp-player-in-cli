import io
import json
from unittest.mock import patch

import pytest

import innertube


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(body: bytes):
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return _Resp(body)
    return fake, calls


def test_next_continuation_posts_the_token_with_a_web_client_context():
    fake, calls = _urlopen(b'{"ok": 1}')
    with patch("innertube.urllib.request.urlopen", fake):
        assert innertube.next_continuation("TOKEN") == {"ok": 1}
    req = calls[0]
    assert req.full_url.startswith("https://www.youtube.com/youtubei/v1/next")
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data)
    assert body["continuation"] == "TOKEN"
    assert body["context"]["client"]["clientName"] == "WEB"
    assert body["context"]["client"]["hl"] == "ko"


def test_next_continuation_passes_the_language_through():
    fake, calls = _urlopen(b"{}")
    with patch("innertube.urllib.request.urlopen", fake):
        innertube.next_continuation("T", hl="en")
    assert json.loads(calls[0].data)["context"]["client"]["hl"] == "en"


def test_fetch_initial_data_extracts_the_json_blob_from_the_watch_page():
    html = b'<html><script>var ytInitialData = {"contents": {"a": 1}};</script></html>'
    fake, calls = _urlopen(html)
    with patch("innertube.urllib.request.urlopen", fake):
        assert innertube.fetch_initial_data("abcdefghijk") == {"contents": {"a": 1}}
    assert calls[0].full_url == "https://www.youtube.com/watch?v=abcdefghijk"


def test_fetch_initial_data_raises_when_the_blob_is_missing():
    fake, _ = _urlopen(b"<html>nothing</html>")
    with patch("innertube.urllib.request.urlopen", fake), pytest.raises(ValueError):
        innertube.fetch_initial_data("abcdefghijk")


def test_search_posts_the_query_with_a_korean_web_client_context():
    fake, calls = _urlopen(b'{"contents": {}}')
    with patch("innertube.urllib.request.urlopen", fake):
        assert innertube.search("아이유 라이브") == {"contents": {}}
    req = calls[0]
    assert req.full_url.startswith("https://www.youtube.com/youtubei/v1/search")
    assert req.get_method() == "POST"
    body = json.loads(req.data)
    assert body["query"] == "아이유 라이브"
    assert "continuation" not in body
    assert body["context"]["client"]["clientName"] == "WEB"
    assert body["context"]["client"]["hl"] == "ko"


def test_search_with_a_continuation_token_sends_the_token_instead_of_the_query():
    fake, calls = _urlopen(b"{}")
    with patch("innertube.urllib.request.urlopen", fake):
        innertube.search(continuation="TOKEN")
    body = json.loads(calls[0].data)
    assert body["continuation"] == "TOKEN"
    assert "query" not in body
