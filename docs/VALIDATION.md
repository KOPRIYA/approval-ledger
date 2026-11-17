# Executed validation

Date: 17 November 2026. Runtime: Python 3.9.6; macOS on Apple Silicon; SQLite FTS5.

27 core tests passed. One optional LangGraph persistence test is skipped because dependencies
could not be installed in this session. The package registry was unavailable. No API key was used.

The test suite includes an actual separate worker process that commits a ticket and then exits
without acknowledgement. After lease expiry, another attempt completes the workflow with one
ticket total. Other cases cover simultaneous claims, stale-worker acknowledgement, retries,
dead-letter transition, scoped retrieval, cross-tenant review, self-approval, stale/expired
approval, request replay, payload collisions, forged evidence and audit-record alteration.

`examples/recovery.json` and `examples/recovery.html` are outputs from the deterministic local
demonstration. Its planner is a fixture; the SQLite state transitions and ticket writes are real.
The demonstration uses a controlled clock rather than waiting for lease expiry in real time.

Not executed: live LLM inference, external OAuth provider, native MCP client, LangGraph runtime,
container image, GitHub Actions, network failure proxy, load test, distributed deployment or
production traffic. These boundaries are deliberate and should remain visible in published results.
