"""Native Chatmail quotes preserve scoped references and durable attempt identity."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import msgspec
import pytest

from medre.adapters.chatmail.adapter import ChatmailAdapter, validate_payload
from medre.adapters.chatmail.codec import ChatmailCodec
from medre.adapters.chatmail.renderer import ChatmailRenderer
from medre.adapters.chatmail.session import RpcCallError
from medre.adapters.chatmail.state import Journal
from medre.core.contracts.adapter import AdapterPermanentError
from medre.core.contracts.delivery import DeferredHandoffCompleted
from medre.core.events.canonical import (
    EventRelation,
    NativeMessageRef,
    NativeRef,
    RelationTargetFact,
)
from medre.core.planning.relation_enricher import RelationEnricher
from medre.core.rendering.renderer import RenderingContext

from conftest import NOW, NativeSession, attempt, rendered, snapshot


class ManualAdapter(ChatmailAdapter):
    async def _run(self):
        import asyncio

        await asyncio.Event().wait()


def relation(adapter="mail", channel="10", native="profile-uuid:19"):
    return EventRelation(
        relation_type="reply",
        target_event_id="prior-canonical-event",
        target_native_ref=NativeRef(adapter, channel, native),
        key=None,
        fallback_text="earlier sender's message",
        target_fact=RelationTargetFact(
            status="bound",
            adapter=adapter,
            native_channel_id=channel,
            native_message_id=native,
        ),
    )


def event(config, *, reference=None):
    origin = ChatmailCodec(config, "profile-uuid").decode(snapshot(), timestamp=NOW)
    assert origin is not None
    return msgspec.structs.replace(
        origin, relations=(reference if reference is not None else relation(),)
    )


def rendering_context():
    return RenderingContext(
        target_adapter="mail",
        target_channel="10",
        target_platform="chatmail",
        delivery_strategy="direct",
    )


async def test_renderer_retains_scoped_native_reference_and_readable_fallback(config):
    result = await ChatmailRenderer({"mail": config}).render(
        event(config), rendering_context()
    )
    assert result.payload["reply_native_ref"] == "profile-uuid:19"
    assert (
        "[reply to: earlier sender's message]" in result.payload["reply_fallback_text"]
    )
    assert result.payload["text"] == snapshot()["text"]
    assert validate_payload(result, config)[-2:] == (
        "profile-uuid:19",
        result.payload["reply_fallback_text"],
    )


@pytest.mark.parametrize(
    "scope",
    [
        relation(adapter="foreign"),
        relation(channel="11"),
        relation(native="other-profile:19"),
    ],
)
async def test_renderer_falls_back_for_out_of_scope_reference(config, scope):
    result = await ChatmailRenderer({"mail": config}).render(
        event(config, reference=scope), rendering_context()
    )
    if (
        scope.target_native_ref.adapter != "mail"
        or scope.target_native_ref.native_channel_id != "10"
    ):
        assert "reply_native_ref" not in result.payload
        assert result.fallback_applied == "relation_reply"
        assert "[reply to: earlier sender's message]" in result.payload["text"]
    else:
        # Profile ownership cannot be established by the pure renderer.
        assert result.payload["reply_native_ref"] == "other-profile:19"


async def test_native_send_uses_qualified_same_chat_quote(config, context):
    ctx, _ = context
    session = NativeSession(config, {19: snapshot(19)})
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        result = rendered(
            attempt(),
            payload={
                "chat_id": 10,
                "text": "Alice [from Matrix]: reply",
                "override_sender_name": "Alice [from Matrix]",
                "reply_native_ref": "profile-uuid:19",
                "reply_fallback_text": "Alice [from Matrix]: reply [reply to: earlier text]",
            },
        )
        await adapter.deliver(result)
        assert adapter.journal is not None
        row = adapter.journal.rows()[0]
        assert row["reply_ref"] == "profile-uuid:19"
        await adapter._flush()
        assert session.sent[-1][2] == {
            "text": "Alice [from Matrix]: reply",
            "quotedMessageId": 19,
            "overrideSenderName": "Alice [from Matrix]",
        }
        assert adapter.journal.rows()[0]["status"] == "pending"
        adapter.journal.enqueue(
            attempt(),
            10,
            "Alice [from Matrix]: reply",
            100,
            sender_override="Alice [from Matrix]",
            reply_ref="profile-uuid:19",
            fallback_body="Alice [from Matrix]: reply [reply to: earlier text]",
        )
        with pytest.raises(ValueError, match="contradicts"):
            adapter.journal.enqueue(
                attempt(),
                10,
                "Alice [from Matrix]: reply",
                100,
                sender_override="Alice [from Matrix]",
                reply_ref="profile-uuid:20",
                fallback_body="changed target",
            )
        await adapter._flush()
        assert len(session.sent) == 1
    finally:
        await adapter.stop()


@pytest.mark.parametrize(
    "quoted", [None, snapshot(19, chatId=11), snapshot(20, chatId=10)]
)
async def test_missing_rebound_or_cross_chat_quote_uses_retained_fallback(
    config, context, quoted
):
    ctx, _ = context
    session = NativeSession(config, {19: quoted} if quoted else {})
    native_call = session.call

    async def call(name, *args):
        if name == "get_message" and args[1] not in session.messages:
            raise RpcCallError("native message unavailable")
        return await native_call(name, *args)

    session.call = call
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        await adapter.deliver(
            rendered(
                attempt(),
                payload={
                    "chat_id": 10,
                    "text": "reply",
                    "reply_native_ref": "profile-uuid:19",
                    "reply_fallback_text": "reply [reply to: earlier text]",
                },
            )
        )
        await adapter._flush()
        assert session.sent[-1][2] == {"text": "reply [reply to: earlier text]"}
    finally:
        await adapter.stop()


def test_journal_restart_preserves_quote_and_submission_fence(config):
    journal = Journal(config.state_dir, "profile-uuid")
    journal.enqueue(
        attempt(),
        10,
        "reply",
        3,
        reply_ref="profile-uuid:19",
        fallback_body="reply [reply to: earlier text]",
    )
    row = journal.rows()[0]
    journal.update(row["identity"], "submitting")
    journal.close()
    recovered = Journal(config.state_dir, "profile-uuid")
    try:
        row = recovered.rows()[0]
        assert row["status"] == "abandoned"
        assert row["reply_ref"] == "profile-uuid:19"
        assert row["fallback_body"] == "reply [reply to: earlier text]"
    finally:
        recovered.close()


@pytest.mark.parametrize("failure", ["ambiguous", "missing", "unavailable"])
async def test_renderer_obeys_core_binding_failure(config, failure):
    source = event(config)

    target = msgspec.structs.replace(
        source, event_id=source.relations[0].target_event_id, relations=()
    )

    async def get(_):
        if failure == "unavailable":
            raise OSError("storage read failed")
        return None if failure == "missing" else target

    async def refs(_):
        return [
            NativeMessageRef(
                str(number),
                target.event_id,
                "mail",
                "10",
                f"profile-uuid:{number}",
                None,
                None,
                "outbound",
            )
            for number in (19, 20)
        ]

    enriched = await RelationEnricher(
        SimpleNamespace(get=get, list_native_refs_for_event=refs)
    ).enrich_for_target(source, target_adapter="mail", target_channel="10")
    assert (
        enriched.relations[0].target_fact.status
        == {
            "ambiguous": "ambiguous",
            "missing": "unresolved_target",
            "unavailable": "binding_unavailable",
        }[failure]
    )
    result = await ChatmailRenderer({"mail": config}).render(
        enriched, rendering_context()
    )
    assert "reply_native_ref" not in result.payload
    assert "[reply to: earlier sender's message]" in result.payload["text"]
    assert result.fallback_applied == "relation_reply"


@pytest.mark.parametrize(
    "reference",
    [
        "other-profile:19",
        "profile-uuid:09",
        "profile-uuid:19x",
        "profile-uuid:4294967296",
    ],
)
async def test_unusable_profile_reference_sends_fallback_without_native_lookup(
    config, context, reference
):
    adapter, session = await start_adapter(config, context)
    try:
        result = rendered(
            attempt(),
            payload={
                "chat_id": 10,
                "text": "Alice [Matrix]: reply",
                "override_sender_name": "Alice [Matrix]",
                "reply_native_ref": reference,
                "reply_fallback_text": "Alice [Matrix]: reply [reply to: earlier text]",
            },
        )
        await adapter.deliver(result)
        # The original claim remains immutable even when it is unusable.
        assert adapter.journal.rows()[0]["reply_ref"] == reference
        await adapter._flush()
        assert session.sent == [
            (
                1,
                10,
                {
                    "text": "Alice [Matrix]: reply [reply to: earlier text]",
                    "overrideSenderName": "Alice [Matrix]",
                },
            )
        ]
        assert not any(
            name == "get_message" and args[1] == 19 for name, args in session.calls
        )
    finally:
        await adapter.stop()


async def start_adapter(config, context):
    session = NativeSession(config)
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    return adapter, session


async def test_native_reply_and_fallback_both_respect_utf8_and_character_budgets(
    config,
):
    source = event(config)
    renderer = ChatmailRenderer({"mail": config})
    for ctx in [
        replace(
            rendering_context(), max_text_bytes=len(source.payload["body"].encode())
        ),
        replace(rendering_context(), max_text_chars=len(source.payload["body"])),
    ]:
        with pytest.raises(ValueError, match="budget"):
            await renderer.render(source, ctx)


async def test_unenriched_reference_does_not_authorize_native_quote(config):
    source = event(config)
    relation = msgspec.structs.replace(source.relations[0], target_fact=None)
    result = await ChatmailRenderer({"mail": config}).render(
        msgspec.structs.replace(source, relations=(relation,)), rendering_context()
    )
    assert "reply_native_ref" not in result.payload


def test_quote_ids_match_native_unsigned_32_bit_boundary(config):
    adapter = ChatmailAdapter(config)
    adapter._profile = "profile-uuid"
    assert adapter._reply_id("profile-uuid:4294967295") == 4294967295
    assert adapter._reply_id("profile-uuid:4294967296") is None


async def test_core_bound_target_wins_over_conflicting_relation_reference(config):
    source = event(config)
    target = msgspec.structs.replace(
        source, event_id=source.relations[0].target_event_id, relations=()
    )

    async def get(_):
        return target

    async def refs(_):
        return [
            NativeMessageRef(
                "stored-copy",
                target.event_id,
                "mail",
                "10",
                "profile-uuid:20",
                None,
                None,
                "outbound",
            )
        ]

    enriched = await RelationEnricher(
        SimpleNamespace(get=get, list_native_refs_for_event=refs)
    ).enrich_for_target(source, target_adapter="mail", target_channel="10")
    assert enriched.relations[0].target_fact.status == "bound"
    assert (
        enriched.relations[0].target_native_ref.native_message_id == "profile-uuid:19"
    )
    result = await ChatmailRenderer({"mail": config}).render(
        enriched, rendering_context()
    )
    assert result.payload["reply_native_ref"] == "profile-uuid:20"


async def test_accepted_native_reply_survives_restart_without_requote_or_resend(
    config, context
):
    ctx, _ = context
    session = NativeSession(config, {19: snapshot(19)})
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    result = rendered(
        attempt(),
        payload={
            "chat_id": 10,
            "text": "reply",
            "reply_native_ref": "profile-uuid:19",
            "reply_fallback_text": "reply [reply to: earlier text]",
        },
    )
    try:
        await adapter.deliver(result)
        await adapter._flush()
        assert len(session.sent) == 1
        assert session.sent[0][2]["quotedMessageId"] == 19
        row = adapter.journal.rows()[0]
        session.messages[row["native"]]["state"] = 26
        del session.messages[19]  # Parent availability cannot change an accepted send.
    finally:
        await adapter.stop()
    replacement_session = NativeSession(config, session.messages)
    replacement = ManualAdapter(config, session_factory=lambda _: replacement_session)
    await replacement.start(ctx)
    try:
        await replacement.deliver(result)
        ctx.report_delivery_feedback.side_effect = RuntimeError(
            "feedback sink unavailable"
        )
        with pytest.raises(RuntimeError, match="feedback sink"):
            await replacement._flush()
        assert replacement.journal.rows()[0]["native"] == row["native"]
        ctx.report_delivery_feedback.side_effect = None
        await replacement._flush()
        fact = ctx.report_delivery_feedback.await_args.args[0]
        assert isinstance(fact, DeferredHandoffCompleted)
        assert fact.handoff.native_message_id == f"profile-uuid:{row['native']}"
        assert replacement.journal.rows() == []
        assert replacement_session.sent == []
    finally:
        await replacement.stop()


def test_nullable_quote_migration_retains_legacy_pending_identity(config):
    from pathlib import Path

    root = Path(config.state_dir)
    root.mkdir()
    connection = sqlite3.connect(root / "chatmail-pending.sqlite")
    with connection:
        connection.execute("CREATE TABLE scope (value TEXT NOT NULL)")
        connection.execute("INSERT INTO scope VALUES ('profile-uuid')")
        connection.execute(
            "CREATE TABLE pending (identity TEXT PRIMARY KEY, provenance TEXT NOT NULL, chat INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, native INTEGER, sender_override TEXT)"
        )
        connection.execute(
            "INSERT INTO pending VALUES (?, ?, 10, 'reply', 'pending', 31, 'Alice [Matrix]')",
            (
                Journal.identity(attempt()),
                msgspec.json.encode(attempt()).decode(),
            ),
        )
    connection.close()
    migrated = Journal(config.state_dir, "profile-uuid")
    try:
        row = migrated.rows()[0]
        assert (row["status"], row["native"], row["sender_override"]) == (
            "pending",
            31,
            "Alice [Matrix]",
        )
        assert row["reply_ref"] is row["fallback_body"] is None
        migrated.enqueue(attempt(), 10, "reply", 10, sender_override="Alice [Matrix]")
        assert migrated.provenance(row) == attempt()
    finally:
        migrated.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"reply_native_ref": "profile-uuid:\ud800", "reply_fallback_text": "fallback"},
        {"reply_native_ref": "profile-uuid:19"},
        {"reply_fallback_text": "fallback"},
        {"reply_native_ref": "profile-uuid:19", "reply_fallback_text": "\ud800"},
    ],
)
def test_reply_payload_rejects_unpaired_or_unencodable_claims(config, updates):
    with pytest.raises(AdapterPermanentError):
        validate_payload(
            rendered(payload={"chat_id": 10, "text": "reply", **updates}), config
        )
