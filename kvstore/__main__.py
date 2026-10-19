"""Run the server with:  python -m kvstore --data kv.data --port 6399"""

import argparse

from .server import KVServer
from .store import KVStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the kvstore TCP server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6399)
    parser.add_argument("--data", default="kv.data", help="path of the log file")
    parser.add_argument("--fsync", action="store_true",
                        help="fsync after every write (safer, slower)")
    args = parser.parse_args()

    store = KVStore(args.data, fsync=args.fsync)
    server = KVServer((args.host, args.port), store)
    print(f"kvstore listening on {args.host}:{args.port} "
          f"(data: {args.data}, {len(store)} keys loaded)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
