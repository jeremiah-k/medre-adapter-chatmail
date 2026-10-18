"""Explicit profile provisioning and chat discovery through the native RPC API."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from medre.config.paths import resolve

from .config import ChatmailConfig, prepare_config
from .session import ChatmailSession, SessionError


class UsageError(ValueError):
    """An operator error with an adapter-authored, safe explanation."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="medre-chatmail",
        description="Provision Chatmail profiles and discover MEDRE conversation IDs.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Create or configure a private Chatmail profile"),
        ("accounts", "List local account IDs without exposing credentials"),
        ("chats", "List chat IDs and names for route configuration"),
        ("join", "Accept a contact or group invitation"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--adapter",
            default="chatmail",
            help="MEDRE adapter instance ID (default: chatmail)",
        )
        command.add_argument(
            "--config", help="Read profile settings from this MEDRE configuration"
        )
        command.add_argument(
            "--accounts-dir", help="Override the private profile directory"
        )
        command.add_argument(
            "--account-id", type=int, help="Select an explicit native account ID"
        )
        command.add_argument(
            "--json", action="store_true", help="Print machine-readable results"
        )
        if name in {"init", "join"}:
            command.add_argument(
                "--qr-stdin",
                action="store_true",
                help="Read the private QR payload from standard input",
            )
        if name == "chats":
            command.add_argument(
                "--groups", action="store_true", help="Show group conversations only"
            )
    return result


def selected_config(options: argparse.Namespace) -> ChatmailConfig:
    paths = resolve()
    from medre.config.env import apply_env_overrides
    from medre.config.loader import load_config, parse_runtime_config

    if options.config:
        loaded, _, paths = load_config(options.config)
    else:
        loaded = parse_runtime_config(
            {
                "adapters": {
                    "chatmail": {options.adapter: {"rpc_timeout_seconds": 120.0}}
                },
                "routes": {},
            },
            paths,
        )
    loaded = apply_env_overrides(loaded)
    item = loaded.adapters.for_transport("chatmail").get(options.adapter)
    if item is None:
        raise UsageError(
            "The selected Chatmail adapter is absent from the configuration"
        )
    config = item.config
    config = replace(
        config,
        accounts_dir=(
            options.accounts_dir
            if options.accounts_dir is not None
            else config.accounts_dir
        ),
        account_id=(
            options.account_id if options.account_id is not None else config.account_id
        ),
    )
    try:
        return prepare_config(
            config, adapter_id=options.adapter, paths=paths, routes=[]
        )
    except ValueError as exc:
        raise UsageError(str(exc)) from None


def read_qr(options: argparse.Namespace) -> str:
    if options.qr_stdin:
        value = sys.stdin.readline().strip()
    else:
        if not sys.stdin.isatty():
            raise UsageError("Use --qr-stdin for noninteractive profile setup")
        value = getpass.getpass("Private Chatmail QR payload: ").strip()
    if not value:
        raise UsageError("A QR payload is required")
    return value


def create_profile_directory(path: str) -> None:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and directory.stat().st_mode & 0o077:
        raise UsageError(
            "Chatmail profile directory must grant access only to its owner"
        )


async def execute(
    options: argparse.Namespace,
    config: ChatmailConfig,
    *,
    session_factory=ChatmailSession,
) -> object:
    qr = None
    if options.command == "join":
        qr = read_qr(options)
        if not qr.upper().startswith("OPENPGP4FPR:") and not qr.lower().startswith(
            ("https://i.delta.chat/#", "https://i.delta.chat#")
        ):
            raise UsageError(
                "Joining requires a contact or group invitation QR payload"
            )
    if options.command == "init":
        await asyncio.to_thread(create_profile_directory, config.accounts_dir)
    session = session_factory(config)
    try:
        await session.start()
        accounts = await session.call("get_all_account_ids")
        if options.command == "accounts":
            return [
                {
                    "account_id": account,
                    "configured": await session.call("is_configured", account),
                }
                for account in accounts
            ]
        account = config.account_id
        if options.command == "init" and not accounts:
            created = await session.call("add_account")
            if created != account:
                raise UsageError(
                    "The requested account ID does not match the created profile"
                )
            accounts = [created]
        if account not in accounts:
            raise UsageError(
                "The selected account does not exist; inspect medre-chatmail accounts"
            )
        if options.command == "init":
            if not await session.call("is_configured", account):
                # Stop the native owner before a person may block on input.
                await session.stop()
                qr = read_qr(options)
                if not qr.upper().startswith(("DCACCOUNT:", "DCLOGIN:")):
                    raise UsageError(
                        "Profile setup requires a DCACCOUNT or DCLOGIN QR payload"
                    )
                session = session_factory(config)
                await session.start()
                if not await session.call("is_configured", account):
                    await session.call("add_transport_from_qr", account, qr)
            return {
                "account_id": account,
                "configured": await session.call("is_configured", account),
            }
        if not await session.call("is_configured", account):
            raise UsageError("The account is unconfigured; run medre-chatmail init")
        if options.command == "join":
            await session.call("start_io", account)
            conversation = await session.call("secure_join", account, qr)
            try:
                async with asyncio.timeout(config.rpc_timeout_seconds):
                    while not await session.call("can_send", account, conversation):
                        session.wake.clear()
                        try:
                            await asyncio.wait_for(session.wake.wait(), 1.0)
                        except TimeoutError:  # nosec B110 - bounded handshake poll
                            pass
            except TimeoutError:
                raise UsageError(
                    f"Chatmail join is pending for chat {conversation}; "
                    "check medre-chatmail chats before retrying"
                ) from None
            return {"chat_id": conversation, "can_send": True}
        entries = await session.call("get_chatlist_entries", account, None, None, None)
        chats = []
        for conversation in entries:
            if conversation <= 9:
                continue
            info = await session.call("get_basic_chat_info", account, conversation)
            if info.get("isDeviceChat") or info.get("isSelfTalk"):
                continue
            kind = info.get("chatType", "Unknown")
            if options.groups and kind != "Group":
                continue
            chats.append(
                {
                    "chat_id": conversation,
                    "kind": kind,
                    "name": info.get("name", ""),
                    "accepted": not info.get("isContactRequest", True),
                    "encrypted": info.get("isEncrypted", False),
                }
            )
        chats.sort(key=lambda item: item["kind"] != "Group")
        return chats
    finally:
        await session.stop()


def _display(value: object) -> str:
    return "".join(ch if ch.isprintable() else f"\\u{ord(ch):04x}" for ch in str(value))


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    try:
        result = asyncio.run(execute(options, selected_config(options)))
    except KeyboardInterrupt:
        return 130
    except (UsageError, SessionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Chatmail command failed; check the profile and relay configuration",
            file=sys.stderr,
        )
        return 1
    if options.json:
        print(json.dumps(result, ensure_ascii=True))
    elif isinstance(result, list):
        if not result:
            print("No entries found.")
        for entry in result:
            print(
                "  ".join(
                    f"{_display(key)}={_display(value)}" for key, value in entry.items()
                )
            )
    else:
        print(
            "  ".join(
                f"{_display(key)}={_display(value)}" for key, value in result.items()
            )
        )
        if options.command == "init":
            print(
                "Discover conversation IDs with medre-chatmail chats using the same profile options."
            )
    return 0
