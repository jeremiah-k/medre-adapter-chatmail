"""Check archival receipt consistency without qualifying an actual campaign."""

import hashlib
import json
import re
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/evidence"
AUDIT = "native-replies-rpc-2.69.0-2026-11-15-source-audit.json"


def test_source_audit_preserves_reported_record_and_discrepancies():
    audit = json.loads((EVIDENCE / AUDIT).read_text(encoding="utf-8"))
    raw = (EVIDENCE / audit["record_file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == audit["record_sha256"]
    record = json.loads(raw)
    for key in ("provider_revision", "core_revision"):
        assert record[key] == audit[key]
        assert re.fullmatch(r"[0-9a-f]{40}", audit[key])
    computed = audit["source_sha256_at_named_revision"]
    reported = record["source_sha256"]
    assert set(computed) == set(reported)
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in computed.values())
    assert set(audit["mismatched_files"]) == {
        path for path, value in computed.items() if reported[path] != value
    }
    # Do not turn a Git-reference audit into a claim about the historical run.
    assert audit["campaign_source_verification"] == "unresolved"
    assert len(audit["mismatched_files"]) == 3


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
