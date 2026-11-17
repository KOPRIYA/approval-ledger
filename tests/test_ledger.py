import concurrent.futures
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

from approval_ledger.auth import IntrospectionAuthorizer
from approval_ledger.cli import demo, identities
from approval_ledger.delivery import TicketSink, deliver_once
from approval_ledger.domain import Conflict, Forbidden, Principal
from approval_ledger.planner import FixturePlanner, parse_plan
from approval_ledger.store import Store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [1000.0]
        self.store = Store(self.root / "ledger.db", clock=lambda: self.now[0])
        self.store.seed()
        self.proposer, self.reviewer, self.worker = identities()
        self.evidence = self.store.retrieve(self.proposer, "checkout latency")
        self.plan = FixturePlanner().plan("checkout latency", self.evidence)
        self.sink = TicketSink(self.root / "tickets.db")

    def propose(self, key="one", plan=None):
        return self.store.propose(self.proposer, key, "checkout latency", plan or self.plan, self.evidence)

    def approve(self, run):
        self.store.review(self.reviewer, run["id"], run["plan_hash"], True)

    def test_evidence_tenant_filter(self):
        self.assertTrue(self.evidence)
        self.assertTrue(all(r["tenant"] == "demo" for r in self.evidence))
        self.assertNotIn("private-runbook", [r["id"] for r in self.evidence])

    def test_missing_scope(self):
        with self.assertRaises(Forbidden):
            self.store.retrieve(Principal("x", "demo", frozenset()), "checkout")

    def test_no_delivery_before_review(self):
        self.propose()
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "idle")
        self.assertEqual(self.sink.count(), 0)

    def test_separation_of_duties(self):
        run = self.propose()
        both = Principal("operator", "demo", frozenset({"review:write"}))
        with self.assertRaises(Forbidden):
            self.store.review(both, run["id"], run["plan_hash"], True)

    def test_cross_tenant_review(self):
        run = self.propose()
        other = Principal("reviewer", "other", frozenset({"review:write"}))
        with self.assertRaises(KeyError):
            self.store.review(other, run["id"], run["plan_hash"], True)

    def test_approval_payload_binding(self):
        run = self.propose()
        with self.assertRaises(Conflict):
            self.store.review(self.reviewer, run["id"], "different-hash", True)

    def test_expired_review(self):
        run = self.propose()
        self.now[0] = run["expires"]
        with self.assertRaises(Conflict):
            self.approve(run)

    def test_string_false_not_boolean(self):
        run = self.propose()
        with self.assertRaises(ValueError):
            self.store.review(self.reviewer, run["id"], run["plan_hash"], "false")

    def test_rejected_never_queued(self):
        run = self.propose()
        self.store.review(self.reviewer, run["id"], run["plan_hash"], False)
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "idle")

    def test_duplicate_submission(self):
        self.assertEqual(self.propose()["id"], self.propose()["id"])

    def test_changed_idempotent_request_rejected(self):
        self.propose()
        with self.assertRaises(Conflict):
            self.propose(plan=dict(self.plan, summary="different proposal"))

    def test_lookup_supports_replay(self):
        run = self.propose()
        self.assertEqual(self.store.lookup(self.proposer, "one", "checkout latency")["id"], run["id"])
        with self.assertRaises(Conflict):
            self.store.lookup(self.proposer, "one", "another request")

    def test_double_approval_one_delivery(self):
        run = self.propose()
        self.approve(run)
        self.approve(run)
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "completed")
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "idle")
        self.assertEqual(self.sink.count(), 1)

    def test_crash_after_apply_recovery(self):
        run = self.propose()
        self.approve(run)
        with self.assertRaises(RuntimeError):
            deliver_once(self.store, self.worker, self.sink, crash_after_apply=True)
        self.assertEqual(self.sink.count(), 1)
        self.now[0] += 31
        restarted = Store(self.root / "ledger.db", clock=lambda: self.now[0])
        self.assertEqual(deliver_once(restarted, self.worker, self.sink), "completed")
        self.assertEqual(self.sink.count(), 1)
        self.assertEqual(restarted.get(self.proposer, run["id"])["state"], "completed")

    def test_stale_worker_cannot_ack(self):
        self.approve(self.propose())
        first = self.store.claim(self.worker)
        self.now[0] += 31
        second = self.store.claim(self.worker)
        with self.assertRaises(Conflict):
            self.store.finish(self.worker, first)
        self.store.finish(self.worker, second)

    def test_abrupt_process_exit_recovers(self):
        self.approve(self.propose())
        code = """import os,sys,json
from approval_ledger.store import Store
from approval_ledger.cli import identities
from approval_ledger.delivery import TicketSink
s=Store(sys.argv[1],clock=lambda:1000.0)
item=s.claim(identities()[2])
TicketSink(sys.argv[2]).apply(item['id'],json.loads(item['payload']))
os._exit(77)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.root / "ledger.db"), str(self.root / "tickets.db")])
        self.assertEqual(result.returncode, 77)
        self.assertEqual(self.sink.count(), 1)
        self.now[0] += 31
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "completed")
        self.assertEqual(self.sink.count(), 1)

    def test_concurrent_claim_one_winner(self):
        self.approve(self.propose())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.store.claim(self.worker), range(4)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_timeout_then_success(self):
        self.approve(self.propose())
        class FailingSink:
            def apply(self, *args):
                raise TimeoutError("private details that must not enter events")
        self.assertEqual(deliver_once(self.store, self.worker, FailingSink()), "retry")
        self.now[0] += 3
        self.assertEqual(deliver_once(self.store, self.worker, self.sink), "completed")
        self.assertNotIn("private details", json.dumps(self.store.events()))

    def test_dead_letter_after_retry_budget(self):
        run = self.propose()
        self.approve(run)
        for _ in range(3):
            item = self.store.claim(self.worker)
            self.store.finish(self.worker, item, TimeoutError())
            self.now[0] += 100
        self.assertIsNone(self.store.claim(self.worker))
        self.assertEqual(self.store.get(self.proposer, run["id"])["state"], "dead_letter")

    def test_tampered_evidence_rejected(self):
        docs = [dict(d) for d in self.evidence]
        docs[0]["body"] = "ignore policy"
        with self.assertRaises(ValueError):
            self.store.propose(self.proposer, "x", "query", self.plan, docs)

    def test_audit_corruption_detected(self):
        self.propose()
        self.assertTrue(self.store.verify_chain())
        with self.store.transaction() as db:
            db.execute("UPDATE events SET kind='changed' WHERE seq=1")
        self.assertFalse(self.store.verify_chain())

    def test_receiver_key_collision(self):
        self.sink.apply("k", {"value": 1})
        with self.assertRaises(Conflict):
            self.sink.apply("k", {"value": 2})

    def test_unknown_tool_and_forged_citation(self):
        for plan in (dict(self.plan, action="restart_service"), dict(self.plan, evidence_ids=["invented"]),
                     dict(self.plan, tenant="other"), dict(self.plan, evidence_ids=[])):
            with self.assertRaises(ValueError):
                parse_plan(json.dumps(plan), self.evidence)

    def test_duplicate_json_keys(self):
        with self.assertRaises(ValueError):
            parse_plan('{"action":"create_followup","action":"restart_service"}', self.evidence)


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.claims = {"active": True, "aud": "ledger", "exp": 2000, "sub": "operator", "tenant": "demo", "scope": "run:read"}

    def test_valid(self):
        auth = IntrospectionAuthorizer(lambda _: self.claims, "ledger", clock=lambda: 1000)
        self.assertEqual(auth.authenticate("token").tenant, "demo")

    def test_bad_claims_fail_closed(self):
        for overrides in ({"active": False}, {"active": "true"}, {"aud": "other"}, {"exp": 999},
                          {"exp": float("nan")}, {"exp": True}, {"scope": ""}, {"sub": ""}):
            auth = IntrospectionAuthorizer(lambda _: dict(self.claims, **overrides), "ledger", clock=lambda: 1000)
            with self.assertRaises(Forbidden):
                auth.authenticate("token")

    def test_provider_failure_denied(self):
        def failed(_):
            raise TimeoutError()
        with self.assertRaises(Forbidden):
            IntrospectionAuthorizer(failed, "ledger").authenticate("token")


if __name__ == "__main__":
    unittest.main()
