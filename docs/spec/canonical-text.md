# Canonical message text

Text rendering MUST select source text through core's `select_message_body`.
A payload with `formatting` and a string `body` uses that body; otherwise a
legacy `text` alias takes precedence over `body`. A malformed explicit body or
selected value MUST fail before native submission. Invalid optional formatting
MUST NOT decorate a different compatibility alias. Relation fallbacks follow
selection; prefixes, native identity, budgets and delivery evidence remain
adapter-owned. This change requires core's text-selection revision (`select_message_body`, released in core 0.1.0).

Candidate qualification MUST use an explicit immutable core revision. Ordinary
CI MUST retain core main as its baseline; dependency merges precede consumers.
