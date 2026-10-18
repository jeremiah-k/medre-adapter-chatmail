"""Opt-in native adapter proof using two dedicated, preconfigured profiles."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from conftest import wait_until

from medre.core.contracts.adapter import AdapterContext
from medre.core.contracts.delivery import DeferredHandoffCompleted
from medre.core.events.delivery import DeliveryAttemptProvenance
from medre.core.rendering.renderer import RenderingResult
from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.session import ChatmailSession

pytestmark = [pytest.mark.live, pytest.mark.timeout(90)]


async def test_native_group_ingress_and_deferred_smtp_handoff(tmp_path):
    path = os.environ.get("MEDRE_CHATMAIL_TEST_CONFIG")
    if not path:
        pytest.skip("Set MEDRE_CHATMAIL_TEST_CONFIG to a private two-profile fixture")
    settings = json.loads(await asyncio.to_thread(Path(path).read_text))
    bot_chat, peer_chat = settings["bot_chat_id"], settings["peer_chat_id"]
    bot_config = ChatmailConfig(
        "mail",
        accounts_dir=settings["bot_accounts_dir"],
        account_id=settings["bot_account_id"],
        chats=(bot_chat,),
        state_dir=str(tmp_path),
        poll_interval_seconds=0.2,
    )
    peer_config = ChatmailConfig(
        "peer",
        accounts_dir=settings["peer_accounts_dir"],
        account_id=settings["peer_account_id"],
    )
    peer = ChatmailSession(peer_config)
    bot = ChatmailAdapter(bot_config)
    inbound, feedback = asyncio.Queue(), asyncio.Queue()
    admissions = []
    checkpoints = {}

    async def publish(event):
        await inbound.put(event)

    async def admit(event, provenance, *, attachment=None):
        assert provenance in {"live", "recovered"}
        admissions.append((event.payload["body"], provenance))
        await inbound.put(event)

    async def load(stream):
        return checkpoints.get(stream)

    async def commit(stream, cursor, metadata):
        from types import SimpleNamespace

        checkpoints[stream] = SimpleNamespace(cursor=cursor, metadata_json=metadata)

    nonce = f"MEDRE Chatmail {uuid4().hex}"
    try:
        await peer.start()
        for account, conversation in ((peer_config.account_id, peer_chat),):
            info = await peer.call("get_basic_chat_info", account, conversation)
            assert (
                info["chatType"] == "Group"
            ), "The live fixture must select a group conversation"
        await peer.call("start_io", peer_config.account_id)
        await bot.start(
            AdapterContext(
                "mail",
                publish,
                logging.getLogger("chatmail-live"),
                lambda: datetime.now(timezone.utc),
                asyncio.Event(),
                admit_inbound=admit,
                load_checkpoint=load,
                commit_checkpoint=commit,
                report_delivery_feedback=feedback.put,
            )
        )
        original_id = await peer.call(
            "send_msg", peer_config.account_id, peer_chat, {"text": nonce}
        )
        async with asyncio.timeout(30):
            while (event := await inbound.get()).payload["body"] != nonce:
                pass
        assert event.source_channel_id == str(bot_chat)
        quoted = nonce + " quoted"
        await peer.call(
            "send_msg",
            peer_config.account_id,
            peer_chat,
            {"text": quoted, "quotedMessageId": original_id},
        )
        async with asyncio.timeout(30):
            while (quote_event := await inbound.get()).payload["body"] != quoted:
                pass
        assert quote_event.relations[0].target_native_ref == event.source_native_ref
        # Kill the real RPC server, rather than a session double, then prove
        # that a fresh profile owner resumes the same group stream.
        process = bot.session.rpc.process
        process.kill()
        await asyncio.to_thread(process.wait, 2)
        await wait_until(lambda: bot._reconnects > 0, timeout=20)
        assert bot.session.rpc.process.pid != process.pid
        assert not bot._task.done()

        # Receive into native storage while MEDRE is stopped. Its new adapter
        # instance must recover this message beyond the saved core checkpoint.
        await bot.stop()
        receiver = ChatmailSession(bot_config)
        offline = nonce + " offline"
        try:
            await receiver.start()
            await receiver.call("start_io", bot_config.account_id)
            await peer.call(
                "send_msg", peer_config.account_id, peer_chat, {"text": offline}
            )
            async with asyncio.timeout(30):
                while True:
                    ids = await receiver.call(
                        "get_message_ids", bot_config.account_id, bot_chat, False, False
                    )
                    snapshots = [
                        await receiver.call("get_message", bot_config.account_id, mid)
                        for mid in ids
                    ]
                    if any(row["text"] == offline for row in snapshots):
                        break
                    receiver.wake.clear()
                    try:
                        await asyncio.wait_for(receiver.wake.wait(), 0.2)
                    except TimeoutError:
                        pass
        finally:
            await receiver.stop()
        ctx = bot.ctx
        bot = ChatmailAdapter(bot_config)
        await bot.start(ctx)
        async with asyncio.timeout(10):
            while (recovered := await inbound.get()).payload["body"] != offline:
                pass
        assert (offline, "recovered") in admissions
        assert recovered.source_channel_id == str(bot_chat)
        provenance = DeliveryAttemptProvenance(
            event_id=nonce,
            delivery_plan_id=nonce,
            target_adapter="mail",
            target_channel=str(bot_chat),
            outbox_id=nonce,
            attempt_number=1,
            source="live",
        )
        reply = nonce + " reply"
        handoff = await bot.deliver(
            RenderingResult(
                nonce,
                "mail",
                str(bot_chat),
                {"text": reply, "chat_id": bot_chat},
                attempt_provenance=provenance,
            )
        )
        assert handoff.disposition == "deferred"
        async with asyncio.timeout(30):
            fact = await feedback.get()
        assert isinstance(fact, DeferredHandoffCompleted)
        assert fact.handoff.confirmation_level == "remote_service"
        async with asyncio.timeout(30):
            found = False
            while not found:
                peer.wake.clear()
                for message_id in await peer.call(
                    "get_message_ids", peer_config.account_id, peer_chat, False, False
                ):
                    snapshot = await peer.call(
                        "get_message", peer_config.account_id, message_id
                    )
                    if snapshot["fromId"] != 1 and snapshot["text"] == reply:
                        found = True
                        break
                if not found:
                    try:
                        await asyncio.wait_for(peer.wake.wait(), 1)
                    except TimeoutError:
                        pass
    finally:
        try:
            await bot.stop()
        finally:
            await peer.stop()
