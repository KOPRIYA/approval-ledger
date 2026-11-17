import argparse
import html
import json
import time
from pathlib import Path

from .delivery import TicketSink, deliver_once
from .domain import Principal
from .planner import FixturePlanner, HTTPPlanner
from .store import Store


def identities():
    return (Principal("operator", "demo", frozenset({"evidence:read", "proposal:write", "run:read"})),
            Principal("reviewer", "demo", frozenset({"review:write", "run:read"})),
            Principal("worker", "demo", frozenset({"delivery:write"})))


def demo(directory):
    root = Path(directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("choose a new empty output directory")
    root.mkdir(parents=True, exist_ok=True)
    now = [time.time()]
    store = Store(root / "ledger.db", clock=lambda: now[0])
    store.seed()
    proposer, reviewer, worker = identities()
    sink = TicketSink(root / "tickets.db")
    evidence = store.retrieve(proposer, "checkout latency")
    plan = FixturePlanner().plan("checkout latency", evidence)
    run = store.propose(proposer, "demo-incident-1", "checkout latency", plan, evidence)
    before_review = deliver_once(store, worker, sink)
    store.review(reviewer, run["id"], run["plan_hash"], True)
    crashed = False
    try:
        deliver_once(store, worker, sink, crash_after_apply=True)
    except RuntimeError:
        crashed = True
    after_crash_count = sink.count()
    # Advance a controlled clock instead of sleeping 31 seconds in the test/demo.
    now[0] += 31
    restarted = Store(root / "ledger.db", clock=lambda: now[0])
    recovered = deliver_once(restarted, worker, sink)
    result = {"mode": "offline_fixture_planner_with_real_sqlite_transactions",
              "production": False, "llm_called": False, "before_review": before_review,
              "crash_injected": crashed, "tickets_after_crash": after_crash_count,
              "recovery": recovered, "tickets_after_retry": sink.count(),
              "run_state": restarted.get(proposer, run["id"])["state"],
              "audit_chain_valid": restarted.verify_chain(), "events": restarted.events()}
    (root / "demo.json").write_text(json.dumps(result, indent=2) + "\n")
    timeline = "".join("<li><b>%s</b><pre>%s</pre></li>" % (html.escape(e["kind"]), html.escape(e["data"])) for e in result["events"])
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Approval Ledger - recovery evidence</title><style>body{font:16px system-ui;color:#192a35;background:#f3f5f4;max-width:1000px;margin:48px auto;padding:20px}h1{font-size:40px}p{line-height:1.7}.card{padding:24px;background:white;border-radius:12px;margin:18px 0}li{padding:14px;border-left:3px solid #26776b;list-style:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}</style>
<p>LOCAL ENGINEERING EXPERIMENT</p><h1>The worker crashed.<br>The ticket was created once.</h1>
<p>A deterministic planner proposed an action. A separate reviewer approved its exact payload.
The worker committed to a separate ticket database, then crashed before acknowledging delivery.
After the lease expired, a restarted worker retried the same idempotency key.</p>
<div class="card"><b>Tickets before recovery: %s &nbsp; / &nbsp; After retry: %s</b><p>Final workflow: %s. Audit chain valid: %s.</p></div>
<div class="card"><h2>Event timeline</h2><ol>%s</ol></div>
<p>No LLM was called. This validates transaction/recovery behavior, not model quality or production scale.
The receiver's durable idempotency contract is necessary for the single-effect outcome.</p></html>""" % (
        after_crash_count, sink.count(), result["run_state"], result["audit_chain_valid"], timeline)
    (root / "report.html").write_text(page)
    print(json.dumps({k: v for k, v in result.items() if k != "events"}, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approval Ledger local reference workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    demo_parser = sub.add_parser("demo")
    demo_parser.add_argument("--out", default="local/demo")
    for name in ("propose", "review", "deliver", "inspect"):
        command = sub.add_parser(name)
        command.add_argument("--directory", default="local/session")
        if name == "propose":
            command.add_argument("--incident", required=True)
            command.add_argument("--key", required=True)
            command.add_argument("--base-url")
            command.add_argument("--model")
        if name in ("review", "inspect"):
            command.add_argument("--run-id", required=True)
        if name == "review":
            command.add_argument("--plan-hash", required=True)
            command.add_argument("--decision", choices=("approve", "reject"), required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        demo(args.out)
        return
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "ledger.db")
    store.seed()
    proposer, reviewer, worker = identities()
    if args.command == "propose":
        if bool(args.base_url) != bool(args.model):
            parser.error("supply --base-url and --model together")
        existing = store.lookup(proposer, args.key, args.incident)
        if existing:
            result = existing
        else:
            planner = HTTPPlanner(args.base_url, args.model) if args.base_url else FixturePlanner()
            evidence = store.retrieve(proposer, args.incident)
            result = store.propose(proposer, args.key, args.incident, planner.plan(args.incident, evidence), evidence)
    elif args.command == "review":
        result = store.review(reviewer, args.run_id, args.plan_hash, args.decision == "approve")
    elif args.command == "deliver":
        result = deliver_once(store, worker, TicketSink(root / "tickets.db"))
    else:
        result = store.get(proposer, args.run_id)
    print(json.dumps(result, indent=2))
