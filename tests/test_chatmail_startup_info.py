"""Verify the Chatmail startup boundary around account and SDK facts."""

from __future__ import annotations

import ast
import importlib.metadata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from medre.core.startup_info import (
    collect_startup_info,
    render_startup_info_block,
    startup_info_callable,
)

from medre.adapters.chatmail import startup_info as startup_info_module
from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.fake import FakeChatmailAdapter

_EMITTED_AT = "2026-11-19T00:00:00+00:00"

# Synthetic native profile identity, the blob-directory parent name the
# connect path captures; the conftest NativeSession reports the same
# shape.  Not a secret: it scopes canonical IDs and journals.
_SYNTHETIC_PROFILE = "profile-uuid"

# Synthetic client version for deterministically exercising the held
# version fact.  Not a pin value; the repo pins live in pyproject only.
_SYNTHETIC_CLIENT_VERSION = "1.2.3"


# -- Helpers ---------------------------------------------------------------


def _make_config(**overrides: Any) -> ChatmailConfig:
    defaults: dict[str, Any] = {"chats": (10,), "relay_prefix": ""}
    defaults.update(overrides)
    return ChatmailConfig("mail", **defaults)


def _account_row() -> list[dict[str, Any]]:
    return [{"name": "account_id", "value": 1, "label": "account"}]


def _installed_client_version() -> str | None:
    """The real environment's answer, computed dynamically (no literals)."""
    try:
        return importlib.metadata.version("deltachat-rpc-client")
    except importlib.metadata.PackageNotFoundError:
        return None


def _expected_rows() -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = [_account_row()]
    client_version = _installed_client_version()
    if client_version is not None:
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


# -- Duck-typed discovery ---------------------------------------------------


def test_startup_info_symbol_is_discoverable() -> None:
    """Core's getattr discovery finds a callable on the adapter object."""
    adapter = ChatmailAdapter(_make_config())
    discovered = startup_info_callable(adapter)
    assert discovered is not None
    assert callable(discovered)
    assert discovered.__name__ == "startup_info"


def test_fake_adapter_exposes_discoverable_startup_info() -> None:
    """The fake mirrors the real adapter's duck-typed seam."""
    fake = FakeChatmailAdapter("mail", _make_config())
    discovered = startup_info_callable(fake)
    assert discovered is not None
    assert callable(discovered)
    assert discovered.__name__ == "startup_info"


# -- Held-fact formatting ---------------------------------------------------


