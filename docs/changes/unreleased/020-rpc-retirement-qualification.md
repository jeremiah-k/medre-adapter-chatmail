# Qualify RPC retirement and profile exclusion

Qualify the pinned RPC client and server against the retirement contract with
SDK-tier tests: pending calls resolve as soon as the native process dies, the
SDK client's `close()` blocks indefinitely on a hung server until the owned
kill path fires, the native `accounts.lock` excludes even a stopped manager
until process death, shutdown reaps the native process before releasing the
profile lease, and interpreter exit stays unblocked on every supported Python
version.

Document the proven contract: the native lock is the machine-local exclusion
authority while the MEDRE SQLite lease gates MEDRE commands, shutdown returns
with the process reaped and the lease released after the bounded drain, and a
refused native acquisition surfaces as a retryable session start failure. The
qualification requires no adapter code changes.
