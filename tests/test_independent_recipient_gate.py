"""Refusal rules for the independent-recipient evidence gate, proven SDK-free.

A positive qualification verdict must be impossible while the independent
peer is disabled or not observing, the source revisions do not match, a
sibling destination receives the message, the ledger identity disagrees, a
receipt witnesses two cases, or an undeclared peer is attributed. These
tests exercise the pure gate; the opt-in live tier feeds it real facts.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/qualify_independent_recipient.py"
spec = importlib.util.spec_from_file_location("chatmail_recipient_gate", PATH)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

CORE = "b" * 40
PROVIDER = "a" * 40
OTHER = "c" * 40


def validate(record, *, core=CORE, provider=PROVIDER):
    return gate.validate(
        record, expected_core_commit=core, expected_provider_commit=provider
    )


def qualified_record():
    record = gate.template()
    record.update(
        provider_commit=PROVIDER,
        core_commit=CORE,
        relay_kind="preconfigured-relay",
        sender_profile_tag="sender-bot",
        recipient_profile_tag="recipient-peer",
        sibling_profile_tag="sibling-observer",
    )
    cases = record["cases"]
    for index, name in enumerate(gate.OBSERVATION_CASES):
        cases[name].update(
            queue_acceptance=True,
            relay_acceptance=True,
            recipient_observed=True,
            passed=True,
            source_event_ref=f"source-event-{index}",
            outbox_ref=f"outbox-{index}",
            receipt_native_ref=f"receipt-{index}",
            evidence_ref=f"local-redacted-observation/{name}.json",
        )
    cases[gate.EXCLUSION_CASE].update(
        passed=True,
        evidence_ref="local-redacted-observation/sibling-window.json",
    )
    return record


def test_template_is_deliberately_unqualified():
    errors = validate(gate.template())
    assert errors
    assert any("recipient-side pass" in error for error in errors)


def test_structurally_complete_external_record():
    assert validate(qualified_record()) == ()


@pytest.mark.parametrize("missing", gate.OBSERVATION_CASES)
def test_peer_not_observing_cannot_qualify(missing):
    """A disabled or silent independent peer must never yield a positive."""
    record = qualified_record()
    record["cases"][missing]["recipient_observed"] = False
    errors = validate(record)
    assert any("recipient-side pass" in error for error in errors)


@pytest.mark.parametrize("missing", gate.OBSERVATION_CASES)
def test_recipient_claim_without_relay_acceptance_refused(missing):
    """No recipient claim may rest on a local SDK queue return."""
    record = qualified_record()
    record["cases"][missing]["relay_acceptance"] = False
    errors = validate(record)
    assert any("without a relay acceptance fact" in error for error in errors)


@pytest.mark.parametrize("name", gate.OBSERVATION_CASES)
def test_relay_claim_without_queue_acceptance_refused(name):
    record = qualified_record()
    record["cases"][name]["queue_acceptance"] = False
    errors = validate(record)
    assert any("without a local queue acceptance fact" in error for error in errors)


def test_partial_fact_chain_blocks_qualification():
    record = qualified_record()
    case = record["cases"]["group_delivery_observed"]
    case.update(
        queue_acceptance=True,
        relay_acceptance=False,
        recipient_observed=False,
    )
    errors = validate(record)
    assert any("recipient-side pass" in error for error in errors)


def test_stale_core_revision_refused():
    errors = validate(qualified_record(), core=OTHER)
    assert any("Core revision does not match" in error for error in errors)


def test_stale_provider_revision_refused():
    errors = validate(qualified_record(), provider=OTHER)
    assert any("Provider revision does not match" in error for error in errors)


@pytest.mark.parametrize("field", ["core_commit", "provider_commit"])
@pytest.mark.parametrize(
    "value", ["", "0" * 40, "not-a-commit", "A" * 40, "a" * 39, "a" * 41]
)
def test_malformed_record_revisions_refused(field, value):
    record = qualified_record()
    record[field] = value
    errors = validate(record)
    assert any(f"{field} must identify" in error for error in errors)


@pytest.mark.parametrize("field", ["core", "provider"])
def test_zero_expected_revision_refuses_everything(field):
    """A null qualification target must refuse before reading the record."""
    record = qualified_record()
    kwargs = {"core": CORE, "provider": PROVIDER}
    kwargs[field] = "0" * 40
    errors = validate(record, **kwargs)
    assert errors == (
        "Expected revisions must be non-null immutable 40-character commits",
    )


def _pinned_version(name):
    return gate._pinned_sdks()[name]


@pytest.mark.parametrize(
    "field,distribution",
    [
        ("sdk_client_version", "deltachat-rpc-client"),
        ("sdk_server_version", "deltachat-rpc-server"),
    ],
)
def test_stale_sdk_version_refused(field, distribution):
    record = qualified_record()
    record[field] = "0.0.0-not-the-pin"
    errors = validate(record)
    assert any("SDK" in error and "pinned contract" in error for error in errors)


@pytest.mark.parametrize("field", ["sdk_client_version", "sdk_server_version"])
def test_wrong_sdk_pair_values_refused(field):
    record = qualified_record()
    record[field] = "9.9.9"
    assert validate(record)


def test_sibling_destination_receiving_the_message_refused():
    record = qualified_record()
    record["cases"][gate.EXCLUSION_CASE]["recipient_observed"] = True
    errors = validate(record)
    assert any("sibling destination observed the message" in error for error in errors)


def test_sibling_acceptance_claims_refused():
    record = qualified_record()
    record["cases"][gate.EXCLUSION_CASE].update(
        queue_acceptance=True, relay_acceptance=True
    )
    errors = validate(record)
    assert any(
        "sibling case must not claim acceptance facts" in error for error in errors
    )


def test_sibling_case_without_witness_refused():
    record = qualified_record()
    record["cases"][gate.EXCLUSION_CASE]["evidence_ref"] = ""
    errors = validate(record)
    assert any("exclusion witness reference" in error for error in errors)


def test_unpassed_sibling_case_refused():
    record = qualified_record()
    record["cases"][gate.EXCLUSION_CASE]["passed"] = False
    errors = validate(record)
    assert any("sibling exclusion was not passed" in error for error in errors)


@pytest.mark.parametrize(
    "field",
    ["source_event_ref", "outbox_ref", "receipt_native_ref", "evidence_ref"],
)
def test_missing_correlation_identity_refused(field):
    """A foreign or absent ledger identity cannot qualify an observation."""
    record = qualified_record()
    record["cases"]["group_delivery_observed"][field] = ""
    errors = validate(record)
    assert any(
        f"group_delivery_observed: {field} must correlate" in error for error in errors
    )


def test_duplicate_receipt_cannot_witness_two_cases():
    record = qualified_record()
    record["cases"]["native_quote_observed"]["receipt_native_ref"] = record["cases"][
        "group_delivery_observed"
    ]["receipt_native_ref"]
    errors = validate(record)
    assert any("duplicate observation" in error for error in errors)


def test_outbox_identity_reuse_refused():
    record = qualified_record()
    record["cases"]["restart_continuation_observed"]["outbox_ref"] = record["cases"][
        "group_delivery_observed"
    ]["outbox_ref"]
    errors = validate(record)
    assert any("exactly one routed attempt" in error for error in errors)


@pytest.mark.parametrize(
    "left,right",
    [
        ("recipient_profile_tag", "sender_profile_tag"),
        ("sibling_profile_tag", "sender_profile_tag"),
        ("sibling_profile_tag", "recipient_profile_tag"),
    ],
)
def test_undeclared_or_shared_peer_tags_refused(left, right):
    record = qualified_record()
    record[left] = record[right]
    errors = validate(record)
    assert any("Three distinct bounded profile tags" in error for error in errors)


@pytest.mark.parametrize(
    "field",
    [
        "sender_profile_tag",
        "recipient_profile_tag",
        "sibling_profile_tag",
        "relay_kind",
    ],
)
@pytest.mark.parametrize("value", ["", "  padded  ", "x" * 129, "bad\x07tag"])
def test_unbounded_or_unprintable_declared_context_refused(field, value):
    record = qualified_record()
    record[field] = value
    errors = validate(record)
    assert errors


def test_undeclared_fields_refused():
    record = qualified_record()
    record["declared_peer"] = "extra"
    assert any("exactly the supported fields" in error for error in validate(record))


def test_missing_fields_refused():
    record = qualified_record()
    del record["relay_kind"]
    assert any("exactly the supported fields" in error for error in validate(record))


def test_unknown_case_name_refused():
    record = qualified_record()
    cases = dict(record["cases"])
    del cases["native_quote_observed"]
    record["cases"] = {
        **cases,
        "unknown_peer_observed": cases["group_delivery_observed"],
    }
    errors = validate(record)
    assert any("All four required" in error for error in errors)


def test_non_object_record_refused():
    assert validate([qualified_record()]) == ("Evidence must be one object",)


@pytest.mark.parametrize(
    "fact",
    [
        "queue_acceptance",
        "relay_acceptance",
        "recipient_observed",
        "passed",
    ],
)
def test_non_boolean_facts_refused(fact):
    record = qualified_record()
    record["cases"]["group_delivery_observed"][fact] = 1
    errors = validate(record)
    assert any("must be a boolean" in error for error in errors)


def test_cli_template_prints_unqualified_record(monkeypatch, capsys):
    monkeypatch.setattr(
        gate.importlib.metadata,
        "version",
        lambda name: _pinned_version(name),
    )
    assert gate.main(["--template"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["format"] == "medre-chatmail-independent-recipient-evidence"
    assert validate(record)


def test_cli_validate_requires_revisions():
    record = qualified_record()
    with_unspecified = subprocess.run(
        [sys.executable, str(PATH), "--validate", "-"],
        capture_output=True,
        text=True,
        input=json.dumps(record),
    )
    assert with_unspecified.returncode != 0


def test_cli_validate_passes_qualified_record(tmp_path):
    path = tmp_path / "qualified.json"
    path.write_text(json.dumps(qualified_record()), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(PATH),
            "--validate",
            str(path),
            "--core-commit",
            CORE,
            "--provider-commit",
            PROVIDER,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "authenticity not verified" in result.stdout


@pytest.mark.parametrize("broken", ["{}", "not json", "", "[1]"])
def test_cli_validate_fails_closed_on_unusable_records(tmp_path, broken):
    path = tmp_path / "broken.json"
    path.write_text(broken, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(PATH),
            "--validate",
            str(path),
            "--core-commit",
            CORE,
            "--provider-commit",
            PROVIDER,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_cli_validate_refuses_duplicate_json_fields(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"format": "x", "format": "y"}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(PATH),
            "--validate",
            str(path),
            "--core-commit",
            CORE,
            "--provider-commit",
            PROVIDER,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_cli_check_sdk_succeeds_only_at_declared_pins(monkeypatch, capsys):
    monkeypatch.setattr(
        gate.importlib.metadata,
        "version",
        lambda name: _pinned_version(name),
    )
    assert gate.main(["--check-sdk"]) == 0
    assert "match the declared pins" in capsys.readouterr().out

    def refuse(_name):
        raise importlib.metadata.PackageNotFoundError("simulated absence")

    monkeypatch.setattr(gate.importlib.metadata, "version", refuse)
    assert gate.main(["--check-sdk"]) == 1
    assert "Unqualified" in capsys.readouterr().err
