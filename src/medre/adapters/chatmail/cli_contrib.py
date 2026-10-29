"""SDK-free parser contribution for standalone and MEDRE-managed commands."""

from __future__ import annotations

import argparse


def configure_parser(result: argparse.ArgumentParser, *, command_dest: str) -> None:
    """Share arguments while preserving the caller's dispatch namespace."""
    result.description = (
        "Provision Chatmail profiles and discover MEDRE conversation IDs."
    )
    commands = result.add_subparsers(dest=command_dest, required=True)
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


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Configure the core-owned medre adapter chatmail namespace without native I/O."""
    configure_parser(parser, command_dest="adapter_chatmail_command")


def dispatch_cli(args: argparse.Namespace) -> bool:
    """Run the selected provider operation without altering core's parsed namespace."""
    if args.command != "adapter" or args.adapter_command != "chatmail":
        return False
    from .cli import run

    options = argparse.Namespace(**vars(args))
    options.command = args.adapter_chatmail_command
    raise SystemExit(run(options))
