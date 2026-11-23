# Native relay tests

Provision two dedicated Chatmail profiles with configured relay accounts and an
accepted encrypted group conversation between them. Keep their directories separate;
each native server owns its profile exclusively. The two profiles may use the
same relay. The test does not deploy or reconfigure that relay.

Write a private JSON fixture outside the checkout:

```json
{
  "bot_accounts_dir": "/private/medre-chatmail-bot",
  "bot_account_id": 1,
  "bot_chat_id": 12,
  "peer_accounts_dir": "/private/medre-chatmail-peer",
  "peer_account_id": 1,
  "peer_chat_id": 15
}
```

Use actual account and chat IDs from the provisioning commands. Restrict fixture
permissions to its owner and stop clients using these profiles before running:

```bash
MEDRE_CHATMAIL_TEST_CONFIG=/private/chatmail-live.json \
  uv run --no-sync pytest tests/test_live.py -m live -q
```

The test sends nonce text in both directions. It observes native ingress through
the adapter, deferred SMTP handoff feedback, and independent native peer receipt.
The fixture must select the same group in both profiles. Its core admission/checkpoint callbacks are test sinks; it does not claim a full
routed core-storage proof. Queue correlation uses a temporary real SQLite journal.

The [official Docker relay](https://github.com/chatmail/docker) is a deployment
option. Its systemd/cgroup requirements are separate from the adapter and are not
enabled by the tests. Do not change host settings or grant container privileges
to run this suite. Use a preconfigured relay or a dedicated disposable machine
for that deployment. The default CI tier needs neither Docker nor a relay.

The same test kills the actual RPC server and verifies reconnect with a fresh
process. It also receives a group message into native storage while MEDRE is
stopped, then checks recovered admission from the saved core checkpoint. All
native processes and profile leases are released during teardown.

## Native reply qualification

The same two-profile test sends an attributed native reply after reconnect and
restart. The peer's own snapshot must retain the original peer-side message ID
and chat ID in its quote, the exact sender/origin body prefix, and the native
sender override. SMTP feedback alone does not qualify native quote presentation.

The SDK quote API test checks the pinned client's draft shape without delivering
a message. Passing it or the deterministic journal tests does not replace the
independent-peer test. Native reply promotion must not merge or release until
the live campaign passes on the exact core/provider revision pair under review. Record those revisions,
the pinned SDK versions and the peer result with qualification evidence; exclude
credentials and private profile paths.

The [2026-10-29 native reply qualification](evidence/native-replies-2026-10-29.json)
records a passing two-profile run with an explicit sender/origin label and an
independent peer-side quote after reconnect and restart. It uses the pinned
2.65.0 client/server and a disposable GreenMail relay. Freshly created groups
need native setup to settle in both directions before the history baseline;
confirm independent native messages in both profiles before running the gate.

The [RPC 2.66.0 qualification](evidence/native-replies-rpc-2.66.0-2026-10-29.json)
records a passing repetition on fresh native profiles with the 2.66.0 client and
server. The independent peer observes the renderer-derived quote and exact
sender/origin presentation after reconnect and restart. Native SDK contracts
also qualify profile exclusion and stopped-state capture/reopen using fresh
2.66.0 state. This is same-version restore evidence, not an upgrade path.
The earlier records describe their own revision pairs and SDKs and are not
qualification for a different SDK.

The [RPC 2.69.0 campaign](evidence/native-replies-rpc-2.69.0-2026-11-15.json)
records a passing GreenMail run and an independent quote receipt. Its
[companion source audit](evidence/native-replies-rpc-2.69.0-2026-11-15-source-audit.json)
reconciles the source digests against the retained original campaign report
and pre-publication export: all five match the named provider Git revision.
The retained independent peer snapshot also matches every public receipt field.

Three digest strings were copied incorrectly into the earlier public
[transcription](evidence/native-replies-rpc-2.69.0-2026-11-15-transcribed.json).
That copy remains byte-preserved for the audit; the campaign record restores
its source digests from the original retained artifacts. Near-matching digest
strings alone do not prove which bytes ran. The reconciliation recovers the
reported campaign provenance without repeating the campaign or making a fresh
measurement of historical runtime bytes. It does not authenticate the retained
observations. This local-relay evidence does not qualify the official Chatmail
deployment, sustained load, or a full routed core-storage path.

## Independent recipient qualification

`tests/test_live_independent_recipient.py` is a separate opt-in tier. It
routes three real attempts through the production render and durable-queue
path — a plain accepted group delivery, a renderer-derived native quote, and
a reply after RPC reconnect and adapter restart over ingress received while
MEDRE was stopped — and requires a second, independently controlled native
profile to observe the exact bound text in the target group. A third sibling
profile must actively observe its own group without seeing any routed text.
Cleanup covers only the test's temporary journal state.

The tier records three separately named facts per attempt and never merges
them: `queue_acceptance` (the adapter retained the attempt),
`relay_acceptance` (the outgoing SMTP server accepted the handoff, reported
under the identical ledger identity), and `recipient_observation` (the
declared peer profile's own native runtime decrypted the bound text). Queue
returns and relay acceptance cannot qualify as recipient observation. The
fixture names three distinct profiles, two group conversations with exactly
one external contact each, and the exact core and provider revisions; the
test refuses before sending when the running revisions differ, when the
installed client and server do not match the declared pins, or when a
required fixture field is missing. Receipt identities must be unique across
cases; duplicates and unknown peer tags are refusals, never evidence.

Write a private JSON fixture outside the checkout:

```json
{
  "core_commit": "core checkout HEAD as 40 hex characters",
  "provider_commit": "provider checkout HEAD as 40 hex characters",
  "bot_accounts_dir": "/private/medre-recipient-bot",
  "bot_account_id": 1,
  "bot_chat_id": 12,
  "peer_accounts_dir": "/private/medre-recipient-peer",
  "peer_account_id": 1,
  "peer_chat_id": 15,
  "sibling_accounts_dir": "/private/medre-recipient-sibling",
  "sibling_account_id": 1,
  "sibling_chat_id": 18
}
```

The bot and peer profiles must share one accepted encrypted group (the peer
selects it as `peer_chat_id`, the bot as `bot_chat_id`); the sibling profile
selects a different group in its own directory. Restrict fixture permissions
to the owner and stop other clients using these profiles:

```bash
MEDRE_CHATMAIL_RECIPIENT_CONFIG=/private/chatmail-recipient.json \
  uv run --no-sync pytest tests/test_live_independent_recipient.py -m live -q
```

The verdict record is validated by
`scripts/qualify_independent_recipient.py`, which is also exercised SDK-free
by the default tier in `tests/test_independent_recipient_gate.py`. The
validator checks completeness and consistency of a content-free record; it
neither performs the campaign nor verifies the truth of linked observations.
Record the relay kind and keep credentials, addresses and message bodies out
of the record. The relay constraint is the same as above: the official relay
image runs systemd as PID 1 with a writable cgroup volume and was observed
to exit immediately (status 255, no console output) when started without
container privileges on a cgroup-v2 host, so the containerized relay tier is
not built and this tier targets a preconfigured relay. The
[independent-recipient tier record](evidence/independent-recipient-tier-2026-11-23.json)
states the tier actually demonstrated at its own revisions.
