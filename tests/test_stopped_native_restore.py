"""Offline native account layout, ownership, and restore qualification.

Run explicitly with the pinned Delta Chat RPC artifacts in a disposable Linux
environment. No credentials, server configuration, or transport I/O is used.
"""

from __future__ import annotations

import sqlite3
import sys
import tomllib
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.chatmail_sdk


@contextmanager
def _manager(client, accounts, binary):
    """Reap each isolated native process and close its caller-owned pipes."""
    rpc = client.Rpc(accounts_dir=str(accounts), rpc_server_path=binary)
    try:
        with rpc:
            yield rpc
    finally:
        if hasattr(rpc, "process"):
            rpc.process.wait(timeout=10)
            for stream in (rpc.process.stdin, rpc.process.stdout, rpc.process.stderr):
                if stream is not None:
                    stream.close()


def test_native_account_lock_capture_and_reopen(tmp_path, monkeypatch):
    """A real native manager excludes capture and reopens its stopped snapshot."""
    if sys.platform != "linux":
        pytest.skip("native lock campaign is qualified on Linux")
    client = pytest.importorskip("deltachat_rpc_client")
    server = pytest.importorskip("deltachat_rpc_server")

    from medre.adapters.chatmail.plugin import adapter_spec
    from medre.adapters.chatmail.state import Journal, ProfileLease
    from medre.adapters.chatmail.stopped_declaration import declaration
    from medre.maintenance import stopped_declare as maintenance
    from medre.maintenance.stopped_snapshot import (
        restore_stopped_snapshot,
        verify_stopped_snapshot,
    )

    root = tmp_path / "state"
    accounts = root / "accounts"
    binary = str(Path(server.__file__).with_name("deltachat-rpc-server"))
    native = declaration()
    with _manager(client, accounts, binary) as rpc:
        account_id = rpc.add_account()
        rpc.set_config(account_id, "displayname", "Stopped account fixture")
        settings = SimpleNamespace(
            accounts_dir=str(accounts), state_dir=str(root), account_id=account_id
        )
        ProfileLease(str(accounts)).close()
        with pytest.raises(maintenance.ExclusionRefused):
            native.local_exclusion(settings, adapter_id="mail")

    held = native.local_exclusion(settings, adapter_id="mail")
    try:
        with pytest.raises(client.JsonRpcError):
            with _manager(client, accounts, binary):
                pytest.fail(
                    "native manager started while maintenance retained its lock"
                )
    finally:
        held.close()
    registry = tomllib.loads((accounts / "accounts.toml").read_text())
    directory = registry["accounts"][0]["dir"]
    journal = Journal(str(root), directory)
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("PRAGMA journal_mode=WAL")
        core.execute("CREATE TABLE committed(value TEXT)")
        core.execute("INSERT INTO committed VALUES ('retained WAL')")
        core.commit()
        assert (root / "core.sqlite-wal").stat().st_size > 0
        with journal.connection:
            journal.connection.executemany(
                "INSERT INTO pending VALUES (?, '{}', 5, 'body', ?, NULL, NULL)",
                [("accepted-operation", "accepted"), ("unknown-operation", "unknown")],
            )
        monkeypatch.setattr(
            maintenance,
            "_prepare_instances",
            lambda *_: iter([("chatmail", "mail", "real", adapter_spec(), settings)]),
        )
        config = SimpleNamespace(
            storage=SimpleNamespace(path=str(root / "core.sqlite"))
        )
        snapshot = tmp_path / "snapshot"
        plan = maintenance.capture_configured_stopped_state(
            config,
            SimpleNamespace(),
            common_root=root,
            snapshot=snapshot,
            runtime_stopped=True,
        )
        assert f"accounts/{directory}/dc.db" in {entry.path for entry in plan.entries}
        verify_stopped_snapshot(snapshot)
        restored = tmp_path / "restored"
        restore_stopped_snapshot(snapshot, restored, runtime_stopped=True)
        with closing(sqlite3.connect(restored / "core.sqlite")) as connection:
            assert connection.execute("SELECT value FROM committed").fetchone() == (
                "retained WAL",
            )
        with closing(
            sqlite3.connect(restored / "chatmail-pending.sqlite")
        ) as connection:
            assert dict(connection.execute("SELECT identity, status FROM pending")) == {
                "accepted-operation": "accepted",
                "unknown-operation": "unknown",
            }
        restored_accounts = restored / "accounts"
        assert not (restored_accounts / directory / "dc.db-blobs").exists()
        with _manager(client, restored_accounts, binary) as rpc:
            assert rpc.get_all_account_ids() == [account_id]
            assert (
                rpc.get_config(account_id, "displayname") == "Stopped account fixture"
            )
        assert (restored_accounts / directory / "dc.db-blobs").is_dir()
        assert (
            tomllib.loads((restored_accounts / "accounts.toml").read_text())["accounts"]
            == registry["accounts"]
        )
        # The same real profile becomes unsupported when required native blobs
        # would otherwise be omitted from the selected snapshot.
        (accounts / directory / "dc.db-blobs" / "required-blob").write_bytes(b"fixture")
        refused = tmp_path / "incomplete-snapshot"
        with pytest.raises(maintenance.StoppedPlanError):
            maintenance.capture_configured_stopped_state(
                config,
                SimpleNamespace(),
                common_root=root,
                snapshot=refused,
                runtime_stopped=True,
            )
        assert not refused.exists()
        native.local_exclusion(settings, adapter_id="mail").close()
    finally:
        journal.close()
        core.close()
