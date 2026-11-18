"""Recovery, admission isolation and attribution boundary regressions."""

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import NOW, NativeSession, attempt, snapshot

from medre.adapters.chatmail import session as boundary
from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.codec import ChatmailCodec
from medre.adapters.chatmail.state import Journal, ProfileLease


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


async def test_stop_during_executable_discovery_does_not_construct_client(
    config, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()
    clients = []

    def discover(_path):
        entered.set()
        if not release.wait(5.0):
            raise TimeoutError("test discovery gate did not release")
        return "server"

    def factory(*args):
        clients.append(args)
        raise AssertionError("retired startup constructed a client")

    monkeypatch.setattr(boundary, "server_path", discover)
    session = boundary.ChatmailSession(config, rpc_factory=factory)
    start = asyncio.create_task(session.start())
    try:
        assert await asyncio.to_thread(entered.wait, 5.0)
        await session.stop()
        ProfileLease(config.accounts_dir).close()
        release.set()
        with pytest.raises(boundary.SessionError, match="unavailable"):
            await asyncio.wait_for(start, 5.0)
        assert clients == []
        assert session.rpc is None and session.lease is None
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)
        await session.stop()


def test_late_retirement_kills_before_graceful_client_close(monkeypatch):
    calls = []
    rpc = SimpleNamespace()
    monkeypatch.setattr(
        boundary.ChatmailSession,
        "_kill",
        staticmethod(lambda client: calls.append(("kill", client))),
    )
    monkeypatch.setattr(
        boundary.ChatmailSession,
        "_close",
        staticmethod(lambda client: calls.append(("close", client))),
    )
    boundary.ChatmailSession._retire(rpc)
    assert calls == [("kill", rpc), ("close", rpc)]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_inflight_rpc_does_not_steal_graceful_close(config, monkeypatch, outcome):
    session = boundary.ChatmailSession(config)
    session.rpc = SimpleNamespace(process=None)
    entered = threading.Event()
    release_call = threading.Event()
    close_entered = threading.Event()
    release_close = threading.Event()
    kills = []

    def call():
        entered.set()
        assert release_call.wait(5)
        if outcome == "failure":
            raise RuntimeError("RPC failed")
        return "ok"

    def close(_rpc):
        close_entered.set()
        assert release_close.wait(5)

    monkeypatch.setattr(session, "_close", close)
    monkeypatch.setattr(session, "_kill", lambda rpc: kills.append(rpc))
    operation = asyncio.create_task(session._run(call))
    shutdown = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        shutdown = asyncio.create_task(session.stop(timeout=5))
        assert await asyncio.to_thread(close_entered.wait, 5)
        if outcome == "cancelled":
            operation.cancel()
        release_call.set()
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 5)
        elif outcome == "failure":
            with pytest.raises(boundary.SessionError, match="RPC operation failed"):
                await asyncio.wait_for(operation, 5)
        else:
            assert await asyncio.wait_for(operation, 5) == "ok"
        assert not kills
        assert not shutdown.done()
        release_close.set()
        await asyncio.wait_for(shutdown, 5)
    finally:
        release_call.set()
        release_close.set()
        await asyncio.gather(
            operation, *([shutdown] if shutdown else []), return_exceptions=True
        )


async def test_retired_startup_kills_process_before_health_reply(config, monkeypatch):
    """Model the pinned publication seam with a real child and no health reply."""
    import subprocess
    import sys
    from types import ModuleType

    entered = threading.Event()
    release = threading.Event()
    created = []

    class Rpc:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            entered.set()
            assert release.wait(5)
            self.process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            # The SDK health request waits for the child to answer. No reply
            # is possible here; only retirement at publication can unblock it.
            self.process.wait(timeout=5)
            raise RuntimeError("health reply unavailable")

        def close(self):
            pass

    sdk = ModuleType("deltachat_rpc_client")
    rpc_module = ModuleType("deltachat_rpc_client.rpc")
    sdk.Rpc = Rpc
    rpc_module.JsonRpcError = RuntimeError
    monkeypatch.setitem(sys.modules, "deltachat_rpc_client", sdk)
    monkeypatch.setitem(sys.modules, "deltachat_rpc_client.rpc", rpc_module)
    monkeypatch.setattr(boundary, "server_path", lambda _: "server")
    monkeypatch.setattr(boundary, "_DRAIN_TIMEOUT", 0.02)

    def factory(*args):
        client = boundary.build_rpc(*args)
        created.append(client)
        return client

    session = boundary.ChatmailSession(config, rpc_factory=factory)
    start = asyncio.create_task(session.start())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(TimeoutError):
            await session.stop(timeout=0.02)
        assert session.lease is None and session.rpc is None
        ProfileLease(config.accounts_dir).close()
        release.set()
        with pytest.raises(boundary.SessionError):
            await asyncio.wait_for(start, 5)
        assert created[0].process.poll() is not None
        assert not session._active
    finally:
        release.set()
        for client in created:
            boundary.ChatmailSession._kill(client)
        await asyncio.gather(start, return_exceptions=True)
        await session.stop()
