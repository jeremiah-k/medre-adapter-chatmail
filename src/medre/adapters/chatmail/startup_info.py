"""Shape the configured account number and installed RPC client version."""

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


def build_startup_info(config: ChatmailConfig) -> dict[str, Any]:
    """Build startup facts without reading native profile or session state."""
    account: list[dict[str, Any]] = [
        {"name": "account_id", "value": config.account_id, "label": "account"}
    ]
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
