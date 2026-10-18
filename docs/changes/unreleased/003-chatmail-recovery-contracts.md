# Chatmail recovery and operator contract hardening

- Isolate non-session ingress/checkpoint errors by conversation; always flush
  deferred outbound work even when an ingress scan fails.
- Use a fresh native session after a partially failed reconnect and publish
  cursors only after all configured conversations and I/O startup succeed.
- Initiate native full-text downloads on `Available`, retain `Failure` for
  retry, and skip terminal `Undecipherable` text without blocking later IDs.
- Move definite preflight checks ahead of the durable native-submission claim;
  keep ambiguous native sends abandoned rather than risk duplicates.
- Ignore diagnostic-only wake batches while retaining unknown events and
  periodic poll fallback.
- Align schema with adapter-instance config blocks, make join timeout guidance
  identify the pending chat, preserve non-contention profile errors, and share
  Ruff policy with CI.

No migration or changes to the core event/delivery envelope are required.

Failed native downloads are retried with polling-interval pacing so a transient
download failure cannot block a conversation permanently.

Release profile ownership after cleanup failures, distinguish definitely unsent
recovered queue entries from uncertain submissions, and keep ingress request
rejections local to their conversation. Reject multiline peer attribution labels.
