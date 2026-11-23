"""Opt-in independent-recipient tier: a positive verdict requires a second,
independently controlled native profile to observe the routed delivery.

Each routed attempt yields three separately named facts that must never be
merged: local queue acceptance (the adapter retained the attempt), SMTP relay
acceptance (the outgoing server accepted the handoff), and an independent
recipient observation (the peer profile's own native runtime decrypted the
exact bound text in the expected group). A sibling destination profile must
not observe any routed text. The verdict record is validated by the pure
gate in ``scripts/qualify_independent_recipient.py`` and binds to the exact
core/provider revisions; any mismatch refuses before a message is sent.

Requires a private JSON fixture (see docs/live-testing.md) pointing at a
preconfigured relay with three dedicated profiles in two groups. The test
does not deploy or reconfigure the relay, and cleans up only its own
temporary journal state.
"""

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import msgspec
import pytest
from conftest import wait_until
from medre.core.contracts.adapter import AdapterContext
from medre.core.contracts.delivery import DeferredHandoffCompleted
from medre.core.events.canonical import (
    CanonicalEvent,
    EventRelation,
    RelationTargetFact,
)
from medre.core.events.delivery import DeliveryAttemptProvenance
from medre.core.rendering.renderer import RenderingContext

from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.renderer import ChatmailRenderer
from medre.adapters.chatmail.session import ChatmailSession

GATE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/qualify_independent_recipient.py"
)
_spec = importlib.util.spec_from_file_location("chatmail_recipient_gate", GATE_PATH)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

FIXTURE_FIELDS = (
    "core_commit",
    "provider_commit",
    "bot_accounts_dir",
    "bot_account_id",
    "bot_chat_id",
    "peer_accounts_dir",
    "peer_account_id",
    "peer_chat_id",
    "sibling_accounts_dir",
    "sibling_account_id",
    "sibling_chat_id",
)

pytestmark = [pytest.mark.live, pytest.mark.timeout(240)]


