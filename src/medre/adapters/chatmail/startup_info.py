"""Startup-info facts from state the Chatmail adapter already holds.

Core's runtime duck-types an optional ``startup_info()`` callable on a
built adapter and renders the returned scalar facts at the adapter's
ready transition (see the adapter-startup-info specification in the
core distribution).  This module shapes the facts this adapter holds at
that transition — the configured account identity, the native profile
identity once a session has connected, and the installed
``deltachat-rpc-client`` distribution version — into the plain mapping
the contract expects.

Pure read: the provider performs no RPC or native I/O, acquires no
resources, and mutates no session state, so a synchronous callable is
safe to run on any worker thread.  The only lookup beyond held state is
the installed client distribution version via ``importlib.metadata`` —
local package metadata, never a source literal (the repository's
pin-hygiene guard keeps pin values in ``pyproject.toml`` only) — and
that fact is omitted when the package is absent.  The native RPC
server's version is knowable only through RPC traffic, so it is never
probed here; a fresh adapter omits it.  Facts that are not held yet are
omitted rather than probed: startup info must never gate, block, or
delay the native session.  This module imports nothing from
``medre.core``: the returned mapping is plain JSON-like data, validated
and redacted by core at the render boundary.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

from medre.adapters.chatmail.config import ChatmailConfig

__all__ = ["build_startup_info"]


def _installed_client_version() -> str | None:
    """Return the installed ``deltachat-rpc-client`` version, or ``None``.

    Reads local package metadata via ``importlib.metadata``; the value
    is never a source literal.  ``None`` means the package is not
    installed (fake-only environments) and the fact is omitted.
    """
    try:
        return importlib.metadata.version("deltachat-rpc-client")
    except importlib.metadata.PackageNotFoundError:
        return None


def build_startup_info(
    config: ChatmailConfig,
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Build the startup-info mapping from held state.

    ``account_id`` is the configured account identity the adapter
    selects inside the prepared profile.  ``profile`` is the native
    profile identity the session captured from the account's blob
    directory at connect time; it is omitted until a session holds one
    (fake mode never connects).  The configured account identity forms
    the first row and the installed client version the second, per the
    contract's row guidance.
    """
    account: list[dict[str, Any]] = [
        {"name": "account_id", "value": config.account_id, "label": "account"}
    ]
    if profile:
        account.append({"name": "profile", "value": profile, "label": "profile"})
    rows: list[list[dict[str, Any]]] = [account]
    client_version = _installed_client_version()
    if client_version:
        rows.append(
            [
                {
                    "name": "client_version",
                    "value": client_version,
                    "label": "client",
                }
            ]
        )
    return {"rows": rows}
