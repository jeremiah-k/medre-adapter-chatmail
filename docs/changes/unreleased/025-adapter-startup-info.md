# Expose startup-info facts at the ready transition

The adapter now offers a pure-read `startup_info()` provider that core
discovers duck-typed and renders at the adapter's ready transition. It
reports the configured account identity, the native profile identity
captured at connect time, and the installed `deltachat-rpc-client`
distribution version read from package metadata (never a source
literal). The native RPC server's version is only knowable through RPC
traffic, so it is never probed; facts that are not held yet are omitted
rather than gathered. The hook performs no RPC, native, or filesystem
I/O, mutates no session state, and never gates, blocks, or delays
readiness — failure or timeout simply yields no facts. Fake adapters
mirror the same seam through the shared builder without ever reporting
a profile.
