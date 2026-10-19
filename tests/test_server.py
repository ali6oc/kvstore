import os
import tempfile
import threading
import unittest

from kvstore import KVClient, KVServer, KVStore, handle_command


class TestProtocol(unittest.TestCase):
    """Exercise the command parser directly, with no sockets involved."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = KVStore(os.path.join(self.dir.name, "kv.data"))

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def run_cmd(self, line):
        return handle_command(self.store, line)

    def test_ping(self):
        self.assertEqual(self.run_cmd("PING"), ("PONG", False))

    def test_set_get_del(self):
        self.assertEqual(self.run_cmd("SET greeting hello there world")[0], "OK")
        self.assertEqual(self.run_cmd("GET greeting")[0], "VALUE hello there world")
        self.assertEqual(self.run_cmd("DEL greeting")[0], "DELETED")
        self.assertEqual(self.run_cmd("GET greeting")[0], "NOT_FOUND")
        self.assertEqual(self.run_cmd("DEL greeting")[0], "NOT_FOUND")

    def test_commands_are_case_insensitive(self):
        self.run_cmd("set a 1")
        self.assertEqual(self.run_cmd("get a")[0], "VALUE 1")

    def test_keys_and_count(self):
        self.run_cmd("SET b 2")
        self.run_cmd("SET a 1")
        self.assertEqual(self.run_cmd("KEYS")[0], "KEYS a b")
        self.assertEqual(self.run_cmd("COUNT")[0], "COUNT 2")

    def test_errors(self):
        self.assertTrue(self.run_cmd("")[0].startswith("ERR"))
        self.assertTrue(self.run_cmd("SET onlykey")[0].startswith("ERR"))
        self.assertTrue(self.run_cmd("GET")[0].startswith("ERR"))
        self.assertTrue(self.run_cmd("NOPE x")[0].startswith("ERR"))

    def test_quit_closes(self):
        self.assertEqual(self.run_cmd("QUIT"), ("BYE", True))


class TestServerEndToEnd(unittest.TestCase):
    """Start a real server on a free port and talk to it over TCP."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = KVStore(os.path.join(self.dir.name, "kv.data"))
        self.server = KVServer(("127.0.0.1", 0), self.store)  # port 0 = any free port
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.dir.cleanup()

    def test_client_round_trip(self):
        with KVClient(port=self.port) as c:
            self.assertTrue(c.ping())
            c.set("city", "London")
            self.assertEqual(c.get("city"), "London")
            self.assertEqual(c.count(), 1)
            self.assertEqual(c.keys(), ["city"])
            self.assertTrue(c.delete("city"))
            self.assertIsNone(c.get("city"))
            c.compact()

    def test_two_clients_share_data(self):
        with KVClient(port=self.port) as a, KVClient(port=self.port) as b:
            a.set("shared", "yes")
            self.assertEqual(b.get("shared"), "yes")

    def test_many_concurrent_clients(self):
        errors = []

        def worker(n):
            try:
                with KVClient(port=self.port) as c:
                    for i in range(25):
                        c.set(f"c{n}-{i}", f"v{i}")
                        if c.get(f"c{n}-{i}") != f"v{i}":
                            errors.append((n, i))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store), 250)


if __name__ == "__main__":
    unittest.main()
