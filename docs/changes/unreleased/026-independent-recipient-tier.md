# Add the independent-recipient qualification tier

Add `tests/test_live_independent_recipient.py`, an opt-in `live` tier that
routes three real attempts through the production render and durable-queue
path on a preconfigured relay: a plain accepted group delivery, a
renderer-derived native quote, and a reply sent after RPC reconnect and
adapter restart over ingress received while the adapter was stopped. A
verdict requires a second, independently controlled native profile to
observe the exact bound text in the target group, plus a third sibling
profile that must not observe it.

Each attempt records three separately named facts: local queue acceptance
(`deferred` / `local_queue`), SMTP relay acceptance
(`DeferredHandoffCompleted` / `remote_service` under the identical ledger
identity), and an independent recipient observation from the peer profile's
own native runtime. Queue returns and relay acceptance never qualify as
recipient observation.

Add `scripts/qualify_independent_recipient.py`, a fail-closed, SDK-free
validator for the content-free verdict record, and
`tests/test_independent_recipient_gate.py` proving in the default tier that
a positive verdict is impossible while the peer is disabled or not
observing, a source revision does not match the fixture, a sibling
destination receives the message, a ledger identity disagrees, a receipt
witnesses two cases, or a peer tag is undeclared. The live tier additionally
refuses before sending when the installed SDK does not match the declared
pins or the running core/provider revisions differ from the fixture.

The official relay image runs systemd as PID 1 with a writable cgroup
volume; it exits immediately when started unprivileged, so the containerized
relay tier remains unbuilt and this tier targets a preconfigured relay. No
core resend or receipt policy changes.
