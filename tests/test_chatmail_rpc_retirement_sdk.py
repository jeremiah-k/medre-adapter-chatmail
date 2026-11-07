"""Qualify native retirement against the released RPC client and server.

Contract: native blocking operations run off the event loop with bounded
waits, timeout and cancellation terminate the owned native process and drain
its client threads, and profile shutdown releases its lease even when
draining native calls times out (docs/spec/chatmail.md lifecycle and
stopped-state sections). Every wait here is bounded and every transition is
observed through process state, thread state or client registration, never
through a fixed sleep.
"""

import asyncio
import contextlib
import signal
import subprocess
import sys
import time

import pytest

from medre.adapters.chatmail.session import (
    ChatmailSession,
    SessionError,
    build_rpc,
    server_path,
)

from conftest import wait_until

pytestmark = pytest.mark.chatmail_sdk

# The SDK reader loop fails every registered request on pipe EOF; these
# bounds only guard scheduling, and the measured values stay far below them.
_RESOLUTION_BOUND = 5.0
_BLOCK_PROBE = 1.5


def _process(rpc) -> object:
    return vars(rpc)["process"]


async def _reap(process) -> None:
    if process.poll() is None:
        process.kill()
    await asyncio.to_thread(process.wait, _RESOLUTION_BOUND)


async def test_killed_server_resolves_pending_call_within_bound(config):
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    try:
        # Stop the server first so the pending call cannot receive an answer
        # before the kill; the request is registered synchronously by
        # future() before the off-thread wait begins.
        process.send_signal(signal.SIGSTOP)
        pending = rpc.get_system_info.future()
        worker = asyncio.create_task(asyncio.to_thread(pending))
        started = time.monotonic()
        process.kill()
        from deltachat_rpc_client.rpc import JsonRpcError

        with pytest.raises(JsonRpcError, match="RPC server closed"):
            await asyncio.wait_for(worker, _RESOLUTION_BOUND)
        elapsed = time.monotonic() - started
        print(f"Q1 pending call resolved {elapsed:.3f}s after SIGKILL")
        assert elapsed < _RESOLUTION_BOUND
        # The reader loop fails the pending call on pipe EOF and the event
        # pump exits through the same failure. The writer only stops on an
        # explicit close(), so it is retired by the shutdown path instead.
        await wait_until(
            lambda: not vars(rpc)["reader_thread"].is_alive()
            and not vars(rpc)["events_thread"].is_alive(),
            timeout=_RESOLUTION_BOUND,
        )
    finally:
        await session.stop(timeout=1)
    assert process.poll() is not None
    assert not session._active


async def test_close_blocks_on_stopped_server_until_kill(config):
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    try:
        process.send_signal(signal.SIGSTOP)
        closer = asyncio.create_task(asyncio.to_thread(rpc.close))
        # close() first issues stop_io_for_all_accounts() without a timeout.
        await wait_until(lambda: vars(rpc)["closing"], timeout=_RESOLUTION_BOUND)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await wait_until(lambda: closer.done(), timeout=_BLOCK_PROBE)
        blocked = time.monotonic() - started
        print(f"Q2 close still blocked after {blocked:.3f}s against a stopped server")
        started = time.monotonic()
        process.kill()
        await wait_until(lambda: closer.done(), timeout=_RESOLUTION_BOUND)
        killed = time.monotonic() - started
        print(f"Q2 close unblocked {killed:.3f}s after SIGKILL")
        assert closer.exception() is None
    finally:
        await session.stop(timeout=1)


async def test_close_completes_against_dead_server(config):
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    process.kill()
    await asyncio.to_thread(process.wait, _RESOLUTION_BOUND)
    started = time.monotonic()
    await asyncio.wait_for(asyncio.to_thread(rpc.close), _RESOLUTION_BOUND)
    elapsed = time.monotonic() - started
    print(f"Q2 dead-server close took {elapsed:.3f}s")
    assert not any(
        vars(rpc)[name].is_alive()
        for name in ("reader_thread", "writer_thread", "events_thread")
    )
    await session.stop(timeout=1)