def test_fresh_adapter_omits_unheld_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh adapter reports only the configured account identity.

    No session has connected, so no native profile is held, and the
    client version is stubbed out to pin the omission path — the
    environment's own answer is exercised separately without literals.
    """
    monkeypatch.setattr(startup_info_module, "_installed_client_version", lambda: None)
    adapter = ChatmailAdapter(_make_config())
    assert adapter.startup_info() == {"rows": [_account_row()]}


async def test_connected_session_keeps_the_native_profile_out_of_startup(
    config, context
) -> None:
    """The recovery profile is retained internally and absent from display facts."""
    from tests.conftest import NativeSession

    ctx, _ = context
    session = NativeSession(config)
    adapter = ChatmailAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        assert adapter._profile == _SYNTHETIC_PROFILE
        assert adapter.startup_info() == _expected_rows()
    finally:
        await adapter.stop()


def test_client_version_fact_follows_installed_metadata() -> None:
    """The version fact tracks ``importlib.metadata`` exactly.

    When the client package is installed the fact equals the queried
    metadata value; when it is absent the fact is omitted.  Either way
    the comparison is against a computed value, never a literal pin.
    """
    installed = _installed_client_version()
    adapter = ChatmailAdapter(_make_config())
    rows = adapter.startup_info()["rows"]
    names = [fact["name"] for row in rows for fact in row]
    if installed is None:
        assert "client_version" not in names
    else:
        values = [
            fact["value"]
            for row in rows
            for fact in row
            if fact["name"] == "client_version"
        ]
        assert values == [installed]


def test_server_version_is_never_probed() -> None:
    """The native RPC server's version is only knowable via RPC traffic.

    Startup info is pure read, so no server-version fact may appear
    from a fresh adapter.
    """
    adapter = ChatmailAdapter(_make_config())
    rows = adapter.startup_info()["rows"]
    names = [fact["name"] for row in rows for fact in row]
    assert not any(
        name.startswith("server") or "server_version" in name for name in names
    )


def test_fake_adapter_reports_configured_facts_only() -> None:
    """The fake never connects, so no native profile is ever reported."""
    fake = FakeChatmailAdapter("mail", _make_config())
    assert fake.startup_info() == _expected_rows()


# -- Purity and thread safety -----------------------------------------------


def test_startup_info_is_pure_read() -> None:
    """Reading facts neither mutates held state nor drifts between calls."""
    adapter = ChatmailAdapter(_make_config())
    adapter._profile = _SYNTHETIC_PROFILE
    before = adapter.diagnostics()
    first = adapter.startup_info()
    second = adapter.startup_info()
    assert first == second
    assert adapter.diagnostics() == before
    assert adapter._profile == _SYNTHETIC_PROFILE


def test_startup_info_snapshot_is_safe_from_worker_threads() -> None:
    """Core runs synchronous providers off-loop; the read must be stable."""
    adapter = ChatmailAdapter(_make_config())
    adapter._profile = _SYNTHETIC_PROFILE
    with ThreadPoolExecutor(max_workers=4) as pool:
        payloads = list(pool.map(lambda _: adapter.startup_info(), range(32)))
    assert all(payload == payloads[0] for payload in payloads)
    assert payloads[0] == _expected_rows()


def test_provider_module_imports_nothing_from_core() -> None:
    """The provider stays duck-typed: plain data, no medre.core coupling."""
    source = Path(startup_info_module.__file__ or "").read_text(encoding="utf-8")
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name
                for alias in node.names
                if alias.name == "medre.core" or alias.name.startswith("medre.core.")
            )
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if module == "medre.core" or module.startswith("medre.core."):
                offenders.append(module)
    assert offenders == []


def test_adapter_and_fake_import_the_shared_builder() -> None:
    """Both surfaces shape facts through the same pure module."""
    from medre.adapters.chatmail.fake import build_startup_info as _fake_build

    assert _fake_build is startup_info_module.build_startup_info


# ---------------------------------------------------------------------------
# Core boundary: collection, rendering
# ---------------------------------------------------------------------------


async def test_adapter_emits_held_facts_through_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core renders the account without the internal profile identifier."""
    monkeypatch.setattr(startup_info_module, "_installed_client_version", lambda: None)
    adapter = ChatmailAdapter(_make_config())
    adapter._profile = _SYNTHETIC_PROFILE
    info = await collect_startup_info(
        "mail-1", "chatmail", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [(f.name, f.row, f.label) for f in info.fields] == [
        ("account_id", 0, "account"),
    ]
    assert info.fields[0].value == "1"  # core renders scalars as display strings
    assert render_startup_info_block("chatmail.mail-1  ready", info) == (
        "chatmail.mail-1  ready\n" "  account 1"
    )


async def test_core_collection_keeps_version_fact_when_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held client version renders on its own row through core."""
    monkeypatch.setattr(
        startup_info_module,
        "_installed_client_version",
        lambda: _SYNTHETIC_CLIENT_VERSION,
    )
    adapter = ChatmailAdapter(_make_config())
    info = await collect_startup_info(
        "mail-1", "chatmail", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [(f.name, f.row, f.label) for f in info.fields] == [
        ("account_id", 0, "account"),
        ("client_version", 1, "client"),
    ]
    assert info.fields[1].value == _SYNTHETIC_CLIENT_VERSION
    assert render_startup_info_block("chatmail.mail-1  ready", info) == (
        "chatmail.mail-1  ready\n" "  account 1\n" "  client 1.2.3"
    )


async def test_core_collection_skips_adapters_without_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held-less fresh adapter still yields its configured identity.

    Even with the version omitted, the configured account identity is
    always held, so emission is never empty for a validated config.
    """
    monkeypatch.setattr(startup_info_module, "_installed_client_version", lambda: None)
    adapter = ChatmailAdapter(_make_config())
    info = await collect_startup_info(
        "mail-1", "chatmail", adapter, emitted_at=_EMITTED_AT
    )
    assert info is not None
    assert [f.name for f in info.fields] == ["account_id"]
