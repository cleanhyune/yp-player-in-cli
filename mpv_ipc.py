from __future__ import annotations

import itertools
import json
import queue
import socket
import threading
import time


class MpvError(Exception):
    pass


class _Pending:
    def __init__(self):
        self.event = threading.Event()
        self.payload: dict | None = None
        self.error: Exception | None = None


class MpvClient:
    """mpv JSON IPC 클라이언트.

    리더 스레드 하나가 소켓을 줄 단위로 읽는다. request_id가 있는 줄은 대기 중인
    command() 호출자에게, event가 있는 줄은 events 큐로 간다. 소켓이 끊기면 대기 중인
    호출은 모두 MpvError로 깨우고 큐에 {"event": "mpv-exited"}를 한 번 넣는다.
    """

    def __init__(self, socket_path: str):
        self._path = socket_path
        self.events: queue.Queue = queue.Queue()
        self._sock: socket.socket | None = None
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._closed = threading.Event()

    def connect(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(self._path)
                break
            except OSError:
                sock.close()
                if time.monotonic() >= deadline:
                    raise MpvError(f"mpv IPC 소켓에 연결할 수 없습니다: {self._path}")
                time.sleep(0.1)
        self._sock = sock
        threading.Thread(target=self._read_loop, daemon=True).start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def command(self, *args, timeout: float = 5.0):
        if self._sock is None or self._closed.is_set():
            raise MpvError("mpv 연결이 닫혔습니다")
        rid = next(self._ids)
        slot = _Pending()
        with self._lock:
            self._pending[rid] = slot
        line = json.dumps({"command": list(args), "request_id": rid}, ensure_ascii=False) + "\n"
        try:
            with self._send_lock:
                self._sock.sendall(line.encode("utf-8"))
        except OSError as e:
            with self._lock:
                self._pending.pop(rid, None)
            raise MpvError(str(e))
        if not slot.event.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise MpvError(f"mpv 응답 시간 초과: {args[0]}")
        if slot.error is not None:
            raise slot.error
        assert slot.payload is not None
        if slot.payload.get("error") != "success":
            raise MpvError(str(slot.payload.get("error", "unknown")))
        return slot.payload.get("data")

    def observe(self, observe_id: int, name: str) -> None:
        self.command("observe_property", observe_id, name)

    def request_log_messages(self, level: str = "error") -> None:
        self.command("request_log_messages", level)

    def close(self) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _read_loop(self) -> None:
        assert self._sock is not None
        buf = b""
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    self._dispatch(msg)
        except OSError:
            pass
        finally:
            self._closed.set()
            try:
                self._sock.close()
            except OSError:
                pass
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for slot in pending:
                slot.error = MpvError("mpv 연결이 닫혔습니다")
                slot.event.set()
            self.events.put({"event": "mpv-exited"})

    def _dispatch(self, msg: dict) -> None:
        if "request_id" in msg:
            with self._lock:
                slot = self._pending.pop(msg["request_id"], None)
            if slot is not None:
                slot.payload = msg
                slot.event.set()
        elif "event" in msg:
            self.events.put(msg)
