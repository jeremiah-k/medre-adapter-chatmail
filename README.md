# MEDRE Chatmail adapter

Bridge accepted Chatmail group conversations and direct chats through the official
Python RPC client and native Chatmail core. The provider supplies plain-text
rendering, durable ingress checkpoints, a bounded outbound queue, and an
SDK-free fake for offline development.

## Install

For a persistent bridge, keep core and adapters in one locked uv project:

```bash
uv init --bare medre-bridge
cd medre-bridge
uv add 'medre @ git+https://github.com/jeremiah-k/medre.git@main' \
  'medre-adapter-chatmail @ git+https://github.com/jeremiah-k/medre-adapter-chatmail.git@main'
```

Both RPC packages are installed together; no Rust build or separate daemon
installation is needed. Use immutable Git revisions for reproducible deployments.
`uv run --locked` preserves the project lock when starting commands.

For a persistent MEDRE tool installation, `medre adapters install chatmail`
adds the provider to the same environment. Provision through `medre adapter
chatmail`; a separately exposed helper executable is unnecessary. The
standalone `medre-chatmail` entry point remains available in activated project
environments and uses the same arguments and execution path.

## Set up a profile

Provisioning proceeds through explicit stages: prerequisites, profile
provisioning, conversation acceptance and discovery, then offline
configuration and verification. Profile creation and invitation acceptance
happen only on these commands; runtime startup never creates an identity or
accepts an invitation. Chatmail declares no `medre config native` form:
a profile needs the pinned native tooling that `medre adapter chatmail`
drives, so the provider contributes these commands instead of an offline
setup form. On cores that provide the typed setup-instructions seam, the
provider declares this journey as ordered setup instructions and
`medre config native --transport chatmail --adapter-id mail --output PATH`
renders it offline with an `informational_only` classification: the
contributed commands appear verbatim with a purpose and an evidence note
each, core never executes them, and no file is written. On older cores this
README remains the source of the journey. Each stage below states what its
observations prove and what they leave unproven.

> The commands in this journey use `--adapter mail` as an **example** instance.
> Replace that value consistently with the actual configured provider ID before
> running commands. `medre config native` displays instructions only; it does
> not substitute IDs, provision accounts, or create a bridge.

### Prerequisites

- The provider installed in MEDRE's environment (above), with the RPC client
  and native server it ships together.
- A relay's private `DCACCOUNT:` or `DCLOGIN:` QR payload for `init`.
- An invitation QR payload (`OPENPGP4FPR:` or an `i.delta.chat` link) for
  each conversation to bridge.
- A terminal with echo control for hidden input, or `--qr-stdin` for
  automation. Credentials never belong in command arguments or committed
  files.
- The bridge stopped and no other client using the profile. The profile is
  dedicated to one adapter instance; concurrent MEDRE commands refuse the
  profile lock, and an unrelated Chatmail client does not honor that lock.

### Stage 1: provision the profile

```bash
uv run --locked medre adapter chatmail init --adapter mail
uv run --locked medre adapter chatmail accounts --adapter mail
```

`init` reads the relay's private QR payload with hidden terminal input,
creates the profile on this explicit command only, and keeps an existing
configured account. `accounts` lists local account IDs and their configured
state without exposing credentials. A configured profile proves that the
native core reports this account configured. Profile provisioning is not
relay service acceptance: relay-side limits surface only through later
delivery feedback, and this stage sends nothing.

### Stage 2: accept conversations and discover chat IDs

```bash
uv run --locked medre adapter chatmail join --adapter mail
uv run --locked medre adapter chatmail chats --adapter mail --groups --json
```

`join` reads one contact or group invitation, completes the native secure
join for it, and waits until the native core reports that the chat can send.
`chats` lists ordinary conversations of this profile, groups first;
`--groups` limits discovery to groups, and omitting it includes direct
conversations. A joined chat proves that this conversation is accepted and
natively sendable. It does not prove that bridge messages reach their
recipients.

### Stage 3: configure and verify offline

Save a private `connections.yaml` using the accepted chat IDs from `chats`:

```yaml
adapters:
  chatmail:
    mail:
      adapter_kind: real
      account_id: 1 # Replace with the ID reported by 'accounts'.
      chats: [12] # Replace with the accepted chat IDs from this profile.
      origin_label: Chatmail
```

Then validate without contacting the profile:

```bash
uv run --locked medre config check --config connections.yaml
uv run --locked medre routes plan --config connections.yaml
```

