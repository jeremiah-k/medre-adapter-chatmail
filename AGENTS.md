# Repository guidelines

Runtime code is under `src/medre/adapters/chatmail`; tests and schemas belong to
this provider. Core owns shared contracts and cross-provider qualification.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for locked uv setup, editable core/fork
checkouts, SDK selection and test commands.

Use the committed executable launcher for quality checks:

```bash
.trunk/trunk check --fix --all
.trunk/trunk check --all --no-fix --ci
```

Run `.trunk/trunk upgrade` when refreshing tools, then fix and check before
committing. Python policy lives in `pyproject.toml`; tool versions live in
`.trunk/trunk.yaml`. Preserve published schema bytes and upstream launcher bytes.

Run tests from this repository with the selected core environment. SDK, service,
local integration, soak and hardware tiers are explicit; report exactly which
ran. Runtime changes include their relevant specification/schema updates and
regression tests. PR descriptions carry compatibility, behavior and validation;
release notes are optional and use descriptive filenames rather than a sequence.

Keep credentials, identities, local state, `.zcode/`, `.core/` and generated
reports outside Git. Preserve genuine commit authorship and keep each commit
focused on one concern.
