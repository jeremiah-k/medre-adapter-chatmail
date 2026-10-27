# Refuse hard-linked native Chatmail state during stopped capture

Reject hard-linked account-registry files, native lock inodes, SQLite database
and sidecars, and the durable pending-send ledger. Those links may alias
external state despite symlink checks. An ambiguous source requires a coherent
native export rather than a snapshot described as complete.
