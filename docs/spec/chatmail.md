# Chatmail provider contract

## Boundary

The `chatmail` provider is a hybrid adapter. It delegates account storage,
SMTP/IMAP, encryption and wire retries to the official RPC client and server.
Registration, configuration, metadata projection, rendering and fake adapters
MUST remain importable without the SDK.

Runtime MUST use an explicitly selected configured account and an allowlist of
accepted ordinary chats. A profile MUST have one MEDRE owner. Configuration
loading and runtime preflight MUST NOT provision accounts or create directories.
`init` is the explicit provisioning boundary. Native client and server versions
MUST agree with the native core version returned by the running server.

## Identity and ingress

Chat IDs, contact IDs and message IDs are local to a native profile. Canonical
IDs and native references MUST include the profile identity, preventing local ID
reuse from binding evidence from different profiles. Email addresses MUST NOT be
used as contact identity lookup keys.

First-use checkpoints baseline existing chat history before native I/O starts.
On restart, messages beyond the durable cursor are admitted with recovered
provenance; subsequent arrivals use live provenance. Native timestamps are
preserved when present. Complete eligible text is admitted through the core's
durable admission seam before its cursor advances. A checkpoint failure MUST
leave the message retryable with the same canonical identity. A partial text
message MUST NOT advance its cursor; other chats may continue independently.
`Available` text triggers one native full-download request until its state changes.
`Failure` remains retryable pending further native action, while
`Undecipherable` is terminal and advances without a text admission. New native
message IDs can replace an incomplete item after download.

SDK notifications are coalesced into a wakeup rather than retained in unbounded
Python account queues. Diagnostic-only event batches do not trigger a full
history scan; unknown event kinds still wake the scanner, and the interval poll
remains a fallback. Native storage remains the recovery source. A scan batch
handles at most 32 messages per chat before yielding to outbound processing.
The native API enumerates a chat's IDs without pagination; large chat histories
therefore have enumeration cost even though payload processing is bounded.

Controls, blocked contacts, self echoes, edits and media MUST NOT masquerade as
fresh text. Native reply references are restricted to the same profile and chat;
outbound replies and threads use core's plain-text fallback. Encryption/signature
metadata describes native evidence and MUST NOT imply bridge confidentiality.

## Outbound ownership

`deliver` accepts only renderer-ready payloads for an allowed chat. It MUST reject
before admission when immutable attempt provenance or the feedback sink is
absent, and MUST reject contradictory event/target provenance.

The private SQLite journal bounds queued attempts using `max_pending`. Admission
returns `deferred` with `local_queue` confirmation and no native message ID. The
worker persists a submission claim before invoking native send. A correlated
native message remains pending until the server reports state 26 or 28, which
permits a `remote_service` handoff fact for SMTP acceptance. State 24 produces
permanent failure. These facts MUST NOT imply recipient delivery or reading.

Exact provenance remains associated with the native ID until core acknowledges
feedback. Feedback failure MUST NOT resubmit the native message. Recovery polls
correlated native jobs; it MUST abandon recovered unsent work and uncertain
submissions rather than dispatch a stale attempt or risk a duplicate send.
Native message identity and chat ownership MUST match before reporting handoff.
The journal is adapter evidence, not authority over core outbox or retry state.

## Lifecycle and capabilities

Native blocking operations execute outside the event loop with bounded waits.
Timeout and cancellation terminate the owned native process and drain its client
threads. Transient session failure reconnects through a fresh profile-owning
session. Partial reconnect setup is torn down on failure; cursor maps are
published only after every configured chat passes validation and native I/O
has started. A failed admission or checkpoint for one chat leaves that chat
retryable without preventing other chats from progressing or outgoing feedback
from being flushed. Shutdown stops ingress and retains pending native correlation.
Diagnostics MUST omit private paths, QR payloads and credentials.

Capabilities advertise text, direct and group conversations, store-and-forward,
and fallback replies/threads. Each instance exposes its configured text budget.
Attachments, native mutations, read receipt forwarding, typing notifications,
presence, puppeting and bridge-wide encryption are not advertised.

Failed partial downloads are retried at most once per polling interval; native
`InProgress` work is not resubmitted. Profile CLI commands honor environment
overrides, escape terminal controls in peer labels, and keep native exception
details out of public output. No native session remains open during QR input.

Recovered queued attempts MUST report that native submission never occurred;
only recovered submission claims have an uncertain native outcome. Neither is
resubmitted using an expired dispatch attempt. RPC request rejections during a
conversation scan leave the native session running and other chats progressing.
The polling interval bounds retries after a rejected download.

Profile shutdown MUST release its lease even if draining native calls times out.
Peer display labels MUST be printable on one line before relay attribution.

## Per-message native attribution