def _git_head(path: Path) -> str | None:
    """Return the HEAD of the checkout that owns this installed file."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError):
                return None
            return result.stdout.strip()
    return None


def _detected_revisions() -> dict[str, str | None]:
    import medre
    from medre.adapters.chatmail import adapter as adapter_module

    return {
        "provider_commit": _git_head(Path(adapter_module.__file__).resolve()),
        "core_commit": _git_head(Path(medre.__file__).resolve()),
    }


def _refuse_revision_mismatch(settings: dict) -> None:
    """Bind the verdict to the exact revisions recorded in the fixture."""
    detected = _detected_revisions()
    for key in ("core_commit", "provider_commit"):
        detected_value = detected[key]
        if detected_value is None:
            pytest.fail(
                f"Cannot bind the verdict to the running {key}; refusing to qualify"
            )
        if detected_value != settings[key]:
            pytest.fail(
                f"Fixture {key} {settings[key]} does not match the running "
                f"revision {detected_value}; refusing to qualify"
            )


def _adapter_config(settings: dict, state_dir: str) -> ChatmailConfig:
    return ChatmailConfig(
        "mail",
        accounts_dir=settings["bot_accounts_dir"],
        account_id=settings["bot_account_id"],
        chats=(settings["bot_chat_id"],),
        state_dir=state_dir,
        poll_interval_seconds=0.2,
    )


def _session_config(settings: dict, role: str) -> ChatmailConfig:
    return ChatmailConfig(
        role,
        accounts_dir=settings[f"{role}_accounts_dir"],
        account_id=settings[f"{role}_account_id"],
    )


async def _require_group(session: ChatmailSession, config: ChatmailConfig, chat_id):
    info = await session.call("get_basic_chat_info", config.account_id, chat_id)
    assert (
        info["chatType"] == "Group"
    ), "The fixture must select a group conversation for every profile"
    return await session.call("get_chat_contacts", config.account_id, chat_id)


async def _snapshots(
    session: ChatmailSession, config: ChatmailConfig, chat_id
) -> list[dict]:
    ids = await session.call(
        "get_message_ids", config.account_id, chat_id, False, False
    )
    return [
        await session.call("get_message", config.account_id, message_id)
        for message_id in ids
    ]


async def _wait_for_observation(
    session: ChatmailSession, config: ChatmailConfig, chat_id, text: str
) -> dict:
    """Wait on observable peer state; duplicates are a failure, not a pass."""
    found: list[dict] = []
    async with asyncio.timeout(60):
        while not found:
            found = [
                row
                for row in await _snapshots(session, config, chat_id)
                if row.get("fromId") != 1 and row.get("text") == text
            ]
            if found:
                break
            session.wake.clear()
            try:
                await asyncio.wait_for(session.wake.wait(), 0.5)
            except TimeoutError:
                pass
    identities = {row["id"] for row in found}
    assert len(identities) == 1, (
        "Duplicate independent observations of the same routed text are "
        "unqualified evidence"
    )
    return found[0]


async def _assert_sibling_excluded(
    session: ChatmailSession, config: ChatmailConfig, chat_id, texts: list[str]
) -> None:
    """The sibling destination actively observes its own group meanwhile."""
    assert session.alive(), "The sibling observer session stopped running"
    observed = {row.get("text") for row in await _snapshots(session, config, chat_id)}
    assert not observed.intersection(
        texts
    ), "The sibling destination received a routed message"


async def _routed_send(
    bot: ChatmailAdapter,
    renderer: ChatmailRenderer,
    bot_config: ChatmailConfig,
    event: CanonicalEvent,
    text: str,
    relations: tuple[EventRelation, ...],
    feedback: asyncio.Queue,
    label: str,
    outbox_id: str,
) -> tuple[dict, DeliveryAttemptProvenance]:
    """Render and deliver one routed attempt through the production path."""
    provenance = DeliveryAttemptProvenance(
        event_id=event.event_id,
        delivery_plan_id=outbox_id,
        target_adapter="mail",
        target_channel=str(bot_config.chats[0]),
        outbox_id=outbox_id,
        attempt_number=1,
        source="live",
    )
    rendered_event = msgspec.structs.replace(
        event,
        event_id=outbox_id,
        payload={"text": text},
        relations=relations,
    )
    result = await renderer.render(
        rendered_event,
        RenderingContext(
            target_adapter="mail",
            target_channel=str(bot_config.chats[0]),
            target_platform="chatmail",
            delivery_strategy="direct",
            source_origin_label=label,
        ),
    )
    # Fact 1 of 3: local queue acceptance only. The deferred disposition and
    # local_queue level claim nothing beyond private retention.
    handoff = await bot.deliver(replace(result, attempt_provenance=provenance))
    assert handoff.disposition == "deferred"
    assert handoff.confirmation_level == "local_queue"
    # Fact 2 of 3: SMTP relay acceptance, reported under the identical ledger
    # identity. A foreign event or outbox identity here is a refusal, never a
    # confirmation.
    async with asyncio.timeout(60):
        fact = await feedback.get()
    assert isinstance(fact, DeferredHandoffCompleted)
    assert fact.attempt_provenance == provenance
    assert fact.handoff.confirmation_level == "remote_service"
    assert fact.handoff.native_channel_id == str(bot_config.chats[0])
    return dict(result.payload), provenance


def _reply_relation(event: CanonicalEvent, bot_chat: int) -> tuple[EventRelation, ...]:
    """Bind the reply to the accepted inbound native reference.

    The accepted inbound native ref survives the core checkpoint and native
    profile restart; it must not be rebound to a different chat or local
    profile on outbound submission.
    """
    return (
        EventRelation(
            relation_type="reply",
            target_event_id=event.event_id,
            target_native_ref=None,
            key=None,
            fallback_text=str(event.payload["body"]),
            target_fact=RelationTargetFact(
                status="bound",
                adapter="mail",
                native_channel_id=str(bot_chat),
                native_message_id=event.source_native_ref.native_message_id,
            ),
        ),
    )


def _verified_record(settings: dict) -> dict:
    record = gate.template()
    record.update(
        provider_commit=settings["provider_commit"],
        core_commit=settings["core_commit"],
        relay_kind="preconfigured-relay",
        sender_profile_tag="bot-sender",
        recipient_profile_tag="peer-recipient",
        sibling_profile_tag="sibling-observer",
    )
    return record


async def test_independent_recipient_qualified_delivery(tmp_path):
    """Group delivery, native quote and restart continuation, all observed."""
    path = os.environ.get("MEDRE_CHATMAIL_RECIPIENT_CONFIG")
    if not path:
        pytest.skip(
            "Set MEDRE_CHATMAIL_RECIPIENT_CONFIG to a private three-profile fixture"
        )
    settings = json.loads(await asyncio.to_thread(Path(path).read_text))
    missing = [field for field in FIXTURE_FIELDS if field not in settings]
    assert not missing, f"Fixture fields missing: {missing}"
    _refuse_revision_mismatch(settings)
    try:
        gate.check_installed_sdk()
    except ValueError as exc:
        pytest.fail(f"Refusing to qualify: {exc}")

    bot_chat = settings["bot_chat_id"]
    peer_chat = settings["peer_chat_id"]
    sibling_chat = settings["sibling_chat_id"]
    assert len({bot_chat, peer_chat, sibling_chat}) == 3
    assert (
        settings["sibling_accounts_dir"] != settings["peer_accounts_dir"]
        and settings["sibling_accounts_dir"] != settings["bot_accounts_dir"]
    ), "The sibling destination must be an independently controlled profile"

    peer_config = _session_config(settings, "peer")
    sibling_config = _session_config(settings, "sibling")
    bot_config = _adapter_config(settings, str(tmp_path))
    peer = ChatmailSession(peer_config)
    sibling = ChatmailSession(sibling_config)
    bot = ChatmailAdapter(bot_config)
    inbound, feedback = asyncio.Queue(), asyncio.Queue()
    admissions = []
    checkpoints = {}
    renderer = ChatmailRenderer({"mail": bot_config})
    seed_nonce = f"MEDRE recipient seed {uuid4().hex}"
    nonce = f"MEDRE recipient tier {uuid4().hex}"

    async def publish(event):
        await inbound.put(event)

    async def admit(event, provenance, *, attachment=None):
        assert provenance in {"live", "recovered"}
        admissions.append((event.payload["body"], provenance))
        await inbound.put(event)

    async def load(stream):
        return checkpoints.get(stream)

    async def commit(stream, cursor, metadata):
        checkpoints[stream] = SimpleNamespace(cursor=cursor, metadata_json=metadata)

    try:
        await peer.start()
        await sibling.start()
        peer_contacts = await _require_group(peer, peer_config, peer_chat)
        await _require_group(sibling, sibling_config, sibling_chat)
        # The declared recipient is the only possible observer in its group:
        # one self identity and one external contact, so no unknown third
        # member can masquerade as the independent recipient.
        assert [
            type(contact) is int and contact > 9 for contact in peer_contacts
        ].count(True) == 1
        await peer.call("start_io", peer_config.account_id)
        await sibling.call("start_io", sibling_config.account_id)
        await bot.start(
            AdapterContext(
                "mail",
                publish,
                logging.getLogger("chatmail-recipient-live"),
                lambda: datetime.now(timezone.utc),
                asyncio.Event(),
                admit_inbound=admit,
                load_checkpoint=load,
                commit_checkpoint=commit,
                report_delivery_feedback=feedback.put,
            )
        )
        # Independent ingress: the declared peer seeds the shared group so the
        # routed attempts correlate to a real admitted source event.
        seed_id = await peer.call(
            "send_msg", peer_config.account_id, peer_chat, {"text": seed_nonce}
        )
        async with asyncio.timeout(60):
            while (event := await inbound.get()).payload["body"] != seed_nonce:
                pass
        assert event.source_channel_id == str(bot_chat)

        routed_texts = []
        # Case 1: plain routed group delivery without a native quote.
        payload_a, provenance_a = await _routed_send(
            bot,
            renderer,
            bot_config,
            event,
            nonce + " delivery",
            (),
            feedback,
            "Independent recipient A",
            uuid4().hex,
        )
        routed_texts.append(payload_a["text"])
        observed_a = await _wait_for_observation(
            peer, peer_config, peer_chat, payload_a["text"]
        )
        receipt_a = str(observed_a["id"])
        await _assert_sibling_excluded(
            sibling, sibling_config, sibling_chat, routed_texts
        )

        # Case 2: renderer-derived native quote for the same admitted source
        # event. The peer's own snapshot must retain the original peer-side
        # message ID and chat ID in its quote, plus the sender override.
        payload_b, provenance_b = await _routed_send(
            bot,
            renderer,
            bot_config,
            event,
            nonce + " quote",
            _reply_relation(event, bot_chat),
            feedback,
            "Independent recipient B",
            uuid4().hex,
        )
        routed_texts.append(payload_b["text"])
        observed_b = await _wait_for_observation(
            peer, peer_config, peer_chat, payload_b["text"]
        )
        receipt_b = str(observed_b["id"])
        quote = observed_b.get("quote", {})
        assert quote.get("kind") == "WithMessage"
        assert quote.get("messageId") == seed_id
        assert quote.get("chatId") == peer_chat
        assert observed_b["id"] != seed_id
        assert observed_b.get("overrideSenderName") == (
            payload_b.get("override_sender_name")
        )
        await _assert_sibling_excluded(
            sibling, sibling_config, sibling_chat, routed_texts
        )

        # Case 3: restart continuation. Kill the real RPC server rather than a
        # session double, then prove that a fresh profile owner resumes the
        # same group stream.
        process = bot.session.rpc.process
        process.kill()
        await asyncio.to_thread(process.wait, 2)
        await wait_until(lambda: bot._reconnects > 0, timeout=20)
        assert bot.session.rpc.process.pid != process.pid
        assert not bot._task.done()

        # Receive into native storage while MEDRE is stopped. Its new adapter
        # instance must recover this message beyond the saved core checkpoint.
        await bot.stop()
        offline_nonce = nonce + " offline"
        receiver = ChatmailSession(bot_config)
        try:
            await receiver.start()
            await receiver.call("start_io", bot_config.account_id)
            await peer.call(
                "send_msg",
                peer_config.account_id,
                peer_chat,
                {"text": offline_nonce},
            )
            async with asyncio.timeout(60):
                while not any(
                    row.get("text") == offline_nonce
                    for row in await _snapshots(receiver, bot_config, bot_chat)
                ):
                    receiver.wake.clear()
                    try:
                        await asyncio.wait_for(receiver.wake.wait(), 0.5)
                    except TimeoutError:
                        pass
        finally:
            await receiver.stop()
        ctx = bot.ctx
        bot = ChatmailAdapter(bot_config)
        await bot.start(ctx)
        async with asyncio.timeout(30):
            while (recovered := await inbound.get()).payload["body"] != offline_nonce:
                pass
        assert (offline_nonce, "recovered") in admissions
        assert recovered.source_channel_id == str(bot_chat)
        payload_c, provenance_c = await _routed_send(
            bot,
            renderer,
            bot_config,
            recovered,
            nonce + " resume",
            _reply_relation(recovered, bot_chat),
            feedback,
            "Independent recipient C",
            uuid4().hex,
        )
        routed_texts.append(payload_c["text"])
        observed_c = await _wait_for_observation(
            peer, peer_config, peer_chat, payload_c["text"]
        )
        receipt_c = str(observed_c["id"])
        assert (
            len({receipt_a, receipt_b, receipt_c}) == 3
        ), "One receipt identity cannot witness two cases"
        await _assert_sibling_excluded(
            sibling, sibling_config, sibling_chat, routed_texts
        )

        # Verdict: the pure gate re-derives qualification from the runtime
        # facts alone, bound to the fixture revisions. Content-free by
        # construction: ids, booleans and tags only.
        record = _verified_record(settings)
        evidence_ref = f"local-redacted-observation/{uuid4().hex}"
        cases = record["cases"]
        cases["group_delivery_observed"].update(
            queue_acceptance=True,
            relay_acceptance=True,
            recipient_observed=True,
            passed=True,
            source_event_ref=event.event_id,
            outbox_ref=provenance_a.outbox_id,
            receipt_native_ref=receipt_a,
            evidence_ref=evidence_ref + "-delivery.json",
        )
        cases["native_quote_observed"].update(
            queue_acceptance=True,
            relay_acceptance=True,
            recipient_observed=True,
            passed=True,
            source_event_ref=event.event_id,
            outbox_ref=provenance_b.outbox_id,
            receipt_native_ref=receipt_b,
            evidence_ref=evidence_ref + "-quote.json",
        )
        cases["restart_continuation_observed"].update(
            queue_acceptance=True,
            relay_acceptance=True,
            recipient_observed=True,
            passed=True,
            source_event_ref=recovered.event_id,
            outbox_ref=provenance_c.outbox_id,
            receipt_native_ref=receipt_c,
            evidence_ref=evidence_ref + "-restart.json",
        )
        cases["sibling_destination_excluded"].update(
            passed=True,
            evidence_ref=evidence_ref + "-sibling.json",
        )
        errors = gate.validate(
            record,
            expected_core_commit=settings["core_commit"],
            expected_provider_commit=settings["provider_commit"],
        )
        assert errors == (), errors
    finally:
        try:
            await bot.stop()
        finally:
            try:
                await sibling.stop()
            finally:
                await peer.stop()
