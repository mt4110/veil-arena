"""Namespace creation must precede Pod admission without extending its deadline."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import k3s_guest as guest


class FixtureDependencies(unittest.TestCase):
    def setUp(self):
        self.manifest = {"apiVersion": "v1", "kind": "List", "items": [
            {"kind": "Namespace", "metadata": {"name": "arena-f1"}},
            {"kind": "Pod", "metadata": {"name": "server", "namespace": "arena-f1"}}]}
        self.account = {"kind": "ServiceAccount", "metadata": {
            "name": "default", "namespace": "arena-f1", "uid": "fixture-account"}}
        self.clock = 0.0
        self.calls = []

    def sleep(self, seconds):
        self.clock += seconds

    def reply(self, code=0, body=b""):
        return SimpleNamespace(returncode=code, stdout=body)

    def run_fixture(self, responses, end=1.0):
        responses = iter(responses)
        def kube(args, deadline, data=None):
            self.calls.append((args, deadline, json.loads(data) if data else None))
            return next(responses)
        guest.apply_fixture(kube, self.manifest, end, now=lambda: self.clock, sleep=self.sleep)

    def test_delayed_account_precedes_single_original_fixture_apply(self):
        self.run_fixture([self.reply(), self.reply(),
                          self.reply(body=json.dumps(self.account).encode()), self.reply()])
        self.assertEqual(self.calls[0][2]["items"], [self.manifest["items"][0]])
        self.assertEqual(self.calls[-1][2], self.manifest)
        self.assertEqual(len([c for c in self.calls if c[2] == self.manifest]), 1)
        self.assertTrue(all(c[1] == 1.0 for c in self.calls))
        self.assertEqual(self.clock, 0.25)

    def test_missing_account_expires_within_original_deadline(self):
        with self.assertRaisesRegex(guest.Failure, "Pod deadline"):
            self.run_fixture([self.reply(), self.reply(), self.reply()], end=0.5)
        self.assertEqual(self.clock, 0.5)
        self.assertFalse(any(c[2] == self.manifest for c in self.calls))

    def test_namespace_failure_does_not_start_pods(self):
        with self.assertRaisesRegex(guest.Failure, "namespace apply failed"):
            self.run_fixture([self.reply(1)])
        self.assertEqual(len(self.calls), 1)

    def test_lookup_failure_is_not_treated_as_account_not_created(self):
        with self.assertRaisesRegex(guest.Failure, "lookup failed"):
            self.run_fixture([self.reply(), self.reply(1)])
        self.assertEqual(self.clock, 0)
        self.assertFalse(any(c[2] == self.manifest for c in self.calls))

    def test_wrong_or_deleting_account_does_not_start_pods(self):
        for change in [{"namespace": "wrong"}, {"deletionTimestamp": "now"}]:
            with self.subTest(change=change):
                self.calls = []
                account = dict(self.account, metadata=dict(self.account["metadata"], **change))
                with self.assertRaisesRegex(guest.Failure, "identity mismatch"):
                    self.run_fixture([self.reply(), self.reply(body=json.dumps(account).encode())])
                self.assertFalse(any(c[2] == self.manifest for c in self.calls))

    def test_fixture_failure_is_preserved_without_retry(self):
        with self.assertRaisesRegex(guest.Failure, "manifest apply failed"):
            self.run_fixture([self.reply(), self.reply(body=json.dumps(self.account).encode()), self.reply(1)])
        self.assertEqual(len([c for c in self.calls if c[2] == self.manifest]), 1)
