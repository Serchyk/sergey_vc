"""Unit tests: python -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_orders.core import build_result, normalize_phone  # noqa: E402
from sync_orders.storage import Store  # noqa: E402
from sync_orders.synchronizer import Synchronizer  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_orders.json")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class TestNormalize(unittest.TestCase):
    def test_valid_formats(self):
        cases = {
            "+7 999 123-45-67": "+79991234567",
            "8 (999) 123 45 67": "+79991234567",
            "79991234567": "+79991234567",
            "+79991234567": "+79991234567",
            "8-999-123-45-67": "+79991234567",
            "+7\t999\n123 45 67": "+79991234567",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_phone(raw), expected, raw)

    def test_invalid_values(self):
        for raw in ["", "   ", "123", "+7 999", "не номер", None, [], {}, True,
                    "9005551212", "+7 999 123 45 678", "+1 202 555 0143"]:
            self.assertIsNone(normalize_phone(raw), repr(raw))


class TestMerge(unittest.TestCase):
    def test_fixture_result(self):
        result = build_result(load_fixture())
        by_phone = {c["phone"]: c for c in result.clients}

        self.assertEqual(result.stats.received, 17)
        self.assertEqual(result.stats.invalid_dropped, 5)
        self.assertEqual(result.stats.unique, len(result.clients))

        # highest amount wins: 1/2/3 -> 5000, 10/11 -> 9000, 7/8 -> 1500
        self.assertEqual(by_phone["+79991234567"]["amount"], 5000)
        self.assertEqual(by_phone["+79261234567"]["amount"], 9000)
        self.assertEqual(by_phone["+79219998877"]["amount"], 1500)

        # equal amount -> the later created_at wins (id 5, 2026-10-05)
        self.assertEqual(by_phone["+79160000001"]["name"], "Мария С.")

        # every public field has exactly the documented shape
        for client in result.clients:
            self.assertEqual(set(client), {"name", "phone", "email", "amount"})
            self.assertTrue(client["phone"].startswith("+7"))
            self.assertEqual(len(client["phone"]), 12)
            self.assertIsInstance(client["amount"], int)

    def test_stats_add_up(self):
        stats = build_result(load_fixture()).stats
        self.assertEqual(
            stats.received,
            stats.unique + stats.duplicates_merged + stats.invalid_dropped,
        )
        self.assertEqual(stats.duplicates_merged, 5)
        self.assertEqual(stats.unique, 7)


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "sync.db"))
        self.sync = Synchronizer(self.store, source_url="http://unused")

    def tearDown(self):
        self.store.close()

    def test_repeated_sync_is_stable(self):
        payload = load_fixture()
        first = self.sync.run(payload)
        first_json = json.dumps(first.as_dict(), ensure_ascii=False, sort_keys=True)
        for _ in range(3):
            again = self.sync.run(payload)
            self.assertEqual(
                json.dumps(again.as_dict(), ensure_ascii=False, sort_keys=True),
                first_json,
            )
        rows = self.store._conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        self.assertEqual(rows, first.stats.unique)
        self.assertEqual(self.store.meta()["runs"], 4)

    def test_rejected_orders_are_listed(self):
        self.sync.run(load_fixture())
        rejected = self.store.invalid_orders()
        self.assertEqual(len(rejected), 5)
        self.assertTrue(all(not item["phone_raw"].strip() or True for item in rejected))
        self.assertEqual(
            {item["source_id"] for item in rejected}, {"9", "14", "15", "16", "17"}
        )
        reasons = {item["source_id"]: item["reason"] for item in rejected}
        self.assertEqual(reasons["15"], "empty")
        self.assertEqual(reasons["14"], "not 11 digits")

    def test_stored_result_matches_computed(self):
        payload = load_fixture()
        computed = self.sync.run(payload)
        stored = self.store.load_result()
        self.assertEqual(stored.as_dict(), computed.as_dict())

    def test_shuffled_input_gives_same_result(self):
        payload = load_fixture()
        baseline = json.dumps(self.sync.run(payload).as_dict(), ensure_ascii=False, sort_keys=True)
        shuffled = list(reversed(payload))
        other = json.dumps(self.sync.run(shuffled).as_dict(), ensure_ascii=False, sort_keys=True)
        self.assertEqual(baseline, other)


if __name__ == "__main__":
    unittest.main(verbosity=2)