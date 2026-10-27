"""Stopped Chatmail account selection from the native account-manager registry.

Delta Chat core v2.65.0, src/accounts.rs: AccountConfig stores id, dir,
and uuid; dbfile() uses the registry's dir and ``dc.db``. The generic
configuration-only declaration must not guess that mapping. Native metadata
may be inspected only in ``resolve_coverage`` under retained owner exclusion.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path
from uuid import UUID

from medre.maintenance.stopped_declare import (
    DeclaredFile,
    ExclusionRefused,
    LocalExclusion,
    ProviderCoverage,
    StoppedPlanError,
    StoppedStateDeclaration,
    declared,
)

_LOCK_INFIX = ".medre-lock.sqlite"
_MAX_REGISTRY_BYTES = 1024 * 1024


def _accounts_dir(config: object) -> Path:
    """Read the prepared account-manager path without accessing the profile."""
    value = getattr(config, "accounts_dir", "")
    return Path(value) if isinstance(value, str) and value else Path()


def _lease_path(accounts_dir: Path) -> Path:
    """Match the runtime profile lease beside the resolved accounts directory."""
    resolved = accounts_dir.resolve()
    return resolved.parent / f".{resolved.name}{_LOCK_INFIX}"


def _regular(path: Path, *, max_bytes: int = _MAX_REGISTRY_BYTES) -> bytes:
    """Read a bounded regular file without dereferencing the final component."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise StoppedPlanError(
                    "native account state is not a bounded regular file"
                )
            if info.st_nlink != 1:
                raise StoppedPlanError(
                    "hard-linked native account state requires export"
                )
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise StoppedPlanError("native account registry exceeds size limit")
            return data
    except (OSError, ValueError):
        raise StoppedPlanError("native account state is not safely readable") from None


