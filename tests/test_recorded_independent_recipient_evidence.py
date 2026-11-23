"""Check the independent-recipient tier record states only what ran."""

import json
import re
from pathlib import Path

EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/independent-recipient-tier-2026-11-23.json"
)
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _record() -> dict:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_record_names_immutable_revisions_and_exact_tier():
    record = _record()
    assert record["format"] == "medre-chatmail-independent-recipient-tier-record"
    assert record["version"] == 1
    assert record["tier_reached"] == "gate_logic_proven_and_optin_harness_delivered"
    for key in ("provider_commit", "core_commit"):
        assert COMMIT.fullmatch(record[key]), key
    assert COMMIT.fullmatch(record["relay_probe"]["image_revision"])


def test_record_does_not_claim_a_live_recipient_observation():
    record = _record()
    assert record["live_tier_executed"] is False
    assert record["relay_probe"]["containerized_relay_built"] is False
    assert record["relay_probe"]["host_settings_changed"] is False
    assert "No independent recipient observed a message" in record["notes"]


def test_record_rehearsals_are_refusals_and_gate_tests_are_counted():
    record = _record()
    assert record["default_tier_gate_tests_passed"] > 0
    assert record["refusal_rehearsals"], "at least one refusal rehearsal"
    assert all(
        rehearsal["outcome"] == "refused" for rehearsal in record["refusal_rehearsals"]
    )
    assert (
        record["campaign_environment"]["sdk_client"]
        == record["campaign_environment"]["sdk_server"]
    )
