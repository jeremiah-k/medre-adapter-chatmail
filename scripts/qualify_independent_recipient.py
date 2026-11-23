#!/usr/bin/env python3
"""Validate a redacted Chatmail independent-recipient evidence record.

The record must carry three separately named delivery facts per routed
attempt: local queue acceptance, SMTP relay acceptance, and an independent
recipient observation from a second, independently controlled native profile.
This tool validates completeness and self-consistency, NOT evidence
authenticity. It never opens a native profile, sends a message, or promotes
capabilities. A local SDK queue return can never satisfy the recipient
observation fact: the schema refuses a recipient claim without the relay
acceptance fact beneath it.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

COMMIT = re.compile(r"^[0-9a-f]{40}$")

SDK_DISTRIBUTIONS = ("deltachat-rpc-client", "deltachat-rpc-server")

CASES = (
    "group_delivery_observed",
    "native_quote_observed",
    "restart_continuation_observed",
    "sibling_destination_excluded",
)

OBSERVATION_CASES = CASES[:3]
EXCLUSION_CASE = CASES[3]

CASE_FIELDS = (
    "queue_acceptance",
    "relay_acceptance",
    "recipient_observed",
    "passed",
    "source_event_ref",
    "outbox_ref",
    "receipt_native_ref",
    "evidence_ref",
)


def _pinned_sdks() -> dict[str, str]:
    """Evidence records must name the SDK versions pyproject exact-pins."""
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]
    pins: dict[str, str] = {}
    for requirement in project["dependencies"]:
        if "==" not in requirement:
            continue
        name, _, value = requirement.partition("==")
        if name in SDK_DISTRIBUTIONS:
            pins[name] = value
    missing = sorted(set(SDK_DISTRIBUTIONS) - set(pins))
    if missing or len(set(pins.values())) != 1:
        raise SystemExit(
            "pyproject must exact-pin the SDK client and server at one shared "
            f"version; unpinned: {missing}"
        )
    return pins


def check_installed_sdk() -> dict[str, str]:
    """Verify the local observation environment, not a remote peer's SDK."""
    installed: dict[str, str] = {}
    for name in SDK_DISTRIBUTIONS:
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"{name} is not installed") from exc
        if installed[name] != _pinned_sdks()[name]:
            raise ValueError(f"Installed {name} does not match the declared pin")
    return installed


def template() -> dict[str, Any]:
    pins = _pinned_sdks()
    return {
        "format": "medre-chatmail-independent-recipient-evidence",
        "version": 1,
        "provider_commit": "",
        "core_commit": "",
        "sdk_client_version": pins["deltachat-rpc-client"],
        "sdk_server_version": pins["deltachat-rpc-server"],
        "relay_kind": "",
        "sender_profile_tag": "",
        "recipient_profile_tag": "",
        "sibling_profile_tag": "",
        "cases": {
            name: {
                "queue_acceptance": False,
                "relay_acceptance": False,
                "recipient_observed": False,
                "passed": False,
                "source_event_ref": "",
                "outbox_ref": "",
                "receipt_native_ref": "",
                "evidence_ref": "",
            }
            for name in CASES
        },
    }


