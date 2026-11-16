"""Check archival receipt consistency without qualifying an actual campaign."""

import hashlib
import json
import re
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/evidence"
AUDIT = "native-replies-rpc-2.69.0-2026-11-15-source-audit.json"


def test_source_audit_preserves_transcription_and_reconciles_campaign_record():
    audit = json.loads((EVIDENCE / AUDIT).read_text(encoding="utf-8"))
    raw = (EVIDENCE / audit["record_file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == audit["record_sha256"]
    record = json.loads(raw)
    transcription_raw = (EVIDENCE / audit["transcribed_record_file"]).read_bytes()
    assert (
        hashlib.sha256(transcription_raw).hexdigest()
        == audit["transcribed_record_sha256"]
    )
    transcription = json.loads(transcription_raw)
    assert all(
        record[key] == transcription[key] for key in record if key != "source_sha256"
    )
    for key in ("provider_revision", "core_revision"):
        assert record[key] == audit[key]
        assert re.fullmatch(r"[0-9a-f]{40}", audit[key])
    computed = audit["source_sha256_at_named_revision"]
    assert record["source_sha256"] == computed
    reported = transcription["source_sha256"]
    assert set(computed) == set(reported)
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in computed.values())
    assert set(audit["mismatched_files"]) == {
        path for path, value in computed.items() if reported[path] != value
    }
    # Reconciliation uses retained campaign records, not hash-edit similarity.
    assert (
        audit["campaign_source_verification"]
        == "reconciled_with_retained_campaign_record"
    )
    assert set(audit["retained_campaign_artifacts_sha256"]) == {
        "native-replies-2026-11-15.json",
        "proposed-checked-in-evidence.json",
        "peer-snapshot.json",
        "junit.xml",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in audit["retained_campaign_artifacts_sha256"].values()
    )
    assert len(audit["mismatched_files"]) == 3


def test_mismatched_digests_are_single_slip_corruptions():
    audit = json.loads((EVIDENCE / AUDIT).read_text(encoding="utf-8"))
    record = json.loads(
        (EVIDENCE / audit["transcribed_record_file"]).read_text(encoding="utf-8")
    )
    for path in audit["mismatched_files"]:
        reported = record["source_sha256"][path]
        computed = audit["source_sha256_at_named_revision"][path]
        if len(reported) == len(computed) - 1:
            # Exactly one deleted character at some offset.
            deletions = [
                offset
                for offset in range(len(computed))
                if computed[:offset] + computed[offset + 1 :] == reported
            ]
            assert len(deletions) == 1, path
            continue
        # Equal lengths: one substituted character or one transposed pair.
        assert len(reported) == len(computed), path
        differences = [
            offset
            for offset, (left, right) in enumerate(
                zip(computed, reported, strict=False)
            )
            if left != right
        ]
        if len(differences) == 2:
            first, second = differences
            assert computed[first] == reported[second], path
            assert computed[second] == reported[first], path
            assert second - first <= 2, path
        else:
            assert len(differences) == 1, path


def test_recorded_peer_receipt_contains_correlated_attribution_and_quote():
    record = json.loads(
        (EVIDENCE / "native-replies-rpc-2.69.0-2026-11-15.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["exit_status"] == 0
    assert record["sdk_client"] == record["sdk_server"]
    assert len(record["peer_receipts"]) == 1
    receipt = record["peer_receipts"][0]
    quote = receipt["quote"]
    assert quote["kind"] == "WithMessage"
    assert receipt["chatId"] == quote["chatId"]
    assert receipt["id"] != quote["messageId"]
    assert receipt["fromId"] != 1
    sender, origin = receipt["overrideSenderName"].rsplit(" [", 1)
    assert origin.endswith("]")
    origin = origin[:-1]
    assert receipt["text"] == f"[{origin}] {sender}: {quote['text']} reply"
