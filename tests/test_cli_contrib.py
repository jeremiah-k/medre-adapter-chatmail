"""Provider management works through MEDRE without a separate exposed executable."""

import argparse
import subprocess
import sys

import pytest

from medre.adapters.chatmail import cli
from medre.adapters.chatmail.plugin import adapter_spec
from medre.cli import contrib


def core_parser(monkeypatch):
    """Register the actual provider through core's contribution isolation checks."""
    monkeypatch.setattr(contrib, "iter_adapter_specs", lambda: (adapter_spec(),))
    monkeypatch.setattr(contrib, "get_adapter_spec", lambda _: adapter_spec())
    result = argparse.ArgumentParser(prog="medre")
    failures = contrib.register_adapter_contributors(
        result.add_subparsers(dest="command")
    )
    assert not failures
    return result


def test_core_help_requires_no_sdk_or_native_profile(monkeypatch, capsys):
    """Provider registration and nested help remain available without native setup."""
    parser = core_parser(monkeypatch)
    monkeypatch.setattr(
        cli, "selected_config", lambda _: pytest.fail("profile opened for help")
    )
    with pytest.raises(SystemExit) as stopped:
        parser.parse_args(["adapter", "chatmail", "--help"])
    assert stopped.value.code == 0
    assert "chats" in capsys.readouterr().out


def test_standalone_and_core_arguments_match(monkeypatch):
    """Native command options retain their meaning under the core namespace."""
    argv = ["chats", "--adapter", "mail", "--groups", "--json"]
    standalone = cli.parser().parse_args(argv)
    nested = core_parser(monkeypatch).parse_args(["adapter", "chatmail", *argv])
    assert nested.command == "adapter"
    assert nested.adapter_command == "chatmail"
    assert nested.adapter_chatmail_command == standalone.command
    for key, value in vars(standalone).items():
        if key != "command":
            assert getattr(nested, key) == value


@pytest.mark.parametrize("code", [0, 1, 130])
def test_core_dispatch_preserves_status_and_namespace(monkeypatch, code):
    """Shared execution preserves failures and interrupts without rewriting core dispatch."""
    parsed = core_parser(monkeypatch).parse_args(
        ["adapter", "chatmail", "chats", "--json"]
    )
    captured = []

    def run(options):
        captured.append(options)
        return code

    monkeypatch.setattr(cli, "run", run)
    with pytest.raises(SystemExit) as stopped:
        contrib.dispatch_contribution(parsed)
    assert stopped.value.code == code
    assert captured[0].command == "chats"
    assert captured[0].json
    assert parsed.command == "adapter"


def test_registration_does_not_import_native_sdk():
    """Generic CLI help never loads the native library, RPC client or worker."""
    script = """
import importlib.abc
import sys
class BlockSdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'deltachat_rpc_client', 'deltachat_rpc_server', 'simplex_chat'}:
            raise AssertionError('SDK imported for help: ' + fullname)
sys.meta_path.insert(0, BlockSdk())
from medre.adapters.chatmail.cli_contrib import register_cli
import argparse
parser = argparse.ArgumentParser()
register_cli(parser)
parser.parse_args(['--help'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "chats" in result.stdout
