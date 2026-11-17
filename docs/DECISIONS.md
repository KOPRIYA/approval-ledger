# Design decisions

## Keep proposals separate from authority

A model is a proposal generator. Its output never supplies the authenticated tenant or reviewer.
The allowlist has one write action, `create_followup`, deliberately excluding service restarts,
shell execution, arbitrary URLs and database mutations. This keeps the first experiment small
enough to reason about. Adding an action requires a schema, authorization policy and receiver contract.

## Bind approval to content

Approval includes a hash of the tenant and complete plan. Reviewing a stale hash fails. The
proposer cannot approve their own action; strings such as `"false"` are not accepted as booleans.
Expiry is checked at review time. Already approved work can be delivered later: the TTL limits
the review window, not the delivery window. A production policy might require reapproval after delay.

## Persist the approval and delivery intent atomically

The outbox record is inserted in the same transaction as the approval and workflow transition.
This closes the gap in which the review succeeds but the process dies before scheduling delivery.
SQLite `BEGIN IMMEDIATE` serializes competing writes in this single-host reference implementation.

## Put deduplication at the effect boundary

Acknowledgement can be lost after a remote effect succeeds. Retrying is necessary for liveness,
but worker-side bookkeeping alone cannot prevent duplicate effects. The ticket receiver stores
the idempotency key and its payload hash transactionally with the ticket. Reusing the key for
different content is rejected. The local receiver is a model of that contract, not a real ticket API.

## Fence stale workers

Every claim has a fresh lease token. Finishing an expired or superseded lease fails even if the
worker still holds the old job. This prevents an old worker from overwriting newer state. It
cannot stop an already-running network call; that is why receiver deduplication is still required.

## Keep framework replay outside the effect boundary

LangGraph checkpoints record orchestration progress, but a checkpoint is not a transaction with
an external service. The graph retrieves or creates an idempotent proposal, pauses for review,
and returns the ledger status. It never executes the write action. The outbox owns delivery.

## Prefer falsifiable evidence to expansive claims

The core demo deliberately has no LLM: it isolates recovery semantics from model variability.
The HTTP planner enables later model evaluation. Both are necessary experiments, but they
answer different questions. Twenty-seven invariant tests do not establish model accuracy,
enterprise security, user adoption or distributed-system throughput.
