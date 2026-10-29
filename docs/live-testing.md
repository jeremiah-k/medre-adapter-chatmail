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
the live campaign passes on the candidate revision pair. Record those revisions,
the pinned SDK versions and the peer result with qualification evidence; exclude
credentials and private profile paths.

The [2026-10-29 native reply qualification](evidence/native-replies-2026-10-29.json)
records a passing two-profile run with an explicit sender/origin label and an
independent peer-side quote after reconnect and restart. It uses the pinned
2.65.0 client/server and a disposable GreenMail relay. Freshly created groups
need native setup to settle in both directions before the history baseline;
confirm independent native messages in both profiles before running the gate.