def _single_link(path: Path) -> bool:
    """Refuse hard-linked or special source files, including SQLite sidecars."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        finally:
            os.close(descriptor)
    except OSError:
        return False


def _directory(path: Path) -> None:
    """Do not traverse native profile symlinks, including ancestor components."""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StoppedPlanError("native account paths cannot traverse symlinks")
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise StoppedPlanError("native account directory is not a directory")
    except OSError:
        raise StoppedPlanError("native account directory is unavailable") from None


def _entries(directory: Path, allowed: set[str]) -> dict[str, Path]:
    """Stop at the first unknown name instead of scanning an unbounded tree."""
    entries = {}
    for path in directory.iterdir():
        if path.name not in allowed:
            raise StoppedPlanError("native state requires a coherent provider export")
        entries[path.name] = path
    return entries


def _refused(adapter_id: str, reason: str) -> ProviderCoverage:
    """Report an incomplete native selection without emitting private paths."""
    return declared(
        provider="chatmail",
        adapter_id=adapter_id,
        files=(),
        state_class="unsupported",
        notes=(reason,),
    )


class ChatmailStoppedState:
    """A single, blob-empty native account with durable MEDRE handoff fencing."""

    provider = "chatmail"

    def coverage(self, config: object, *, adapter_id: str) -> ProviderCoverage:
        """SDK-free and filesystem-free preview; cannot select a guessed path."""
        if not _accounts_dir(config).is_absolute() or not getattr(
            config, "state_dir", ""
        ):
            return _refused(
                adapter_id, "absolute accounts_dir and state_dir are required"
            )
        return _refused(
            adapter_id,
            "native registry resolution requires retained exclusion and capture-config; "
            "a configuration-only plan cannot select the account database",
        )

    def resolve_coverage(self, config: object, *, adapter_id: str) -> ProviderCoverage:
        """Read native metadata ONLY while core retains the local exclusion.

        A multi-account registry or any nonempty/unknown native file tree needs
        a coherent Delta Chat export, not a synthesized partial restore.
        """
        accounts = _accounts_dir(config)
        state = Path(getattr(config, "state_dir", ""))
        account_id = getattr(config, "account_id", None)
        if (
            not accounts.is_absolute()
            or not state.is_absolute()
            or type(account_id) is not int
            or not 1 <= account_id <= 0xFFFFFFFF
        ):
            return _refused(
                adapter_id, "absolute directories and valid account ID required"
            )
        _directory(accounts)
        _directory(state)
        try:
            registry = tomllib.loads(
                _regular(accounts / "accounts.toml").decode("utf-8")
            )
        except (ValueError, UnicodeError, tomllib.TOMLDecodeError):
            return _refused(
                adapter_id, "native account registry is malformed or unsafe"
            )
        if not isinstance(registry, dict) or set(registry) - {
            "selected_account",
            "next_id",
            "accounts",
            "accounts_order",
        }:
            return _refused(adapter_id, "unrecognized native registry schema")
        if (
            type(registry.get("next_id")) is not int
            or not account_id < registry["next_id"] <= 0xFFFFFFFF
            or not isinstance(registry.get("accounts_order", [account_id]), list)
            or any(
                type(value) is not int
                for value in registry.get("accounts_order", [account_id])
            )
            or registry.get("accounts_order", [account_id]) != [account_id]
        ):
            return _refused(adapter_id, "native registry account order is inconsistent")
        rows = registry.get("accounts")
        if not isinstance(rows, list) or len(rows) != 1:
            return _refused(
                adapter_id,
                "multi-account or empty registry needs coherent native export",
            )
        row = rows[0]
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "dir", "uuid"}
            or type(row.get("id")) is not int
            or row["id"] != account_id
        ):
            return _refused(adapter_id, "account mapping missing or inconsistent")
        if (
            type(registry.get("selected_account")) is not int
            or registry["selected_account"] != account_id
        ):
            return _refused(adapter_id, "selected account disagrees with configuration")
        try:
            uuid = UUID(row["uuid"])
            directory = row["dir"]
            if not isinstance(directory, str) or directory != str(uuid):
                raise ValueError("noncanonical account directory")
        except (KeyError, TypeError, ValueError, AttributeError):
            return _refused(
                adapter_id, "native account directory/UUID mapping is unsafe"
            )
        try:
            entries = _entries(accounts, {"accounts.toml", "accounts.lock", directory})
        except (OSError, StoppedPlanError):
            return _refused(
                adapter_id, "native account manager state is not enumerable"
            )
        if set(entries) != {"accounts.toml", "accounts.lock", directory}:
            return _refused(
                adapter_id, "unrecognized native account manager state requires export"
            )
        if not _single_link(entries["accounts.lock"]):
            return _refused(
                adapter_id, "native account lock file is not a private regular inode"
            )
        account_dir = accounts / directory
        _directory(account_dir)
        db = account_dir / "dc.db"
        blobs = account_dir / "dc.db-blobs"
        _directory(blobs)
        try:
            # Refuse *every* unknown entry, including symlinks, subdirectories,
            # blobs, hot rollback journals, and native files we do not understand.
            contents = _entries(
                account_dir, {"dc.db", "dc.db-wal", "dc.db-shm", "dc.db-blobs"}
            )
            if set(contents) - {"dc.db", "dc.db-wal", "dc.db-shm", "dc.db-blobs"}:
                return _refused(
                    adapter_id, "unrecognized native account state requires export"
                )
            if any(True for _ in blobs.iterdir()):
                return _refused(
                    adapter_id, "nonempty blob state requires coherent native export"
                )
            for name, path in contents.items():
                if name != "dc.db-blobs" and not _single_link(path):
                    return _refused(
                        adapter_id, "native account state has an unsafe inode alias"
                    )
            if "dc.db" not in contents:
                return _refused(adapter_id, "native account database is missing")
            # Even an empty native blob directory is a valid omission only for
            # this narrowly qualified single-account, no-blob boundary.
            if not _single_link(accounts / "accounts.toml"):
                return _refused(
                    adapter_id, "native account registry has an unsafe inode alias"
                )
            ledger = state / "chatmail-pending.sqlite"
            sidecars = (
                ledger.with_name(ledger.name + suffix) for suffix in ("-wal", "-shm")
            )
            if not _single_link(ledger) or any(
                (sidecar.exists() or sidecar.is_symlink()) and not _single_link(sidecar)
                for sidecar in sidecars
            ):
                return _refused(
                    adapter_id, "native handoff journal has an unsafe inode alias"
                )
        except (OSError, StoppedPlanError):
            return _refused(
                adapter_id, "native account state cannot be enumerated safely"
            )
        return declared(
            provider=self.provider,
            adapter_id=adapter_id,
            files=(
                DeclaredFile(
                    accounts / "accounts.toml",
                    "native_identity",
                    "opaque",
                    purpose="native account registry",
                ),
                DeclaredFile(
                    db,
                    "native_identity",
                    "sqlite",
                    purpose="Delta Chat dc.db with committed WAL",
                ),
                DeclaredFile(
                    state / "chatmail-pending.sqlite",
                    "adapter_ledger",
                    "sqlite",
                    purpose="accepted/unknown handoff fence",
                ),
            ),
            notes=(
                "one validated registry-assigned UUID account; native dc.db-blobs is empty",
                "SQLite capture folds committed WAL; sidecars are not independently copied",
                "native accounts.lock and MEDRE profile lease are excluded as lock inodes",
                "local exclusion does not cover other machines or non-cooperating processes",
            ),
        )

    def local_exclusion(self, config: object, *, adapter_id: str) -> LocalExclusion:
        """Retain existing MEDRE and native locks without creating profile state."""
        if sys.platform.startswith("sunos"):
            raise ExclusionRefused(
                "native account-manager lock semantics are unsupported here"
            )
        accounts = _accounts_dir(config)
        if not accounts.is_absolute():
            raise ExclusionRefused("chatmail requires an absolute accounts directory")
        _directory(accounts)
        if not _single_link(_lease_path(accounts)) or not _single_link(
            accounts / "accounts.lock"
        ):
            raise ExclusionRefused(
                "native profile locks require unaliased regular inodes"
            )
        lease = LocalExclusion(self.provider, note="uninitialized")
        try:
            lease.retain_sqlite_exclusive(
                _lease_path(accounts), purpose="MEDRE profile lease"
            )
            # Delta Chat accounts.rs uses fd_lock on accounts.lock (Unix).
            # Never create or truncate it to simulate exclusion.
            lease.retain_flock(
                accounts / "accounts.lock", purpose="native account manager lock"
            )
        except BaseException:
            lease.close()
            raise
        lease.note = "local MEDRE lease and native account-manager flock; other hosts are not covered"
        return lease


def declaration() -> StoppedStateDeclaration:
    """Return SDK-free native metadata selection and local owner exclusion."""
    return ChatmailStoppedState()
