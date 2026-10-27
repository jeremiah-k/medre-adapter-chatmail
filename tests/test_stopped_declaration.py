"""Stopped Chatmail validation against Delta Chat core v2.65.0 registry layout.

All fixtures are disposable. No RPC process, credentials, or native profile is used.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from medre.adapters.chatmail.plugin import adapter_spec
from medre.adapters.chatmail.stopped_declaration import declaration
from medre.maintenance.stopped_declare import (
    ExclusionRefused,
    LocalExclusion,
    StoppedPlanError,
    capture_configured_stopped_state,
    declaration_from_spec,
    plan_from_coverages,
)
from medre.maintenance.stopped_snapshot import (
    restore_stopped_snapshot,
    verify_stopped_snapshot,
)

UUID_DIR = "c43fc7c4-94ab-4ba3-a871-72b53d43e76f"


def _db(path: Path, table: str = "kept") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE {table}(value TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES('committed')")


def _settings(root: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "accounts_dir": str(root / "accounts"),
        "state_dir": str(root),
        "account_id": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _registry(rows: str | None = None, *, selected: int = 7) -> bytes:
    account = rows or f'id = 7\ndir = "{UUID_DIR}"\nuuid = "{UUID_DIR}"'
    return (
        f"selected_account = {selected}\nnext_id = 8\naccounts_order = [7]\n"
        f"[[accounts]]\n{account}\n"
    ).encode()


def _profile(root: Path) -> SimpleNamespace:
    settings = _settings(root)
    accounts = Path(settings.accounts_dir)
    accounts.mkdir(parents=True)
    (accounts / "accounts.toml").write_bytes(_registry())
    (accounts / "accounts.lock").write_bytes(b"")
    (accounts / UUID_DIR / "dc.db-blobs").mkdir(parents=True)
    _db(accounts / UUID_DIR / "dc.db")
    journal = root / "chatmail-pending.sqlite"
    with closing(sqlite3.connect(journal)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE pending(identity TEXT PRIMARY KEY, provenance TEXT NOT NULL, "
            "chat INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, "
            "native INTEGER, sender_override TEXT)"
        )
        conn.executemany(
            "INSERT INTO pending VALUES (?, ?, 5, 'body', ?, NULL, NULL)",
            [("op-accepted", "{}", "accepted"), ("op-unknown", "{}", "unknown")],
        )
    _db(root / ".accounts.medre-lock.sqlite", "owner")
    return settings


def _resolved(settings: object):
    exclusion = declaration().local_exclusion(settings, adapter_id="relay")
    try:
        return declaration().resolve_coverage(settings, adapter_id="relay")
    finally:
        exclusion.close()


def test_spec_resolution_is_sdk_free():
    ref = adapter_spec().stopped_state_declaration
    assert ref is not None and ref.name == "declaration"
    assert declaration_from_spec(adapter_spec()).provider == "chatmail"


def test_unresolved_plan_refuses_numeric_directory_guess(tmp_path):
    settings = _profile(tmp_path / "state")
    preview = declaration().coverage(settings, adapter_id="relay")
    assert preview.state_class == "unsupported" and preview.files == ()
    with pytest.raises(StoppedPlanError):
        plan_from_coverages(tmp_path, [preview])


def test_registry_assigned_uuid_dc_db_and_journal(tmp_path):
    root = tmp_path / "state"
    settings = _profile(root)
    coverage = _resolved(settings)
    assert coverage.state_class == "file_backed"
    assert {p.path.name for p in coverage.files} == {
        "accounts.toml",
        "dc.db",
        "chatmail-pending.sqlite",
    }
    assert next(p.path for p in coverage.files if p.path.name == "dc.db") == (
        root / "accounts" / UUID_DIR / "dc.db"
    )
    assert all("db.sqlite" not in str(p.path) for p in coverage.files)
    assert all("lock" not in p.path.name for p in coverage.files)


@pytest.mark.parametrize(
    "payload", [b"broken [", b"x" * (1024 * 1024 + 1), b"[[accounts]]\nid = 7\n"]
)
def test_corrupt_oversize_incomplete_registry_refused(tmp_path, payload):
    settings = _profile(tmp_path / "state")
    (Path(settings.accounts_dir) / "accounts.toml").write_bytes(payload)
    assert _resolved(settings).state_class == "unsupported"


@pytest.mark.parametrize(
    "rows",
    [
        'id = 9\ndir = "' + UUID_DIR + '"\nuuid = "' + UUID_DIR + '"',
        'id = 7\ndir = "../escape"\nuuid = "' + UUID_DIR + '"',
        'id = 7\ndir = "/etc"\nuuid = "' + UUID_DIR + '"',
        'id = 7\ndir = "7"\nuuid = "' + UUID_DIR + '"',
        'id = 7\ndir = "' + UUID_DIR + '"\nuuid = "not-a-uuid"',
        'id = 7\ndir = "' + UUID_DIR + '"\nuuid = "' + UUID_DIR + '"\n'
        '[[accounts]]\nid = 7\ndir = "' + UUID_DIR + '"\nuuid = "' + UUID_DIR + '"',
    ],
)
def test_invalid_mapping_and_multiple_accounts_refused(tmp_path, rows):
    settings = _profile(tmp_path / "state")
    (Path(settings.accounts_dir) / "accounts.toml").write_bytes(_registry(rows))
    assert _resolved(settings).state_class == "unsupported"


def test_native_blobs_unknown_files_and_symlinks_refused(tmp_path):
    settings = _profile(tmp_path / "state")
    account = Path(settings.accounts_dir) / UUID_DIR
    blob = account / "dc.db-blobs" / "avatar.png"
    blob.write_bytes(b"image")
    assert _resolved(settings).state_class == "unsupported"
    blob.unlink()
    (account / "unknown").write_bytes(b"x")
    assert _resolved(settings).state_class == "unsupported"
    (account / "unknown").unlink()
    db = account / "dc.db"
    db.unlink()
    db.symlink_to(tmp_path / "outside")
    assert _resolved(settings).state_class == "unsupported"


@pytest.mark.parametrize(
    "relative",
    [
        "accounts/accounts.toml",
        "accounts/accounts.lock",
        f"accounts/{UUID_DIR}/dc.db",
        "chatmail-pending.sqlite",
        ".accounts.medre-lock.sqlite",
        f"accounts/{UUID_DIR}/dc.db-wal",
        f"accounts/{UUID_DIR}/dc.db-shm",
        "chatmail-pending.sqlite-wal",
        "chatmail-pending.sqlite-shm",
    ],
)
def test_native_hardlinks_outside_account_profile_refuse_capture(tmp_path, relative):
    """A hard link can alias external native state without traversing a symlink."""
    settings = _profile(tmp_path / "state")
    source = tmp_path / "state" / relative
    outside = tmp_path / "external-native-state"
    if not source.exists():
        source.write_bytes(b"")
    source.rename(outside)
    source.hardlink_to(outside)
    if relative in {"accounts/accounts.lock", ".accounts.medre-lock.sqlite"}:
        with pytest.raises(ExclusionRefused):
            _resolved(settings)
    else:
        assert _resolved(settings).state_class == "unsupported"
    source.unlink()
    outside.rename(source)
    declaration().local_exclusion(settings, adapter_id="relay").close()


def test_retain_both_native_and_medre_lease(tmp_path):
    settings = _profile(tmp_path / "state")
    held = declaration().local_exclusion(settings, adapter_id="relay")
    assert held.scope == "local_only"
    rival = LocalExclusion("chatmail", note="test")
    with pytest.raises(ExclusionRefused):
        rival.retain_flock(
            Path(settings.accounts_dir) / "accounts.lock", purpose="native"
        )
    with pytest.raises(ExclusionRefused):
        declaration().local_exclusion(settings, adapter_id="rival")
    held.close()
    reopened = declaration().local_exclusion(settings, adapter_id="relay")
    assert reopened.scope == "local_only"
    reopened.close()


def test_resolved_capture_restores_wal_commits_and_pending_handoff(
    tmp_path, monkeypatch
):
    root = tmp_path / "state"
    settings = _profile(root)
    _db(root / "medre.sqlite", "events")
    # Use the actual SDK-free spec loader and core resolution/capture path.
    monkeypatch.setattr(
        "medre.maintenance.stopped_declare._prepare_instances",
        lambda _config, _paths: iter(
            [("chatmail", "relay", "real", adapter_spec(), settings)]
        ),
    )
    config = SimpleNamespace(storage=SimpleNamespace(path=str(root / "medre.sqlite")))
    snapshot = tmp_path / "snapshot"
    plan = capture_configured_stopped_state(
        config,
        SimpleNamespace(),
        common_root=root,
        snapshot=snapshot,
        runtime_stopped=True,
    )
    assert {item.path for item in plan.entries} == {
        "medre.sqlite",
        "chatmail-pending.sqlite",
        "accounts/accounts.toml",
        f"accounts/{UUID_DIR}/dc.db",
    }
    verify_stopped_snapshot(snapshot)
    restored = tmp_path / "restored"
    restore_stopped_snapshot(snapshot, restored, runtime_stopped=True)
    for name, table in [
        ("medre.sqlite", "events"),
        (f"accounts/{UUID_DIR}/dc.db", "kept"),
    ]:
        with closing(sqlite3.connect(restored / name)) as con:
            assert con.execute(f"SELECT value FROM {table}").fetchone() == (
                "committed",
            )
    with closing(sqlite3.connect(restored / "chatmail-pending.sqlite")) as conn:
        assert dict(conn.execute("SELECT identity, status FROM pending")) == {
            "op-accepted": "accepted",
            "op-unknown": "unknown",
        }
    assert not (restored / "accounts/7/db.sqlite").exists()
    assert UUID(
        restored.joinpath("accounts/accounts.toml")
        .read_text()
        .split('uuid = "')[1]
        .split('"')[0]
    )


def test_stopped_assertion_and_blocked_owners_refuse_capture(tmp_path, monkeypatch):
    root = tmp_path / "state"
    settings = _profile(root)
    _db(root / "medre.sqlite", "events")
    monkeypatch.setattr(
        "medre.maintenance.stopped_declare._prepare_instances",
        lambda *_: iter([("chatmail", "relay", "real", adapter_spec(), settings)]),
    )
    config = SimpleNamespace(storage=SimpleNamespace(path=str(root / "medre.sqlite")))
    with pytest.raises(StoppedPlanError):
        capture_configured_stopped_state(
            config,
            SimpleNamespace(),
            common_root=root,
            snapshot=tmp_path / "snap",
            runtime_stopped=False,
        )
    held = declaration().local_exclusion(settings, adapter_id="relay")
    try:
        with pytest.raises(ExclusionRefused):
            capture_configured_stopped_state(
                config,
                SimpleNamespace(),
                common_root=root,
                snapshot=tmp_path / "snap",
                runtime_stopped=True,
            )
    finally:
        held.close()


@pytest.mark.parametrize(
    "replacement",
    ["accounts_order = [7, 9]", "next_id = 7", "next_id = '8'"],
)
def test_inconsistent_native_registry_metadata_refused(tmp_path, replacement):
    settings = _profile(tmp_path / "state")
    registry = Path(settings.accounts_dir) / "accounts.toml"
    content = _registry().decode()
    old = (
        "accounts_order = [7]"
        if replacement.startswith("accounts_order")
        else "next_id = 8"
    )
    registry.write_text(content.replace(old, replacement))
    assert _resolved(settings).state_class == "unsupported"


def test_legacy_numeric_directory_decoy_is_never_selected(tmp_path):
    settings = _profile(tmp_path / "state")
    _db(Path(settings.accounts_dir) / "7" / "db.sqlite")
    # A stray native directory must not disappear silently from an asserted
    # complete-installation capture, even when the correct UUID DB exists.
    assert _resolved(settings).state_class == "unsupported"


@pytest.mark.parametrize("field", ["selected_account", "accounts_order"])
def test_boolean_account_aliases_are_refused(tmp_path, field):
    settings = _profile(tmp_path / "state")
    settings.account_id = 1
    registry = Path(settings.accounts_dir) / "accounts.toml"
    text = (
        _registry()
        .decode()
        .replace("= 7", "= 1")
        .replace("[7]", "[1]")
        .replace("next_id = 8", "next_id = 2")
    )
    text = (
        text.replace("selected_account = 1", "selected_account = true")
        if field == "selected_account"
        else text.replace("accounts_order = [1]", "accounts_order = [true]")
    )
    registry.write_text(text)
    assert _resolved(settings).state_class == "unsupported"


def test_malformed_native_database_never_publishes_a_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "state"
    settings = _profile(root)
    _db(root / "medre.sqlite", "events")
    (root / "accounts" / UUID_DIR / "dc.db").write_bytes(b"opaque encrypted database")
    monkeypatch.setattr(
        "medre.maintenance.stopped_declare._prepare_instances",
        lambda *_: iter([("chatmail", "relay", "real", adapter_spec(), settings)]),
    )
    snapshot = tmp_path / "snapshot"
    config = SimpleNamespace(storage=SimpleNamespace(path=str(root / "medre.sqlite")))
    with pytest.raises(sqlite3.DatabaseError):
        capture_configured_stopped_state(
            config,
            SimpleNamespace(),
            common_root=root,
            snapshot=snapshot,
            runtime_stopped=True,
        )
    assert not snapshot.exists()
    declaration().local_exclusion(settings, adapter_id="relay").close()
