# Development

Use the committed launcher for formatting and linting:

```bash
.trunk/trunk check --fix --all
.trunk/trunk check --all --no-fix --ci
```

CI runs the same all-file check. Run `.trunk/trunk upgrade` only for an
intentional toolchain refresh; review the pin changes, then rerun both checks.
Tool versions are owned by `.trunk/trunk.yaml`;
Python policy is owned by `pyproject.toml`. Renovate tracks the inline
upstream-source annotations on external pins; preserve them when editing. The
formatter targets the package's minimum supported Python. Preserve frozen
schema bytes; local `.zcode/`, `.core/`, reports and caches are ignored.

For tests, use a MEDRE source checkout and its locked uv development environment:

```bash
uv sync --project ../medre --locked --extra dev
uv run --project ../medre --no-sync medre dev sync --project ../medre --no-sdk chatmail --editable chatmail=.
uv run --project ../medre --no-sync medre dev test --project ../medre --editable chatmail=. chatmail -- -q
```

For native SDK contracts, explicitly replace the SDK-free selection:

```bash
uv run --project ../medre --no-sync medre dev sync --project ../medre --sdk chatmail --editable chatmail=.
uv run --project ../medre --no-sync medre dev test --project ../medre --editable chatmail=. chatmail -- -m "chatmail_sdk and not local_integration and not soak and not live and not hardware and not docker"
```

Adjust `../medre` to the core checkout location. Select native SDK, service,
soak and hardware tiers explicitly; a local default run is not RF qualification.
Forks and arbitrary local checkout locations remain supported through explicit
source catalogs and editable overrides. See the core
[development environment](https://github.com/jeremiah-k/medre/blob/main/docs/dev/environment.md)
and [installation sources](https://github.com/jeremiah-k/medre/blob/main/docs/ops/package-management.md).

## Change notes

Describe behavior, compatibility, validation and release impact in the PR.
Update relevant specifications and tests together. Routine changes do not
require numbered fragments; optional operator or coordinated-release notes use
descriptive filenames. Released notes are historical context, not pending work.

## Adapter maintenance

The setup-instructions seam contract behind the README's provisioning journey
is documented in
[adapter maintenance](docs/dev/adapter-maintenance.md).
