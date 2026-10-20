# kvstore

A small, persistent key-value store written in pure Python (standard library only), with a TCP server and client.

It uses the same core design as Bitcask, the storage engine behind Riak: an **append-only log on disk** plus an **in-memory hash index**. I wanted to understand how databases actually store data and survive crashes, instead of just using one. I'm working through the code piece by piece, and I plan to keep extending it.

[![tests](https://github.com/ali6oc/kvstore/actions/workflows/tests.yml/badge.svg)](https://github.com/ali6oc/kvstore/actions/workflows/tests.yml)

## Features

- **Persistent:** data survives restarts; the log is replayed on startup to rebuild the index
- **Crash-safe recovery:** every record has a CRC32 checksum, so a half-written or corrupted tail is detected and discarded
- **Fast reads:** one dictionary lookup plus one disk read per `GET`
- **Fast writes:** sequential appends only, no in-place edits
- **Compaction:** rewrites the log with only live data, using an atomic file swap so a crash mid-compaction cannot lose data
- **Thread-safe:** safe to share between threads, and the server handles many clients at once
- **Network server:** simple line-based text protocol, usable with `nc`/`telnet` or the included Python client
- **Tested:** 23 unit and end-to-end tests, run automatically in CI

## Quick start

Requires Python 3.9+. No dependencies.

```bash
# start the server
python -m kvstore --data kv.data --port 6399
```

In another terminal:

```bash
nc 127.0.0.1 6399
SET city London
GET city
DEL city
```

Or from Python:

```python
from kvstore import KVClient

with KVClient(port=6399) as db:
    db.set("name", "Ali")
    print(db.get("name"))   # Ali
    db.delete("name")
```

Or use the storage engine directly, with no server:

```python
from kvstore import KVStore

with KVStore("my.data") as store:
    store.put("a", "1")
    print(store.get("a"))   # 1
    store.compact()
```

## Protocol

One command per line, one reply per line.

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `SET <key> <value>` | `OK` (value may contain spaces) |
| `GET <key>` | `VALUE <value>` or `NOT_FOUND` |
| `DEL <key>` | `DELETED` or `NOT_FOUND` |
| `KEYS` | `KEYS <k1> <k2> ...` |
| `COUNT` | `COUNT <n>` |
| `COMPACT` | `OK` |
| `QUIT` | `BYE` |

Errors come back as `ERR <message>`.

## How it works

```
           put("a","1")                get("a")
                |                         |
                v                         v
   +------------------------+    +-----------------+
   | in-memory index (dict) |--->| seek + read     |
   | key -> (offset, len)   |    | from the log    |
   +------------------------+    +-----------------+
                |
                v  append
   +-------------------------------------------------+
   | log file:  [rec][rec][rec][tombstone][rec] ...  |
   +-------------------------------------------------+
```

Each record on disk:

```
| crc32 (4) | flags (1) | key_len (4) | value_len (4) | key | value |
```

- **Write:** append a record, then point the index at its value.
- **Delete:** append a *tombstone* record and drop the key from the index.
- **Startup:** read the log front to back, rebuilding the index. Stop at the first record whose checksum fails or that is cut short, and truncate the file there.
- **Compaction:** write every live key's latest value to a new file, `fsync` it, then atomically replace the old file.

## Design trade-offs

- **All keys live in memory.** This makes reads fast, but the number of keys is limited by RAM (values stay on disk).
- **Single node, no replication.** A real distributed store would need replication and consensus on top of this.
- **Single global lock.** Simple and correct, but writes are serialised. Finer-grained locking or a write-ahead batching scheme would raise throughput.
- **Durability is configurable.** By default each write is flushed to the OS. `--fsync` forces every write to disk, which is safer against power loss but much slower.
- **Text protocol limits:** keys cannot contain spaces, values cannot contain newlines.

## Running the tests

```bash
python -m unittest discover -s tests -t . -v
```

## Possible next steps

- TTL / key expiry
- Background compaction when dead data passes a threshold
- Binary-safe keys and values with a length-prefixed protocol
- Hint files to speed up startup on very large logs
- Benchmarks (reads/writes per second, with and without `fsync`)
