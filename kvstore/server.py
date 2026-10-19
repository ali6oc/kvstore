"""A tiny TCP server that exposes a KVStore over a line-based text protocol.

Protocol (one command per line, one reply per line):

    PING              -> PONG
    SET <key> <value> -> OK              (value may contain spaces)
    GET <key>         -> VALUE <value> | NOT_FOUND
    DEL <key>         -> DELETED | NOT_FOUND
    KEYS              -> KEYS <k1> <k2> ...
    COUNT             -> COUNT <n>
    COMPACT           -> OK
    QUIT              -> BYE   (then the connection closes)

Errors are reported as `ERR <message>`. Keys cannot contain spaces and values
cannot contain newlines, because the protocol is line-based.
"""

import socketserver

from .store import KVStore, KVStoreError

MAX_LINE = 64 * 1024  # refuse absurdly long lines


def handle_command(store: KVStore, line: str):
    """Run one protocol line against the store.

    Returns (reply, close_connection). Kept free of any socket code so it
    can be tested directly.
    """
    line = line.strip()
    if not line:
        return "ERR empty command", False

    parts = line.split(" ", 2)
    cmd = parts[0].upper()

    try:
        if cmd == "PING":
            return "PONG", False
        if cmd == "SET":
            if len(parts) < 3:
                return "ERR usage: SET <key> <value>", False
            store.put(parts[1], parts[2])
            return "OK", False
        if cmd == "GET":
            if len(parts) != 2:
                return "ERR usage: GET <key>", False
            value = store.get(parts[1])
            return ("NOT_FOUND" if value is None else f"VALUE {value}"), False
        if cmd == "DEL":
            if len(parts) != 2:
                return "ERR usage: DEL <key>", False
            return ("DELETED" if store.delete(parts[1]) else "NOT_FOUND"), False
        if cmd == "KEYS":
            return "KEYS " + " ".join(store.keys()), False
        if cmd == "COUNT":
            return f"COUNT {len(store)}", False
        if cmd == "COMPACT":
            store.compact()
            return "OK", False
        if cmd == "QUIT":
            return "BYE", True
    except KVStoreError as exc:
        return f"ERR {exc}", False

    return f"ERR unknown command {cmd}", False


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            raw = self.rfile.readline(MAX_LINE + 1)
            if not raw:
                return  # client disconnected
            if len(raw) > MAX_LINE:
                self._send("ERR line too long")
                return
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                self._send("ERR invalid utf-8")
                continue
            reply, close = handle_command(self.server.store, line)
            self._send(reply)
            if close:
                return

    def _send(self, text: str) -> None:
        self.wfile.write((text + "\n").encode("utf-8"))
        self.wfile.flush()


class KVServer(socketserver.ThreadingTCPServer):
    """One thread per client connection; all threads share one KVStore."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, store: KVStore):
        super().__init__(address, _Handler)
        self.store = store
