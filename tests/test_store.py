import os
import tempfile
import threading
import unittest

from kvstore import KVStore, KVStoreError


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "kv.data")

    def tearDown(self):
        self.dir.cleanup()


class TestBasics(StoreTestCase):
    def test_put_and_get(self):
        with KVStore(self.path) as s:
            s.put("name", "ali")
            self.assertEqual(s.get("name"), "ali")

    def test_missing_key_returns_none(self):
        with KVStore(self.path) as s:
            self.assertIsNone(s.get("nope"))

    def test_overwrite_returns_latest(self):
        with KVStore(self.path) as s:
            s.put("k", "one")
            s.put("k", "two")
            self.assertEqual(s.get("k"), "two")
            self.assertEqual(len(s), 1)

    def test_delete(self):
        with KVStore(self.path) as s:
            s.put("k", "v")
            self.assertTrue(s.delete("k"))
            self.assertIsNone(s.get("k"))
            self.assertFalse(s.delete("k"))

    def test_unicode_and_empty_value(self):
        with KVStore(self.path) as s:
            s.put("héllo", "wörld ✓ 日本語")
            s.put("empty", "")
            self.assertEqual(s.get("héllo"), "wörld ✓ 日本語")
            self.assertEqual(s.get("empty"), "")

    def test_keys_sorted(self):
        with KVStore(self.path) as s:
            for k in ("b", "c", "a"):
                s.put(k, "x")
            self.assertEqual(s.keys(), ["a", "b", "c"])

    def test_invalid_input(self):
        with KVStore(self.path) as s:
            with self.assertRaises(KVStoreError):
                s.put("", "v")
            with self.assertRaises(KVStoreError):
                s.put("k", 123)
            with self.assertRaises(KVStoreError):
                s.get(None)

    def test_use_after_close(self):
        s = KVStore(self.path)
        s.close()
        with self.assertRaises(KVStoreError):
            s.put("k", "v")


class TestPersistence(StoreTestCase):
    def test_data_survives_reopen(self):
        with KVStore(self.path) as s:
            s.put("a", "1")
            s.put("b", "2")
            s.put("a", "3")
            s.delete("b")
        with KVStore(self.path) as s:
            self.assertEqual(s.get("a"), "3")
            self.assertIsNone(s.get("b"))
            self.assertEqual(len(s), 1)

    def test_writes_after_reopen_are_readable(self):
        with KVStore(self.path) as s:
            s.put("a", "1")
        with KVStore(self.path) as s:
            s.put("b", "2")
            self.assertEqual(s.get("a"), "1")
            self.assertEqual(s.get("b"), "2")
        with KVStore(self.path) as s:
            self.assertEqual(s.keys(), ["a", "b"])


class TestCrashRecovery(StoreTestCase):
    def _write_two_records(self):
        with KVStore(self.path) as s:
            s.put("first", "aaa")
            s.put("second", "bbb")
            return s.file_size()

    def test_truncated_tail_is_discarded(self):
        size = self._write_two_records()
        with open(self.path, "r+b") as f:
            f.truncate(size - 2)  # simulate a crash mid-write of record 2
        with KVStore(self.path) as s:
            self.assertEqual(s.get("first"), "aaa")
            self.assertIsNone(s.get("second"))
            s.put("third", "ccc")  # store must still be usable
        with KVStore(self.path) as s:
            self.assertEqual(s.keys(), ["first", "third"])

    def test_corrupted_record_is_discarded(self):
        size = self._write_two_records()
        with open(self.path, "r+b") as f:
            f.seek(size - 1)
            f.write(b"X")  # flip the last byte of record 2
        with KVStore(self.path) as s:
            self.assertEqual(s.get("first"), "aaa")
            self.assertIsNone(s.get("second"))


class TestCompaction(StoreTestCase):
    def test_compaction_shrinks_file_and_keeps_data(self):
        with KVStore(self.path) as s:
            for i in range(200):
                s.put("counter", str(i))  # 199 dead records
            s.put("keep", "me")
            s.put("gone", "x")
            s.delete("gone")
            before = s.file_size()
            s.compact()
            after = s.file_size()
            self.assertLess(after, before)
            self.assertEqual(s.get("counter"), "199")
            self.assertEqual(s.get("keep"), "me")
            self.assertIsNone(s.get("gone"))
            s.put("after", "compact")  # writes still work afterwards
        with KVStore(self.path) as s:
            self.assertEqual(s.keys(), ["after", "counter", "keep"])
            self.assertEqual(s.get("counter"), "199")


class TestConcurrency(StoreTestCase):
    def test_parallel_writers(self):
        with KVStore(self.path) as s:
            def worker(n):
                for i in range(100):
                    s.put(f"t{n}-{i}", f"value-{n}-{i}")

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(s), 800)
            for n in range(8):
                for i in range(100):
                    self.assertEqual(s.get(f"t{n}-{i}"), f"value-{n}-{i}")

        with KVStore(self.path) as s:  # and it all survives a restart
            self.assertEqual(len(s), 800)


if __name__ == "__main__":
    unittest.main()
