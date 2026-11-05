"""The declared provisioning journey stays verbatim, ordered, and truthful.

The journey restates the README's staged provisioning with the provider's
own contributed commands. Cores that provide the typed setup-instructions
seam render it informationally through `medre config native`; older cores
construct the spec exactly as before, with discovery unchanged.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from medre import adapter_spec as registration
from medre.adapters.chatmail import cli, plugin
from medre.adapters.chatmail.plugin import adapter_spec
from medre.cli import contrib

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"

_SEAM_AVAILABLE = hasattr(registration, "NativeSetupInstructions") and hasattr(
    registration, "NativeSetupStep"
)

_requires_seam = pytest.mark.skipif(
    not _SEAM_AVAILABLE,
    reason="core does not provide the typed setup-instructions seam",
)

_SDK_MODULE_NAMES = ("deltachat_rpc_client", "deltachat_rpc_server")

_EXPECTED_COMMANDS = (
    "medre adapter chatmail init --adapter mail",
    "medre adapter chatmail accounts --adapter mail",
    "medre adapter chatmail join --adapter mail",
    "medre adapter chatmail chats --adapter mail --groups --json",
)


def _journey_texts():
    """Return the declared title, summary, and flattened step texts."""
    texts = [plugin._SETUP_TITLE, plugin._SETUP_SUMMARY]
    for command, purpose, evidence_note in plugin._SETUP_STEPS:
        texts.extend((command, purpose, evidence_note))
    return texts


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


def test_journey_declares_the_readme_stages_verbatim_and_in_order():
    """The four commands restate the staged README journey, ordered identically."""
    commands = [command for command, _, _ in plugin._SETUP_STEPS]
    assert commands == list(_EXPECTED_COMMANDS)

    readme = _README_PATH.read_text(encoding="utf-8")
    positions = [readme.index(command) for command in commands]
    assert positions == sorted(positions)


@pytest.mark.parametrize("expected_command", _EXPECTED_COMMANDS)
def test_declared_commands_parse_through_the_shipped_cli_contribution(
    monkeypatch, expected_command
):
    """Every declared command exists verbatim in the core-managed CLI surface."""
    argv = expected_command.split()[1:]
    parsed = core_parser(monkeypatch).parse_args(argv)
    assert parsed.command == "adapter"
    assert parsed.adapter_command == "chatmail"
    assert parsed.adapter_chatmail_command == argv[2]

    standalone = cli.parser().parse_args(expected_command.split()[3:])
    assert standalone.command == parsed.adapter_chatmail_command


def test_evidence_notes_state_true_evidence_levels():
    """Every note denies relay acceptance, delivery, reads, and send authority."""
    for _, purpose, evidence_note in plugin._SETUP_STEPS:
        assert purpose.strip() and evidence_note.strip()
        assert "relay acceptance" in evidence_note
        assert "delivery" in evidence_note
        assert "read evidence" in evidence_note
        assert "send authority" in evidence_note
        assert "proves relay acceptance" not in evidence_note
        assert "proves delivery" not in evidence_note
        assert "proves read" not in evidence_note

    assert "relay-side SMTP acceptance" in plugin._SETUP_SUMMARY
    assert "delivery feedback" in plugin._SETUP_SUMMARY
    assert "never proven" in plugin._SETUP_SUMMARY


def test_declaration_stays_deterministic_and_path_free():
    """No secrets, building-machine paths, URLs, or timestamps in the journey."""
    for text in _journey_texts():
        assert "/" not in text
        assert "\\" not in text
        assert "://" not in text


def test_provider_keeps_no_native_setup_form():
    """The form contract is untouched: instructions never add guided fields."""
    spec = adapter_spec()
    assert not getattr(spec, "native_setup_fields", ())
    assert spec.support_secret_fields == ("accounts_dir", "state_dir")
    assert spec.support_endpoint_fields == ("account_id", "chats")


@_requires_seam
def test_declared_journey_is_carried_by_the_spec():
    """The spec carries a typed, structurally valid journey on seam cores."""
    instructions = adapter_spec().native_setup_instructions
    assert isinstance(instructions, registration.NativeSetupInstructions)
    assert instructions.title == plugin._SETUP_TITLE
    assert instructions.summary == plugin._SETUP_SUMMARY
    assert isinstance(instructions.steps, tuple)
    assert len(instructions.steps) == len(plugin._SETUP_STEPS)
    for step, (command, purpose, evidence_note) in zip(
        instructions.steps, plugin._SETUP_STEPS, strict=True
    ):
        assert isinstance(step, registration.NativeSetupStep)
        assert (step.command, step.purpose, step.evidence_note) == (
            command,
            purpose,
            evidence_note,
        )


@_requires_seam
def test_journey_construction_is_deterministic():
    """Repeated construction produces an equal frozen declaration."""
    assert plugin._setup_instructions() == plugin._setup_instructions()


@_requires_seam
def test_core_renders_the_journey_informationally_offline(
    monkeypatch, tmp_path, capsys
):
    """`medre config native` prints the journey, runs nothing, writes nothing."""
    from medre.cli import native_commands

    if not hasattr(native_commands, "_render_setup_instructions"):
        pytest.skip("core does not provide journey rendering")

    monkeypatch.setattr(native_commands, "get_adapter_spec", lambda _: adapter_spec())
    output = tmp_path / "connection.yaml"
    native_commands._config_native(
        argparse.Namespace(
            transport="chatmail",
            adapter_id="mail",
            config=None,
            output=str(output),
        )
    )

    rendered = capsys.readouterr().out
    assert "informational_only" in rendered
    assert plugin._SETUP_TITLE in rendered
    for command, _, _ in plugin._SETUP_STEPS:
        assert command in rendered
    assert not output.exists()
    assert not any(tmp_path.iterdir())


def test_older_core_without_setup_symbols_still_discovers_provider(monkeypatch):
    """Cores without the seam symbols construct the spec exactly as before."""
    monkeypatch.delattr(registration, "NativeSetupInstructions", raising=False)
    monkeypatch.delattr(registration, "NativeSetupStep", raising=False)
    spec = adapter_spec()
    assert spec.transport == "chatmail"
    assert getattr(spec, "native_setup_instructions", None) is None
    assert spec.config.load().__name__ == "ChatmailConfig"


def test_legacy_spec_without_the_field_still_discovers_provider(monkeypatch):
    """A spec model without the field never receives the unsupported kwarg."""

    class LegacyAdapterSpec:
        def __init__(self, **fields):
            assert "native_setup_instructions" not in fields
            self.transport = fields["transport"]

    monkeypatch.setattr(plugin, "AdapterSpec", LegacyAdapterSpec)
    spec = plugin.adapter_spec()
    assert spec.transport == "chatmail"


def test_declaration_path_imports_no_native_sdk():
    """Building the spec and journey never imports the pinned RPC SDKs."""
    script = """
import importlib.abc
import sys
class BlockSdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'deltachat_rpc_client', 'deltachat_rpc_server'}:
            raise AssertionError('SDK imported on the declaration path: ' + fullname)
sys.meta_path.insert(0, BlockSdk())
from medre.adapters.chatmail.plugin import adapter_spec
spec = adapter_spec()
journey = getattr(spec, 'native_setup_instructions', None)
assert journey is None or journey.steps
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