By default, `native_sender_attribution` adds the source sender and optional origin
label to Delta Chat's native `overrideSenderName` presentation field. It MUST NOT
change the sending account, contact profile, or email address. Source verification
annotations remain attached to the display label. The existing body prefix remains
independently controlled by `relay_prefix`; setting `native_sender_attribution: false`
disables the native presentation field.

Native names MUST fit 1024 UTF-8 bytes and contain no control characters,
bidirectional controls, invisible Unicode format characters (except U+200C and
U+200D joiners), or line separators. A valid label MUST also contain a visible base character;
joiners and combining marks alone cannot form a sender name. JSON Schema `maxLength` counts Unicode
characters, not encoded bytes; the sender-name validator enforces the byte cap
at ingress, rendering and delivery boundaries. Unsafe, absent or oversized
display names omit only this optional field;
the configured body remains intact. Delivery validation MUST reject malformed
renderer payloads rather than silently changing their claims.

The durable journal MUST bind native presentation to the same immutable attempt as
the body and destination. Migration of older rows adds an absent sender override
without changing native correlations or recovery policy. Incoming native overrides
are retained as bounded `sender_override` display claims, separately from the
profile-scoped contact identity and `sender_label`. They MUST NOT grant original
sender identity, encryption, relation binding, or delivery authority.

This carrier conveys native sender/origin presentation, not the complete shared
bridge-context object or canonical event references. SMTP acceptance still yields
only `remote_service` confirmation; no recipient delivery or reading is implied.

## Native health notifications

The provider MUST use the same retained native state for health probes and the
optional `AdapterContext.report_health` sink. Reports MUST describe settled
`healthy`, `degraded` or `failed` observations for the bound adapter. Startup and
stop MUST delimit the reporting lifetime; retired callbacks MUST NOT publish
for a replacement. Observer exceptions MUST NOT interrupt native operations,
release ownership, create receipts or authorize another send. Contexts without
the sink retain explicit-probe compatibility.

The existing selected-account connectivity observation MUST govern health:
a native connectivity value of at least 3000 is healthy; lower connectivity is
degraded. Native worker failure, recovery and permanent account/setup failures
MUST update retained health. Account connectivity does not replace SMTP,
recipient delivery or read evidence. No extra connectivity poller is introduced.

Account readiness MUST NOT be reported before a native connectivity observation.
Prepared startup MAY remain `starting` until the owner performs that observation;
abnormal owner failure MUST remain reportable without fabricating connectivity.

## Stopped-state declaration

The adapter MUST keep configuration-only `coverage` pure and SDK-free;
this preview MUST return `unsupported` rather than guessing an account
path. Under a retained existing MEDRE profile SQLite lease **and** native
`accounts.lock` flock, `resolve_coverage` MAY read a bounded, validated
`accounts.toml` and derive the configured `account_id` mapping through
`AccountConfig.dir`. The database MUST be `dc.db` inside that UUID
account directory, never `<account_id>/db.sqlite`.

Automatic `capture-config` selection MUST refuse missing/ambiguous IDs,
multiple accounts, malformed registry, traversal, symlinked/hard-linked files,
unsafe opaque input, unexpected native files, nonempty `dc.db-blobs`, or unsupported SQLite
state. It MUST select the exact registry, native database as SQLite backup
(including committed WAL), and `chatmail-pending.sqlite` journal containing
accepted/uncertain send fences. The native `accounts.lock` and MEDRE SQLite
lease are excluded from captured bytes: neither file grants ownership after
restore. The empty blob directory MAY be reconstructed by native startup.

Both locks MUST be retained from resolution through capture and publication,
with reverse-order cleanup on failure. Native account-manager exclusion uses
the released Delta Chat core's `fd_lock` semantics on supported Unix hosts;
no file is created/truncated merely to acquire a lock. A running pinned native account manager MUST block acquisition of its
accounts.lock. Non-cooperating processes and other hosts remain outside the
machine-local exclusion claim. A blob-bearing or multi-account
profile requires a separately qualified coherent native export.

## Operator command namespace

The provider MUST contribute its profile commands under `medre adapter chatmail`
through MEDRE's isolated CLI registration and dispatch hooks. Generic help MUST
remain SDK-free and MUST NOT open a native profile. The standalone
`medre-chatmail` entry point MUST use the same parser definitions and execution
policy. Both paths MUST retain exit status, hidden secret input, profile locks,
native cleanup, and configuration/environment selection. Provisioning remains
an explicit operator action; runtime startup MUST NOT create an identity.

## Common diagnostic projection

Native and SDK-free adapters MUST expose the core common diagnostic keys.
`health` MUST match the same retained observation returned by `health_check`;
`mode` MUST distinguish `live` from `fake`. Legacy provider-specific keys MUST
remain available. Unobserved connection state, uncounted delivery failures,
and unavailable reconnect/error details MUST remain `None`, rather than
fabricated success, zero counts or inferred remote reachability. Cumulative
`reconnects` MUST NOT be relabeled as consecutive `reconnect_attempts`.