async def test_native_manager_exclusion_surfaces_transient_start_failure(config):
    # A foreign native manager with no MEDRE lease holds accounts.lock, so a
    # MEDRE session passes the profile lease and fails at native startup.
    executable = await asyncio.to_thread(server_path, "")
    holder = build_rpc(config.accounts_dir, executable, lambda: None)
    await asyncio.to_thread(holder.start)
    process = _process(holder)
    try:
        process.send_signal(signal.SIGSTOP)
        session = ChatmailSession(config)
        started = time.monotonic()
        with pytest.raises(SessionError, match="inspect the native profile"):
            await session.start()
        elapsed = time.monotonic() - started
        print(f"Q3 MEDRE start refused after {elapsed:.3f}s")
        # The native manager retries accounts.lock with a bounded backoff
        # before refusing; an instant refusal would not prove exclusion.
        assert 2.0 <= elapsed < 10
        assert session.lease is None and session.rpc is None
        # A stopped manager still holds the lock; death releases it.
        await _reap(process)
        replacement = ChatmailSession(config)
        await replacement.start()
        info = await replacement.call("get_system_info")
        assert isinstance(info, dict) and info.get("deltachat_core_version")
        await replacement.stop(timeout=1)
    finally:
        await _reap(process)
        await asyncio.wait_for(asyncio.to_thread(holder.close), _RESOLUTION_BOUND)
        # The SDK close path only closes stdin; retire the remaining pipes
        # like the session teardown does.
        holder_process = _process(holder)
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(holder_process, name, None)
            if stream is not None:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(stream.close)


async def test_stop_reaps_before_lease_release_and_permits_restart(config):
    session = ChatmailSession(config)
    await session.start()
    process = _process(session.rpc)
    process.send_signal(signal.SIGSTOP)
    started = time.monotonic()
    # The drain wait times out against a stopped server; the owned process is
    # killed, the drain completes and stop() returns with the lease released.
    await asyncio.wait_for(session.stop(timeout=0.05), 8)
    elapsed = time.monotonic() - started
    print(f"Q4 stop with a hung server took {elapsed:.3f}s")
    assert process.poll() is not None
    assert session.lease is None and session.rpc is None
    assert not session._active
    replacement = ChatmailSession(config)
    await replacement.start()
    assert isinstance(await replacement.call("get_system_info"), dict)
    await replacement.stop(timeout=1)


async def test_drain_timeout_stop_surfaces_call_failure_and_restart(config):
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    process.send_signal(signal.SIGSTOP)
    before = set(vars(rpc)["request_results"])
    pending = asyncio.create_task(session.call("get_system_info"))
    await wait_until(
        lambda: bool(set(vars(rpc)["request_results"]) - before),
        timeout=_RESOLUTION_BOUND,
    )
    await asyncio.wait_for(session.stop(timeout=0.05), 8)
    assert pending.done()
    assert isinstance(pending.exception(), SessionError)
    assert process.poll() is not None
    assert session.lease is None
    follower = ChatmailSession(config)
    await follower.start()
    assert isinstance(await follower.call("get_system_info"), dict)
    await follower.stop(timeout=1)


async def test_cancelled_stop_kills_and_releases_lease(config):
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    process.send_signal(signal.SIGSTOP)
    task = asyncio.create_task(session.stop(timeout=5))
    await wait_until(lambda: vars(rpc)["closing"], timeout=_RESOLUTION_BOUND)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    assert process.poll() is not None
    assert session.lease is None and session.rpc is None
    follower = ChatmailSession(config)
    await follower.start()
    await follower.stop(timeout=1)


_SCRIPT = """
import asyncio, signal, sys
from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.session import ChatmailSession


async def main():
    config = ChatmailConfig(
        "mail",
        accounts_dir=sys.argv[1],
        state_dir=sys.argv[2],
        chats=(10,),
        relay_prefix="",
    )
    session = ChatmailSession(config)
    await session.start()
    rpc = session.rpc
    vars(rpc)["process"].send_signal(signal.SIGSTOP)
    before = set(vars(rpc)["request_results"])
    pending = asyncio.create_task(session.call("get_system_info"))
    while not set(vars(rpc)["request_results"]) - before:
        await asyncio.sleep(0.001)
    await session.stop(timeout=0.05)
    await asyncio.gather(pending, return_exceptions=True)
    assert vars(rpc)["process"].poll() is not None


asyncio.run(main())
print("retirement-complete")
"""


@pytest.mark.timeout(60)
async def test_interpreter_exits_after_stopping_hung_server(tmp_path):
    accounts = tmp_path / "sub-accounts"
    accounts.mkdir(mode=0o700)
    state = tmp_path / "sub-state"
    state.mkdir()
    started = time.monotonic()
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _SCRIPT, str(accounts), str(state)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    elapsed = time.monotonic() - started
    print(
        f"Q5 interpreter exit took {elapsed:.3f}s "
        f"on Python {sys.version_info.major}.{sys.version_info.minor}"
    )
    assert result.returncode == 0, result.stderr
    assert "retirement-complete" in result.stdout
