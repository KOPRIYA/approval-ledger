# What would make this stronger evidence

1. Install the optional dependencies and execute the persisted LangGraph interrupt/resume test.
   Record exact versions and resolve any version mismatch before claiming framework experience.
2. Run two real models on a frozen, manually reviewed incident corpus. Measure proposal validity,
   evidence correctness, action correctness, reviewer rejection rate, tokens, cost and p50/p95
   model latency. Keep model results distinct from the recovery suite.
3. Add held-out adversarial documents, ambiguous incidents, missing runbooks and requests outside
   the tool allowlist. A regex/fixture passing these cases is not evidence that an LLM passes.
4. Integrate a real sandbox ticket API with documented idempotency semantics. Inject connection
   loss after the server commits. Record what happens if that API lacks idempotency support.
5. Replace the demo identities with an actual identity provider, introspection/JWKS verification,
   validated audience and issuer, tenant mapping, authorization tests and HTTP access controls.
6. Add a PostgreSQL outbox and a Kafka event consumer only when testing multi-worker/multi-node
   behavior. Demonstrate duplicate events, backpressure, poison messages, offset commits and lag.
7. Ask real users to try the workflow, with consent and synthetic incidents first. Record failed
   tasks, usability changes and adoption. Do not claim internal-customer production use for a demo.

## Interview drill

Explain why a checkpoint cannot make a remote API call exactly once; why approval and outbox
insertion share a transaction; what a lease token prevents; where tenant isolation happens;
what the audit chain cannot prove; and how to evaluate the model independently of the state machine.
Then modify one invariant without assistance and write a regression test. That ability matters
more than hiding the use of coding tools.

References: [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
[persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[OAuth introspection, RFC 7662](https://www.rfc-editor.org/rfc/rfc7662).
