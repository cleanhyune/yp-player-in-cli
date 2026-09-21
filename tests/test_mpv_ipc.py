import json
import os
import queue
import socket
import tempfile
import threading
import time

import pytest

from mpv_ipc import MpvClient, MpvError


class FakeMpv:
    """줄 단위 JSON 요청을 받아 handler(msg) -> list[dict] 응답을 돌려주는 가짜 mpv IPC 서버.

    macOS의 AF_UNIX 경로 길이 제한(104바이트) 때문에 pytest tmp_path가 아니라 /tmp 아래에 소켓을 만든다.
    delay를 주면 그만큼 기다렸다가 bind해서 클라이언트의 connect 재시도를 검증할 수 있다.
    """

    def __init__(self, handler, delay: float = 0.0):
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.path = os.path.join(self.dir, "sock")
        self._handler = handler
        self._delay = delay
        self._server = None
        self.conn = None
        self.connected = threading.Event()
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        time.sleep(self._delay)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(1)
        self.conn, _ = self._server.accept()
        self.connected.set()
        buf = b""
        while True:
            try:
                chunk = self.conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                self.received.append(msg)
                for reply in self._handler(msg):
                    self.conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))

    def push(self, event: dict):
        self.connected.wait(2)
        self.conn.sendall((json.dumps(event) + "\n").encode("utf-8"))

    def close(self):
        self.connected.wait(2)
        # 서버 스레드가 recv()에 막혀 있는 소켓이다. Linux는 close()만으로는 상대에게
        # 끊김이 가지 않으므로(recv가 돌아올 때까지 유지) shutdown으로 먼저 깨운다.
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
        self._server.close()


def _ok(msg, data=None):
    return [{"error": "success", "data": data, "request_id": msg["request_id"]}]


def test_command_returns_data_from_matching_response():
    server = FakeMpv(lambda msg: _ok(msg, data="0.41.0"))
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        assert client.command("get_property", "mpv-version") == "0.41.0"
        assert server.received[0]["command"] == ["get_property", "mpv-version"]
    finally:
        client.close()
        server.close()


def test_event_lines_go_to_queue_and_do_not_confuse_responses():
    def handler(msg):
        # 응답보다 이벤트가 먼저 오는 상황
        return [{"event": "property-change", "name": "volume", "data": 50}] + _ok(msg, data=True)

    server = FakeMpv(handler)
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        assert client.command("get_property", "pause") is True
        ev = client.events.get(timeout=1)
        assert ev == {"event": "property-change", "name": "volume", "data": 50}
    finally:
        client.close()
        server.close()


def test_pushed_events_arrive_without_any_command():
    server = FakeMpv(lambda msg: _ok(msg))
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        server.push({"event": "end-file", "reason": "eof"})
        assert client.events.get(timeout=1)["reason"] == "eof"
    finally:
        client.close()
        server.close()


def test_command_raises_mpv_error_on_error_reply():
    server = FakeMpv(lambda msg: [{"error": "property not found", "request_id": msg["request_id"]}])
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        with pytest.raises(MpvError, match="property not found"):
            client.command("get_property", "nope")
    finally:
        client.close()
        server.close()


def test_command_times_out_when_no_response():
    server = FakeMpv(lambda msg: [])
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        with pytest.raises(MpvError, match="시간 초과"):
            client.command("get_property", "pause", timeout=0.3)
    finally:
        client.close()
        server.close()


def test_server_close_emits_mpv_exited_and_marks_closed():
    server = FakeMpv(lambda msg: _ok(msg))
    client = MpvClient(server.path)
    client.connect(timeout=2)
    server.close()
    assert client.events.get(timeout=1) == {"event": "mpv-exited"}
    time.sleep(0.05)
    assert client.closed is True
    with pytest.raises(MpvError):
        client.command("get_property", "pause")


def test_connect_retries_until_socket_appears():
    server = FakeMpv(lambda msg: _ok(msg), delay=0.4)
    client = MpvClient(server.path)
    client.connect(timeout=3)
    try:
        assert client.command("get_property", "pause") is None
    finally:
        client.close()
        server.close()


def test_connect_raises_when_socket_never_appears():
    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "never")
    client = MpvClient(path)
    with pytest.raises(MpvError, match="연결할 수 없습니다"):
        client.connect(timeout=0.3)


def test_observe_and_request_log_messages_send_expected_commands():
    server = FakeMpv(lambda msg: _ok(msg))
    client = MpvClient(server.path)
    client.connect(timeout=2)
    try:
        client.observe(3, "time-pos")
        client.request_log_messages("error")
        cmds = [m["command"] for m in server.received]
        assert ["observe_property", 3, "time-pos"] in cmds
        assert ["request_log_messages", "error"] in cmds
    finally:
        client.close()
        server.close()


def test_server_close_fails_pending_command_and_emits_mpv_exited_once():
    server = FakeMpv(lambda msg: [])  # 응답 없음 -> command()가 대기 중 소켓이 끊김
    client = MpvClient(server.path)
    client.connect(timeout=2)

    result: dict = {}

    def call():
        try:
            client.command("get_property", "pause", timeout=3)
        except MpvError as e:
            result["error"] = e

    t = threading.Thread(target=call)
    t.start()
    server.connected.wait(2)
    time.sleep(0.1)  # 요청이 _pending에 등록될 시간을 준다
    server.close()
    t.join(timeout=2)

    assert isinstance(result.get("error"), MpvError)
    assert client.events.get(timeout=1) == {"event": "mpv-exited"}
    time.sleep(0.05)
    assert client.events.empty()


def test_local_close_does_not_emit_mpv_exited_and_marks_closed_immediately():
    server = FakeMpv(lambda msg: _ok(msg))
    client = MpvClient(server.path)
    client.connect(timeout=2)
    server.connected.wait(2)
    client.close()
    assert client.closed is True
    time.sleep(0.1)
    assert client.events.empty()
    server.close()
