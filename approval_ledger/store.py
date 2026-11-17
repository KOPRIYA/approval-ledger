import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .domain import Conflict, Forbidden, canonical, digest, validate_plan

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id TEXT, tenant TEXT, service TEXT, body TEXT, PRIMARY KEY(tenant,id));
CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(id UNINDEXED, tenant UNINDEXED, body);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, owner TEXT NOT NULL, request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, plan TEXT NOT NULL, plan_hash TEXT NOT NULL,
 state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
 UNIQUE(tenant,request_key));
CREATE TABLE IF NOT EXISTS approvals (
 run_id TEXT PRIMARY KEY, reviewer TEXT NOT NULL, plan_hash TEXT NOT NULL,
 approved INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 available REAL NOT NULL, lease_until REAL, lease_token TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL,
 data TEXT NOT NULL, created REAL NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
"""


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, run_id, kind, data):
        previous = db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        created = self.clock()
        item = {"run_id": run_id, "kind": kind, "data": data, "created": created, "previous_hash": previous_hash}
        db.execute("INSERT INTO events(run_id,kind,data,created,previous_hash,event_hash) VALUES(?,?,?,?,?,?)",
                   (run_id, kind, canonical(data), created, previous_hash, digest(item)))

    def seed(self):
        docs = [
            ("checkout-latency", "demo", "checkout", "Checkout latency runbook: inspect dependency timeouts and queue depth. Open a follow-up ticket with evidence; do not restart a service automatically."),
            ("catalog-stale", "demo", "catalog", "Catalog stale data runbook: inspect ingestion lag and recent schema changes. Create a follow-up ticket and ask an operator to validate the lag."),
            ("private-runbook", "other", "checkout", "Checkout private tenant incident notes. Not visible to demo users."),
        ]
        with self.transaction() as db:
            for doc in docs:
                db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?)", doc)
                if not db.execute("SELECT 1 FROM document_search WHERE id=? AND tenant=?", (doc[0], doc[1])).fetchone():
                    db.execute("INSERT INTO document_search(id,tenant,body) VALUES(?,?,?)", (doc[0], doc[1], doc[3]))

    def retrieve(self, principal, query, limit=3):
        import re
        principal.require("evidence:read")
        # Build a literal token query, not a user-provided FTS expression.
        words = re.findall(r"[a-zA-Z0-9]+", query)[:24]
        if not words:
            return []
        match = " OR ".join('"' + word + '"' for word in words)
        with self.connect() as db:
            rows = db.execute("""SELECT d.* FROM document_search s JOIN documents d
                ON d.id=s.id AND d.tenant=s.tenant
                WHERE document_search MATCH ? AND d.tenant=?
                ORDER BY bm25(document_search),d.id LIMIT ?""", (match, principal.tenant, min(max(limit, 1), 10))).fetchall()
        return [dict(row) for row in rows]

    def propose(self, principal, request_key, request, plan, evidence, ttl_seconds=900):
        principal.require("proposal:write")
        if not request_key or len(request_key) > 128 or not 0 < ttl_seconds <= 86400:
            raise ValueError("invalid request key or approval lifetime")
        validate_plan(plan, evidence)
        if any(doc["tenant"] != principal.tenant for doc in evidence):
            raise Forbidden("cross-tenant evidence")
        request_hash = digest({"request": request, "owner": principal.subject})
        with self.transaction() as db:
            for doc in evidence:
                stored = db.execute("SELECT * FROM documents WHERE tenant=? AND id=?", (principal.tenant, doc["id"])).fetchone()
                if not stored or dict(stored) != doc:
                    raise ValueError("evidence must match stored content")
            existing = db.execute("SELECT * FROM runs WHERE tenant=? AND request_key=?", (principal.tenant, request_key)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash or json.loads(existing["plan"]) != plan:
                    raise Conflict("idempotency key reused with different content")
                return dict(existing)
            run_id = uuid.uuid4().hex
            now = self.clock()
            plan_hash = digest({"tenant": principal.tenant, "plan": plan})
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, principal.tenant, principal.subject,
                request_key, request_hash, canonical(plan), plan_hash, "awaiting_review", now, now + ttl_seconds))
            self._event(db, run_id, "proposal_created", {"plan_hash": plan_hash, "evidence_ids": plan["evidence_ids"]})
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def lookup(self, principal, request_key, request):
        principal.require("proposal:write")
        with self.connect() as db:
            existing = db.execute("SELECT * FROM runs WHERE tenant=? AND request_key=?", (principal.tenant, request_key)).fetchone()
        if not existing:
            return None
        if existing["request_hash"] != digest({"request": request, "owner": principal.subject}):
            raise Conflict("idempotency key reused with different request or principal")
        return dict(existing)

    def get(self, principal, run_id):
        principal.require("run:read")
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=? AND tenant=?", (run_id, principal.tenant)).fetchone()
        if not row:
            raise KeyError("run not found")
        return dict(row)

    def review(self, principal, run_id, expected_hash, approved):
        principal.require("review:write")
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean")
        with self.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=? AND tenant=?", (run_id, principal.tenant)).fetchone()
            if not run:
                raise KeyError("run not found")
            if run["owner"] == principal.subject:
                raise Forbidden("reviewer must be different from proposer")
            if expected_hash != run["plan_hash"]:
                raise Conflict("review does not bind the current proposal")
            prior = db.execute("SELECT * FROM approvals WHERE run_id=?", (run_id,)).fetchone()
            if prior:
                if prior["reviewer"] == principal.subject and bool(prior["approved"]) == approved and prior["plan_hash"] == expected_hash:
                    return run["state"]
                raise Conflict("review is already final")
            if run["state"] != "awaiting_review" or self.clock() >= run["expires"]:
                raise Conflict("proposal expired or no longer reviewable")
            state = "queued" if approved else "rejected"
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?)", (run_id, principal.subject, expected_hash, int(approved), self.clock()))
            db.execute("UPDATE runs SET state=? WHERE id=?", (state, run_id))
            if approved:
                payload = {"tenant": run["tenant"], "plan": json.loads(run["plan"]), "plan_hash": run["plan_hash"]}
                db.execute("INSERT INTO outbox(id,run_id,payload,state,available) VALUES(?,?,?,?,?)",
                           (run_id, run_id, canonical(payload), "pending", self.clock()))
            self._event(db, run_id, "approved" if approved else "rejected", {"reviewer": principal.subject, "plan_hash": expected_hash})
            return state

    def claim(self, worker, lease_seconds=30, max_attempts=3):
        worker.require("delivery:write")
        with self.transaction() as db:
            row = db.execute("""SELECT o.* FROM outbox o JOIN runs r ON r.id=o.run_id
                WHERE r.tenant=? AND ((o.state='pending' AND o.available<=?) OR
                (o.state='leased' AND o.lease_until<=?)) ORDER BY o.available,o.id LIMIT 1""",
                (worker.tenant, self.clock(), self.clock())).fetchone()
            if not row:
                return None
            if row["attempts"] >= max_attempts:
                db.execute("UPDATE outbox SET state='dead_letter' WHERE id=?", (row["id"],))
                db.execute("UPDATE runs SET state='dead_letter' WHERE id=?", (row["run_id"],))
                self._event(db, row["run_id"], "retry_exhausted", {})
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE outbox SET state='leased',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?",
                       (self.clock() + lease_seconds, token, row["id"]))
            self._event(db, row["run_id"], "delivery_claimed", {"attempt": row["attempts"] + 1})
            return dict(db.execute("SELECT * FROM outbox WHERE id=?", (row["id"],)).fetchone())

    def finish(self, worker, item, error=None):
        worker.require("delivery:write")
        with self.transaction() as db:
            current = db.execute("SELECT o.*,r.tenant FROM outbox o JOIN runs r ON r.id=o.run_id WHERE o.id=?", (item["id"],)).fetchone()
            if (not current or current["tenant"] != worker.tenant or current["state"] != "leased"
                    or current["lease_token"] != item["lease_token"] or current["lease_until"] <= self.clock()):
                raise Conflict("delivery lease lost")
            if error:
                # Store a bounded error category, never exception messages with secrets.
                db.execute("UPDATE outbox SET state='pending',available=?,last_error=?,lease_token=NULL WHERE id=?",
                           (self.clock() + min(2 ** current["attempts"], 60), type(error).__name__, item["id"]))
                self._event(db, item["run_id"], "delivery_retry", {"error_type": type(error).__name__})
            else:
                db.execute("UPDATE outbox SET state='done',lease_token=NULL WHERE id=?", (item["id"],))
                db.execute("UPDATE runs SET state='completed' WHERE id=?", (item["run_id"],))
                self._event(db, item["run_id"], "delivery_completed", {})

    def events(self):
        # Local operator inspection only; do not expose this global view as a tenant API.
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY seq")]

    def verify_chain(self):
        previous = "0" * 64
        for event in self.events():
            body = {k: event[k] for k in ("run_id", "kind", "created", "previous_hash")}
            body["data"] = json.loads(event["data"])
            if event["previous_hash"] != previous or digest(body) != event["event_hash"]:
                return False
            previous = event["event_hash"]
        return True
