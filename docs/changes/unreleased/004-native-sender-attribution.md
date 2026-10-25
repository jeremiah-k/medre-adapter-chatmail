Chatmail now presents the original sender and origin through Delta Chat's per-message
sender override while retaining the configured body prefix. The durable queue binds
that display claim to the exact attempt and migrates existing pending correlations.
`native_sender_attribution: false` disables the native display field. Invalid or
oversized names leave body attribution intact. Incoming overrides remain untrusted
metadata and never replace profile-scoped sender identity or delivery evidence.
