# Adapter maintenance

Maintainer-facing contracts for the Chatmail adapter distribution. The
[README](../../README.md) keeps the operator journey; this page holds the
material behind it.

## Setup-instructions seam

Chatmail declares no `medre config native` form: a profile needs the pinned
native tooling that `medre adapter chatmail` drives, so the provider
contributes these commands instead of an offline setup form. On cores that
provide the typed setup-instructions seam, the provider declares this journey
as ordered setup instructions and
`medre config native --transport chatmail --adapter-id mail --output PATH`
renders it offline with an `informational_only` classification: the
contributed commands appear verbatim with a purpose and an evidence note
each, core never executes them, and no file is written. On older cores this
README remains the source of the journey.
