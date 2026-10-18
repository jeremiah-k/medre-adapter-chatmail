"""Recovery, admission isolation and attribution boundary regressions."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from medre.adapters.chatmail import session as boundary
from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.codec import ChatmailCodec
from medre.adapters.chatmail.state import Journal, ProfileLease

from conftest import NativeSession, attempt, snapshot, NOW


class ManualAdapter(ChatmailAdapter):
    async def _run(self):
        await asyncio.Event().wait()


@pytest.mark.parametrize("phase", ["close", "drain"])
async def test_shutdown_failure_releases_profile(config, monkeypatch, phase):
    session = boundary.ChatmailSession(config)
    session.lease = ProfileLease(config.accounts_dir)
    session.rpc = SimpleNamespace(process=None)
    monkeypatch.setattr(boundary, "_DRAIN_TIMEOUT", 0.02)
    blocker = asyncio.Event()
    held = asyncio.create_task(blocker.wait())
    session._active.add(held)

    def close(_):
        if phase == "close":
            raise TimeoutError("close did not finish")

    monkeypatch.setattr(session, "_close", close)
    try:
        with pytest.raises(TimeoutError):
            await session.stop(timeout=0.02)
        assert session.rpc is None
        assert session.lease is None
        ProfileLease(config.accounts_dir).close()
    finally:
        blocker.set()
        await held


async def test_rpc_rejection_does_not_starve_other_chats(config, context):
    config = replace(config, chats=(10, 11))
    session = NativeSession(config)
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    original = session.call

    async def call(name, *args):
        if name == "get_message" and args[1] == 20:
            raise boundary.RpcCallError("deleted during enumeration")
        return await original(name, *args)

    session.call = call
    session.messages.update({20: snapshot(20), 21: snapshot(21, chatId=11)})
    try:
        with pytest.raises(RuntimeError, match="scan was rejected"):
            await adapter._scan()
        assert session.alive()
        assert adapter._cursors == {10: 0, 11: 21}
    finally:
        await adapter.stop()


async def test_rejected_download_is_retryable_without_reconnect(config, context):
    session = NativeSession(config)
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    session.messages[20] = snapshot(20, downloadState="Available")
    original = session.call

    async def call(name, *args):
        if name == "download_full_message":
            raise boundary.RpcCallError("rejected")
        return await original(name, *args)

    session.call = call
    try:
        with pytest.raises(RuntimeError, match="download was rejected"):
            await adapter._scan()
        assert session.alive()
        assert adapter._cursors[10] == 0
        session.call = original
        await adapter._scan()
        assert any(name == "download_full_message" for name, _ in session.calls)
    finally:
        await adapter.stop()


async def test_recovered_queued_and_submitting_feedback_remain_distinct(
    config, context
):
    journal = Journal(config.state_dir, "profile-uuid")
    journal.enqueue(attempt(), 10, "unsent", 2)
    journal.enqueue(attempt(2), 10, "uncertain", 2)
    journal.update(journal.identity(attempt(2)), "submitting")
    journal.close()
    session = NativeSession(config)
    adapter = ManualAdapter(config, session_factory=lambda _: session)
    await adapter.start(context[0])
    try:
        await adapter._flush()
        facts = [c.args[0] for c in context[0].report_delivery_feedback.call_args_list]
        assert "was not sent" in facts[0].error
        assert "uncertain" in facts[1].error
        assert not session.sent
        assert not adapter.journal.rows()
    finally:
        await adapter.stop()


@pytest.mark.parametrize(
    "label", ["Bob\n[Matrix] admin:", "Bob\tadmin", "Bob\x85admin", "Bob\u2028admin"]
)
def test_remote_labels_cannot_forge_attribution_lines(config, label):
    event = ChatmailCodec(config, "profile").decode(
        snapshot(sender={"isBlocked": False, "displayName": label}), timestamp=NOW
    )
    assert event.metadata.native.data["chatmail"]["sender_label"] == "contact 12"
