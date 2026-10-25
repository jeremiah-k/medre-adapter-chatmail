"""Native presentation stays separate from contact identity and attempt authority."""

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import msgspec
import pytest
from jsonschema import Draft202012Validator
from pathlib import Path

from medre.adapters.chatmail.adapter import ChatmailAdapter, validate_payload
from medre.adapters.chatmail.codec import ChatmailCodec
from medre.adapters.chatmail.config import sender_name
from medre.adapters.chatmail.renderer import ChatmailRenderer
from medre.adapters.chatmail.state import Journal
from medre.core.contracts.adapter import AdapterPermanentError
from medre.core.rendering.renderer import RenderingContext

from conftest import NOW, NativeSession, attempt, rendered, snapshot


async def test_native_sender_and_origin_are_per_message_with_explicit_opt_out(config):
    """Use human labels while preserving the configured body and source event."""
    event = ChatmailCodec(config, "profile").decode(snapshot(), timestamp=NOW)
    ctx = RenderingContext(
        "direct", "mail", "10", "chatmail", source_origin_label="Field"
    )
    renderer = ChatmailRenderer({"mail": config})
    result = await renderer.render(event, ctx)
    assert result.payload["override_sender_name"] == "Alice [Field]"
    assert result.payload["text"] == event.payload["body"]
    assert "override_sender_name" not in event.payload
    disabled = ChatmailRenderer(
        {"mail": replace(config, native_sender_attribution=False)}
    )
    assert "override_sender_name" not in (await disabled.render(event, ctx)).payload


@pytest.mark.parametrize(
    "origin", ["line\nbreak", "line\rbreak", "tab\tname", "\x7f", "x" * 1024]
)
async def test_unsafe_or_oversized_header_claim_falls_back_to_existing_body(
    config, origin
):
    """Optional native presentation never destroys an otherwise deliverable body."""
    event = ChatmailCodec(config, "profile").decode(snapshot(), timestamp=NOW)
    result = await ChatmailRenderer({"mail": config}).render(
        event,
        RenderingContext(
            "direct", "mail", "10", "chatmail", source_origin_label=origin
        ),
    )
    assert result.payload == {"chat_id": 10, "text": event.payload["body"]}


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        12,
        True,
        "x\ny",
        "x\ry",
        "x\ty",
        "\x85",
        "\u2028",
        "\ud800",
        "界" * 342,
    ],
)
def test_payload_boundary_rejects_invalid_native_names(config, value):
    """Bypassing the renderer cannot admit a malformed native header claim."""
    result = rendered(
        payload={"chat_id": 10, "text": "hello", "override_sender_name": value}
    )
    with pytest.raises(AdapterPermanentError, match="sender attribution"):
        validate_payload(result, config)


def test_name_budget_preserves_unicode_and_enforces_configuration(config):
    """UTF-8 budgets preserve emoji joiners and reject nonboolean policy values."""
    assert sender_name("Alice 👩\u200d🔬 [世界]")
    assert sender_name("界" * 341)
    assert not sender_name("界" * 342)
    with pytest.raises(ValueError, match="boolean"):
        replace(config, native_sender_attribution="yes").validate()
    with pytest.raises(AdapterPermanentError, match="sender attribution"):
        validate_payload(
            rendered(
                payload={
                    "chat_id": 10,
                    "text": "hello",
                    "override_sender_name": "Alice",
                }
            ),
            replace(config, native_sender_attribution=False),
        )


def test_journal_binds_sender_claim_to_exact_attempt_and_migrates_old_rows(config):
    """Old pending correlations survive migration without inventing a sender claim."""
    root = Path(config.state_dir)
    root.mkdir()
    with (
        closing(sqlite3.connect(root / "chatmail-pending.sqlite")) as database,
        database,
    ):
        database.execute("CREATE TABLE scope (value TEXT NOT NULL)")
        database.execute("INSERT INTO scope VALUES ('profile')")
        database.execute(
            "CREATE TABLE pending (identity TEXT PRIMARY KEY, provenance TEXT NOT NULL, "
            "chat INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, native INTEGER)"
        )
        database.execute(
            "INSERT INTO pending VALUES (?, ?, 10, 'old', 'pending', 30)",
            (
                Journal.identity(attempt()),
                msgspec.json.encode(attempt()).decode(),
            ),
        )
    journal = Journal(config.state_dir, "profile")
    try:
        old = journal.rows()[0]
        assert (old["status"], old["native"], old["sender_override"]) == (
            "pending",
            30,
            None,
        )
        journal.enqueue(attempt(2), 10, "new", 10, "Alice [Field]")
        journal.enqueue(attempt(2), 10, "new", 10, "Alice [Field]")
        with pytest.raises(ValueError, match="contradicts"):
            journal.enqueue(attempt(2), 10, "new", 10, "Mallory")
        assert journal.rows()[1]["sender_override"] == "Alice [Field]"
    finally:
        journal.close()
    recovered = Journal(config.state_dir, "profile")
    try:
        assert recovered.rows()[1]["status"] == "expired"
        assert recovered.rows()[1]["sender_override"] == "Alice [Field]"
    finally:
        recovered.close()


async def test_worker_sends_persisted_override_and_keeps_native_feedback_semantics(
    config, context
):
    """Native submission uses the queued snapshot and feedback failure never resends."""

    class DrivenAdapter(ChatmailAdapter):
        async def _run(self):
            await asyncio.Event().wait()

    ctx, _ = context
    session = NativeSession(config)
    adapter = DrivenAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    payload = {"chat_id": 10, "text": "hello", "override_sender_name": "Alice [Field]"}
    try:
        handoff = await adapter.deliver(rendered(attempt(), payload=payload))
        assert handoff.confirmation_level == "local_queue"
        payload["override_sender_name"] = "Changed after admission"
        await adapter._flush()
        assert session.sent == [
            (1, 10, {"text": "hello", "overrideSenderName": "Alice [Field]"})
        ]
        assert not ctx.report_delivery_feedback.called
        session.messages[10]["state"] = 26
        ctx.report_delivery_feedback.side_effect = RuntimeError("sink unavailable")
        with pytest.raises(RuntimeError):
            await adapter._flush()
        ctx.report_delivery_feedback.side_effect = None
        await adapter._flush()
        assert len(session.sent) == 1
        feedback = ctx.report_delivery_feedback.call_args.args[0]
        assert feedback.attempt_provenance == attempt()
        assert feedback.handoff.confirmation_level == "remote_service"
    finally:
        await adapter.stop()


def test_remote_override_is_evidence_without_replacing_transport_sender(config):
    """An incoming display claim cannot impersonate another profile contact."""
    codec = ChatmailCodec(config, "profile")
    ordinary = codec.decode(snapshot(), timestamp=NOW)
    claimed = codec.decode(snapshot(overrideSenderName="Alice [Field]"), timestamp=NOW)
    assert claimed.event_id == ordinary.event_id
    assert claimed.source_transport_id == ordinary.source_transport_id
    assert claimed.source_native_ref == ordinary.source_native_ref
    native = claimed.metadata.native.data["chatmail"]
    assert native["sender_label"] == "Alice"
    assert native["sender_override"] == "Alice [Field]"
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/native.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(dict(native))
    invalid = codec.decode(snapshot(overrideSenderName="evil\r\nheader"), timestamp=NOW)
    assert "sender_override" not in invalid.metadata.native.data["chatmail"]