Both commands run entirely offline against the typed configuration model.
This fragment describes one connection, not a complete bridge: its route plan
has no delivery legs until a second adapter and explicit routes are added.
Use `medre config bridge` to construct those legs, then run both checks again.
Neither a valid config nor a nonempty route plan proves peer delivery.
`medre smoke` exercises the SDK-free fake only; real configurations are
rejected by fake smoke. An initial `deferred` result with `local_queue`
confirmation records retention in the private pending-delivery journal only;
SMTP handoff is pending. Later correlated `remote_service` feedback confirms
SMTP-server acceptance, with notes differentiated by native state: the outgoing
server accepted the message but recipient delivery and reading are unconfirmed,
or a native MDN (read receipt) confirms reading.

## Configuration reference

Add routes using decimal channel strings, such as `source_channel: "12"` or
`dest_channel: "12"`. Chat IDs are local to a profile; contact addresses are not
used to guess conversation identity. Run `medre config check` before starting
the bridge. Explicit `accounts_dir`, `state_dir` and `rpc_server_path` overrides
are available for existing profiles and native server installations.

Profiles default to MEDRE's instance state directory, under
`adapters/mail/chatmail/accounts`. `MEDRE_HOME` and the usual XDG paths are
honored. Use the same `--adapter` and `--accounts-dir` options for setup and
runtime configuration. Once a runtime YAML exists, `--config bridge.yaml
--adapter mail` selects its profile and account directly, including for the
`chats` discovery above.

Use `--qr-stdin` to supply a QR payload through standard input in automation.
Keep the profile, MEDRE database and pending-delivery journal together when
backing up state.

## Delivery and rendering

- Preserve plain text and MEDRE attribution. When core uniquely binds an earlier
  message to a native reference in the same private profile and chat,
  a reply uses Delta Chat's native quote UI (`quotedMessageId`). If the target
  cannot be verified, retain a readable inline reply instead. Threads still use
  core's inline fallback. Refuse text exceeding the configured UTF-8 budget.
- Baseline existing history on first use. Resume after the last durable checkpoint
  on subsequent starts, committing the cursor only after canonical admission.
- Keep partial text messages pending. Exclude self echoes, blocked contacts,
  informational controls, edits and unsupported media from fresh-message ingress.
- Return local queue admission as deferred delivery. Report SMTP acceptance only
  after the native core confirms outgoing-server acceptance; recipient delivery
  and reading remain unconfirmed without separate MDN evidence. Neither claims
  relay acceptance or bridge confidentiality.
- Retain exact attempt/native-ID correlation until core accepts delivery feedback.
  Recover correlated native jobs without resending. Abandon unsent or uncertain
  work recovered across a restart, requiring inspection before an explicit retry.
- Expose encryption/signature observations in native metadata, alongside the
  sender's chatmail address, native send/receive timestamps and validated
  conversation facts; attribution carries the address as the sender handle.
  A bounded opted-in `public_facts` projection (currently
  `encrypted_and_signed`) feeds core's transport-neutral bridge context.
  Confidentiality across the bridge and identity puppeting are not implied.

Attachments, native edits/deletes/reactions, forwarded read receipts, typing
notifications and puppeting are not advertised. The native core owns SMTP/IMAP,
cryptography, protocol retries and account storage. MEDRE owns routing and
lifecycle authority.

See the [provider contract](docs/spec/chatmail.md) and
[configuration schema](docs/schemas/config.schema.json).

## Develop and test

Create an isolated environment from checkouts at any paths:

```bash
uv venv
uv pip install -e /absolute/path/to/medre -e '.[test]'
uv run --no-sync pytest -q
uv run --no-sync pytest -q -m chatmail_sdk
```

The default tier excludes native SDK and live tests. The SDK tier runs the actual
pinned RPC client and server against temporary local profiles without an external
mail service. CI runs both tiers on Python 3.11–3.14 and qualifies the adapter
against core `main`, resolved once per run. Keep `MEDRE_CORE_REF` unset;
workflow dispatch accepts an exact candidate core revision.
The run summary records both resolved commit IDs. SDK constraints are exported
from core's lock without installing private core test helpers into this provider.

The [live test](tests/test_live.py) accepts an explicitly prepared relay/profile
fixture. Native quote presentation requires that independent-peer campaign on
the candidate revision before merging or releasing native reply support; API-shape
tests alone do not qualify it. See [live testing](docs/live-testing.md). All service fixtures use
ordinary container permissions; no host system configuration is required by this
provider's tests.

Per-message native sender attribution is enabled by default. Delta Chat recipients
see the original sender and origin as a message display name while the bridge
account and contact profile remain unchanged. Set `native_sender_attribution: false`
to keep only the configured body prefix. See [the provider contract](docs/spec/chatmail.md#per-message-native-attribution)
and [the two-receipt native qualification](docs/evidence/native-sender-2026-10-25.json).