def _printable_tag(value: Any, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= limit
        and value == value.strip()
        and value.isprintable()
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object fields are not accepted")
        result[key] = value
    return result


def _valid_commit(value: Any) -> bool:
    return (
        isinstance(value, str) and COMMIT.fullmatch(value) is not None
    ) and value != "0" * 40


def _check_case_facts(name: str, case: Any, errors: list[str]) -> None:
    if not isinstance(case, dict) or set(case) != set(CASE_FIELDS):
        errors.append(f"{name}: unexpected evidence fields")
        return
    for fact in (
        "queue_acceptance",
        "relay_acceptance",
        "recipient_observed",
        "passed",
    ):
        if type(case[fact]) is not bool:
            errors.append(f"{name}: {fact} must be a boolean")
            return
    # The three delivery levels are ordered. A recipient claim without the
    # relay fact beneath it is exactly the over-claim this tier exists to
    # refuse; a relay claim without a queued attempt is equally impossible.
    if case["recipient_observed"] and not case["relay_acceptance"]:
        errors.append(
            f"{name}: recipient observation claimed without a relay acceptance fact"
        )
    if case["relay_acceptance"] and not case["queue_acceptance"]:
        errors.append(
            f"{name}: relay acceptance claimed without a local queue acceptance fact"
        )
    if name in OBSERVATION_CASES:
        if case["passed"] is not True or case["recipient_observed"] is not True:
            errors.append(f"{name}: recipient-side pass not independently recorded")
    elif name == EXCLUSION_CASE:
        if case["recipient_observed"] is True:
            errors.append(f"{name}: sibling destination observed the message")
        if case["passed"] is not True:
            errors.append(f"{name}: sibling exclusion was not passed")
        if case["relay_acceptance"] or case["queue_acceptance"]:
            errors.append(f"{name}: sibling case must not claim acceptance facts")
    for ref in ("source_event_ref", "outbox_ref", "receipt_native_ref", "evidence_ref"):
        if case[ref] != "" and not _printable_tag(case[ref], 512):
            errors.append(f"{name}: bounded printable {ref} required")


def _check_qualified_refs(name: str, case: dict[str, Any], errors: list[str]) -> None:
    if name in OBSERVATION_CASES:
        for ref in (
            "source_event_ref",
            "outbox_ref",
            "receipt_native_ref",
            "evidence_ref",
        ):
            if not case[ref]:
                errors.append(f"{name}: {ref} must correlate the observed attempt")
    elif name == EXCLUSION_CASE and not case["evidence_ref"]:
        errors.append(f"{name}: an exclusion witness reference is required")


def validate(
    record: Any, *, expected_core_commit: str, expected_provider_commit: str
) -> tuple[str, ...]:
    """Fail closed on missing/stale or self-contradicting evidence metadata."""
    errors: list[str] = []
    for expected in (expected_core_commit, expected_provider_commit):
        if not _valid_commit(expected):
            return (
                "Expected revisions must be non-null immutable 40-character commits",
            )
    if not isinstance(record, dict):
        return ("Evidence must be one object",)
    if set(record) != set(template()):
        errors.append("Evidence must contain exactly the supported fields")
    if record.get("format") != "medre-chatmail-independent-recipient-evidence":
        errors.append("Unsupported evidence format")
    if type(record.get("version")) is not int or record["version"] != 1:
        errors.append("Unsupported evidence version")
    pins = _pinned_sdks()
    if record.get("sdk_client_version") != pins["deltachat-rpc-client"]:
        errors.append("SDK client version must match the pinned contract")
    if record.get("sdk_server_version") != pins["deltachat-rpc-server"]:
        errors.append("SDK server version must match the pinned contract")
    for key in ("provider_commit", "core_commit"):
        if not _valid_commit(record.get(key)):
            errors.append(f"{key} must identify an immutable 40-character commit")
    if record.get("core_commit") != expected_core_commit:
        errors.append("Core revision does not match the selected qualification target")
    if record.get("provider_commit") != expected_provider_commit:
        errors.append(
            "Provider revision does not match the selected qualification target"
        )
    tags = {
        key: record.get(key)
        for key in (
            "sender_profile_tag",
            "recipient_profile_tag",
            "sibling_profile_tag",
        )
    }
    if (
        not all(_printable_tag(tag, 128) for tag in tags.values())
        or len(set(tags.values())) != 3
    ):
        errors.append("Three distinct bounded profile tags are required")
    if not _printable_tag(record.get("relay_kind"), 128):
        errors.append("A bounded printable relay kind is required")
    cases = record.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(CASES):
        errors.append("All four required independent-recipient cases must be present")
        return tuple(errors)
    for name in CASES:
        _check_case_facts(name, cases[name], errors)
    if errors:
        return tuple(errors)
    for name in CASES:
        _check_qualified_refs(name, cases[name], errors)
    receipts = [
        cases[name]["receipt_native_ref"]
        for name in OBSERVATION_CASES
        if cases[name]["receipt_native_ref"]
    ]
    if len(set(receipts)) != len(receipts):
        errors.append(
            "One receipt identity cannot witness two cases; duplicate observation"
        )
    outboxes = [
        cases[name]["outbox_ref"] for name in CASES if cases[name]["outbox_ref"]
    ]
    if len(set(outboxes)) != len(outboxes):
        errors.append("An outbox identity belongs to exactly one routed attempt")
    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--template", action="store_true", help="Print an unqualified evidence template"
    )
    mode.add_argument(
        "--check-sdk", action="store_true", help="Verify the installed observation SDK"
    )
    mode.add_argument(
        "--validate",
        metavar="FILE",
        type=Path,
        help="Validate an independent-recipient observation record",
    )
    parser.add_argument(
        "--core-commit", help="Expected immutable core qualification revision"
    )
    parser.add_argument(
        "--provider-commit", help="Expected immutable provider qualification revision"
    )
    args = parser.parse_args(argv)
    if (args.template or args.check_sdk) and (
        args.core_commit is not None or args.provider_commit is not None
    ):
        parser.error("Revision selectors apply only to --validate")
    if args.template or args.check_sdk:
        try:
            installed = check_installed_sdk()
        except ValueError as exc:
            print(f"Unqualified: {exc}", file=sys.stderr)
            return 1
        if args.template:
            record = template()
            record["sdk_client_version"] = installed["deltachat-rpc-client"]
            record["sdk_server_version"] = installed["deltachat-rpc-server"]
            print(json.dumps(record, indent=2))
        else:
            print("Installed SDK distributions match the declared pins")
        return 0
    if any(not _valid_commit(ref) for ref in (args.core_commit, args.provider_commit)):
        parser.error(
            "--validate requires non-null immutable --core-commit and --provider-commit"
        )
    try:
        path: Path = args.validate
        if not path.is_file():
            raise ValueError("Evidence must be a regular file")
        with path.open("rb") as handle:
            payload = handle.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Evidence record exceeds the 1 MiB limit")
        record = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f"Evidence not usable: {type(exc).__name__}", file=sys.stderr)
        return 2
    errors = validate(
        record,
        expected_core_commit=args.core_commit,
        expected_provider_commit=args.provider_commit,
    )
    if errors:
        for error in errors:
            print(f"Unqualified: {error}", file=sys.stderr)
        return 1
    print(
        "Record structurally complete for selected revisions; "
        "independent observation authenticity not verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
