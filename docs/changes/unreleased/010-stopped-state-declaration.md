# Resolve Chatmail native state under retained stopped ownership

Replace the guessed numeric account directory/`db.sqlite` with a bounded
`accounts.toml` registry lookup of the actual UUID directory and `dc.db`.
Configuration-only previews refuse unknown native layouts. A single-account,
blob-empty profile can be captured via core `capture-config`, which retains
the existing MEDRE lease and Delta Chat `accounts.lock` during resolution,
SQLite/WAL backup, and publication. Include the MEDRE pending-send journal.
Refuse multi-account, blob-bearing, malformed, encrypted, unsafe, and unknown
native state until a coherent Delta Chat export is supported.
