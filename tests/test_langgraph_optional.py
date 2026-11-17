import importlib.util
import tempfile
import unittest
from pathlib import Path

from approval_ledger.cli import identities
from approval_ledger.planner import FixturePlanner
from approval_ledger.store import Store


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "optional LangGraph dependencies not installed")
class LangGraphTests(unittest.TestCase):
    def test_persisted_interrupt_review_resume(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.types import Command
        from approval_ledger.langgraph_flow import build_graph
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "ledger.db")
            store.seed()
            proposer, reviewer, _ = identities()
            config = {"configurable": {"thread_id": "incident-1"}}
            with SqliteSaver.from_conn_string(str(root / "graph.db")) as saver:
                graph = build_graph(store, FixturePlanner(), proposer, saver)
                result = graph.invoke({"request_key": "case-1", "incident": "checkout latency"}, config)
                self.assertTrue(result["__interrupt__"])
                run_id = result["run_id"]
                store.review(reviewer, run_id, result["plan_hash"], True)
            with SqliteSaver.from_conn_string(str(root / "graph.db")) as saver:
                graph = build_graph(store, FixturePlanner(), proposer, saver)
                final = graph.invoke(Command(resume=True), config)
                self.assertEqual(final["status"], "queued")
