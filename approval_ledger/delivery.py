import json
import sqlite3

from .domain import Conflict, digest


class TicketSink:
    """Independent SQLite tool simulator: durable idempotency at the receiver."""
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,payload_hash TEXT,payload TEXT)")

    def apply(self, key, payload):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload_hash FROM tickets WHERE id=?", (key,)).fetchone()
            if row:
                if row[0] != digest(payload):
                    raise Conflict("receiver idempotency key collision")
                return False
            db.execute("INSERT INTO tickets VALUES(?,?,?)", (key, digest(payload), json.dumps(payload)))
        return True

    def count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT count(*) FROM tickets").fetchone()[0]


def deliver_once(store, principal, sink, crash_after_apply=False):
    item = store.claim(principal)
    if item is None:
        return "idle"
    try:
        sink.apply(item["id"], json.loads(item["payload"]))
    except (TimeoutError, ConnectionError) as error:
        store.finish(principal, item, error)
        return "retry"
    if crash_after_apply:
        # Intentionally leave the lease outstanding; another worker must recover it.
        raise RuntimeError("injected crash after receiver commit, before acknowledgement")
    store.finish(principal, item)
    return "completed"
