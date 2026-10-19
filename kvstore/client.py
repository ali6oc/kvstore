"""A minimal Python client for the kvstore server."""

import socket


class KVClientError(Exception):
    """Raised when the server replies with an error."""


class KVClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 6399, timeout: float = 5.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._file = self._sock.makefile("rwb")

    def _call(self, line: str) -> str:
        self._file.write((line + "\n").encode("utf-8"))
        self._file.flush()
        reply = self._file.readline()
        if not reply:
            raise KVClientError("connection closed by server")
        text = reply.decode("utf-8").rstrip("\n")
        if text.startswith("ERR"):
            raise KVClientError(text[4:])
        return text

    def ping(self) -> bool:
        return self._call("PING") == "PONG"

    def set(self, key: str, value: str) -> None:
        self._call(f"SET {key} {value}")

    def get(self, key: str):
        reply = self._call(f"GET {key}")
        return None if reply == "NOT_FOUND" else reply[len("VALUE "):]

    def delete(self, key: str) -> bool:
        return self._call(f"DEL {key}") == "DELETED"

    def keys(self):
        reply = self._call("KEYS")[len("KEYS"):].strip()
        return reply.split(" ") if reply else []

    def count(self) -> int:
        return int(self._call("COUNT").split(" ")[1])

    def compact(self) -> None:
        self._call("COMPACT")

    def close(self) -> None:
        try:
            self._call("QUIT")
        except (KVClientError, OSError):
            pass
        self._file.close()
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
