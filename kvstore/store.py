"""A small persistent key-value store.

Design (Bitcask-style):
  * Every write is appended to a single log file on disk.
  * An in-memory dict (the "index") maps each key to where its latest value
    lives in the file, so a read is one dictionary lookup plus one disk read.
  * Deleting a key appends a "tombstone" record instead of editing the file.
  * On startup the log is replayed to rebuild the index. A half-written or
    corrupted record at the end of the file (e.g. after a crash) is detected
    with a checksum and discarded.
  * compact() rewrites the log with only live data, reclaiming disk space.

Record layout on disk (big-endian):

    | crc32 (4) | flags (1) | key_len (4) | value_len (4) | key | value |

The checksum covers everything after the crc field.
"""

import os
import struct
import threading
import zlib

HEADER = struct.Struct(">IBII")  # crc32, flags, key_len, value_len
_BODY_HEADER = struct.Struct(">BII")  # the part of the header the crc covers
FLAG_TOMBSTONE = 1


class KVStoreError(Exception):
    """Raised for invalid use of the store (bad keys, closed store, ...)."""


def _encode_record(flags: int, key: bytes, value: bytes) -> bytes:
    body_header = _BODY_HEADER.pack(flags, len(key), len(value))
    crc = zlib.crc32(body_header + key + value)
    return struct.pack(">I", crc) + body_header + key + value


class KVStore:
    """Persistent, thread-safe key-value store for string keys and values."""

    def __init__(self, path: str, fsync: bool = False):
        """Open (or create) a store backed by the file at `path`.

        fsync=True forces every write to physical disk before returning:
        safer against power loss, but much slower.
        """
        self.path = path
        self.fsync = fsync
        self._lock = threading.RLock()
        self._index = {}  # key (str) -> (value_offset, value_length)
        self._closed = False

        valid_end = self._replay() if os.path.exists(path) else 0
        if os.path.exists(path) and os.path.getsize(path) > valid_end:
            # Drop a partial/corrupt tail so new writes start on a clean record.
            os.truncate(path, valid_end)

        self._writer = open(path, "ab")
        self._reader = open(path, "rb")
        self._size = valid_end

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def put(self, key: str, value: str) -> None:
        """Store `value` under `key`, replacing any existing value."""
        k = self._check_key(key)
        if not isinstance(value, str):
            raise KVStoreError("value must be a string")
        v = value.encode("utf-8")
        with self._lock:
            self._check_open()
            record = _encode_record(0, k, v)
            value_offset = self._size + HEADER.size + len(k)
            self._append(record)
            self._index[key] = (value_offset, len(v))

    def get(self, key: str):
        """Return the value for `key`, or None if it does not exist."""
        self._check_key(key)
        with self._lock:
            self._check_open()
            entry = self._index.get(key)
            if entry is None:
                return None
            offset, length = entry
            self._reader.seek(offset)
            return self._reader.read(length).decode("utf-8")

    def delete(self, key: str) -> bool:
        """Remove `key`. Returns True if it existed, False otherwise."""
        k = self._check_key(key)
        with self._lock:
            self._check_open()
            if key not in self._index:
                return False
            self._append(_encode_record(FLAG_TOMBSTONE, k, b""))
            del self._index[key]
            return True

    def keys(self):
        """Return a sorted list of all live keys."""
        with self._lock:
            self._check_open()
            return sorted(self._index)

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._index

    def file_size(self) -> int:
        """Current size of the log file in bytes."""
        with self._lock:
            return self._size

    def compact(self) -> None:
        """Rewrite the log so it holds only the latest value of each live key.

        Overwrites and deletes leave dead records behind; this reclaims them.
        The new file is fully written and synced before it atomically replaces
        the old one, so a crash mid-compaction cannot lose data.
        """
        with self._lock:
            self._check_open()
            tmp_path = self.path + ".compact"
            new_index = {}
            offset = 0
            with open(tmp_path, "wb") as out:
                for key in sorted(self._index):
                    old_offset, length = self._index[key]
                    self._reader.seek(old_offset)
                    value = self._reader.read(length)
                    k = key.encode("utf-8")
                    out.write(_encode_record(0, k, value))
                    new_index[key] = (offset + HEADER.size + len(k), length)
                    offset += HEADER.size + len(k) + length
                out.flush()
                os.fsync(out.fileno())

            self._writer.close()
            self._reader.close()
            os.replace(tmp_path, self.path)  # atomic swap
            self._writer = open(self.path, "ab")
            self._reader = open(self.path, "rb")
            self._index = new_index
            self._size = offset

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._writer.close()
            self._reader.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _append(self, record: bytes) -> None:
        self._writer.write(record)
        self._writer.flush()
        if self.fsync:
            os.fsync(self._writer.fileno())
        self._size += len(record)

    def _replay(self) -> int:
        """Rebuild the index from the log. Returns the offset of the last
        fully valid record's end (anything after it is garbage)."""
        offset = 0
        with open(self.path, "rb") as f:
            while True:
                header = f.read(HEADER.size)
                if len(header) < HEADER.size:
                    break  # clean end of file, or a truncated header
                crc, flags, key_len, value_len = HEADER.unpack(header)
                payload = f.read(key_len + value_len)
                if len(payload) < key_len + value_len:
                    break  # truncated record (crash mid-write)
                expected = zlib.crc32(_BODY_HEADER.pack(flags, key_len, value_len) + payload)
                if crc != expected:
                    break  # corrupted record: stop trusting the file here
                key = payload[:key_len].decode("utf-8")
                if flags & FLAG_TOMBSTONE:
                    self._index.pop(key, None)
                else:
                    self._index[key] = (offset + HEADER.size + key_len, value_len)
                offset += HEADER.size + key_len + value_len
        return offset

    @staticmethod
    def _check_key(key) -> bytes:
        if not isinstance(key, str) or not key:
            raise KVStoreError("key must be a non-empty string")
        return key.encode("utf-8")

    def _check_open(self) -> None:
        if self._closed:
            raise KVStoreError("store is closed")
