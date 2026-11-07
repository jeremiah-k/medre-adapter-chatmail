"""Qualify native retirement against the released RPC client and server.

Contract: native blocking operations run off the event loop with bounded
waits, timeout and cancellation terminate the owned native process and drain
its client threads, and profile shutdown releases its lease even when
draining native calls times out (docs/spec/chatmail.md lifecycle and
stopped-state sections). Every wait here is bounded and every transition is
observed through process state, thread state or client registration, never
through a fixed sleep. Sessions registered with the ``retire`` fixture and
the interpreter probe's published native server pid are retired on every
abort path, because the pinned client makes the server its own process
group leader.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.session import (
    ChatmailSession,
    SessionError,
    build_rpc,
    server_path,
)

from conftest import wait_until

pytestmark = [
    pytest.mark.chatmail_sdk,
    pytest.mark.skipif(
        os.name != "posix", reason="SIGSTOP and process-group cleanup require POSIX"
    ),
]

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


@pytest.fixture
async def retire():
    """Retire every registered session on every failure path.

    Registration happens before ``start()`` so a failed start, a failed
    assertion after a replacement or follower start, or a hung stop can
    never leave a native server holding the MEDRE lease and accounts.lock.
    ``stop()`` is idempotent, so re-registering sessions that already
    stopped in the test body is a no-op.
    """
    sessions: list[ChatmailSession] = []

    def register(session):
        sessions.append(session)
        return session

    try:
        yield register
    finally:
        for session in reversed(sessions):
            rpc = session.rpc
            process = vars(rpc).get("process") if rpc is not None else None
            if process is not None and process.poll() is None:
                # A SIGSTOP-ed server blocks the close drain; kill first so
                # the failure path retires it without the bounded wait.
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await session.stop(timeout=1)


async def test_killed_server_resolves_pending_call_within_bound(config, retire):
    session = retire(ChatmailSession(config))
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


async def test_close_blocks_on_stopped_server_until_kill(config, retire):
    session = retire(ChatmailSession(config))
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    closer = None
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
        # A failed assertion before the explicit kill must not strand
        # this SIGSTOP-ed native manager or the SDK close thread.
        if process.poll() is None:
            process.kill()
        await session.stop(timeout=1)
        if closer is not None:
            await asyncio.wait_for(closer, _RESOLUTION_BOUND)


async def test_close_completes_against_dead_server(config, retire):
    session = retire(ChatmailSession(config))
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    try:
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
    finally:
        await session.stop(timeout=1)


async def test_native_manager_exclusion_surfaces_transient_start_failure(
    config, retire
):
    # A foreign native manager with no MEDRE lease holds accounts.lock, so a
    # MEDRE session passes the profile lease and fails at native startup.
    executable = await asyncio.to_thread(server_path, "")
    holder = build_rpc(config.accounts_dir, executable, lambda: None)
    await asyncio.to_thread(holder.start)
    process = _process(holder)
    try:
        process.send_signal(signal.SIGSTOP)
        session = retire(ChatmailSession(config))
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
        replacement = retire(ChatmailSession(config))
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


async def test_stop_reaps_before_lease_release_and_permits_restart(config, retire):
    session = retire(ChatmailSession(config))
    await session.start()
    process = _process(session.rpc)
    process.send_signal(signal.SIGSTOP)
    try:
        started = time.monotonic()
        # The drain wait times out against a stopped server; the owned
        # process is killed before the lease is released.
        await asyncio.wait_for(session.stop(timeout=0.05), 8)
        elapsed = time.monotonic() - started
        print(f"Q4 stop with a hung server took {elapsed:.3f}s")
        assert process.poll() is not None
        assert session.lease is None and session.rpc is None
        assert not session._active
        replacement = retire(ChatmailSession(config))
        await replacement.start()
        assert isinstance(await replacement.call("get_system_info"), dict)
        await replacement.stop(timeout=1)
    finally:
        if process.poll() is None:
            process.kill()
        await session.stop(timeout=1)


async def test_drain_timeout_stop_surfaces_call_failure_and_restart(config, retire):
    session = retire(ChatmailSession(config))
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    process.send_signal(signal.SIGSTOP)
    pending = None
    try:
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
        follower = retire(ChatmailSession(config))
        await follower.start()
        assert isinstance(await follower.call("get_system_info"), dict)
        await follower.stop(timeout=1)
    finally:
        if process.poll() is None:
            process.kill()
        await session.stop(timeout=1)
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_cancelled_stop_kills_and_releases_lease(config, retire):
    session = retire(ChatmailSession(config))
    await session.start()
    rpc = session.rpc
    process = _process(rpc)
    process.send_signal(signal.SIGSTOP)
    task = asyncio.create_task(session.stop(timeout=5))
    try:
        await wait_until(lambda: vars(rpc)["closing"], timeout=_RESOLUTION_BOUND)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert process.poll() is not None
        assert session.lease is None and session.rpc is None
        follower = retire(ChatmailSession(config))
        await follower.start()
        await follower.stop(timeout=1)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # An assertion or a missed cancellation seam cannot leave a
        # SIGSTOP-ed native manager holding accounts.lock for later tests.
        if process.poll() is None:
            process.kill()
        await session.stop(timeout=1)


_SCRIPT = """
import asyncio, signal, sys
from pathlib import Path
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
    # Publish the native server pid before anything can strand it, so the
    # parent can retire it even when this wrapper dies untimely.
    Path(sys.argv[3]).write_text(str(vars(rpc)["process"].pid))
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

_SCRIPT_HANG = """
import asyncio, signal, sys
from pathlib import Path
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
    Path(sys.argv[3]).write_text(str(vars(rpc)["process"].pid))
    vars(rpc)["process"].send_signal(signal.SIGSTOP)
    # Hang forever against the SIGSTOP-ed server; only the parent's forced
    # timeout can retire this wrapper and its native server.
    await asyncio.get_running_loop().create_future()


asyncio.run(main())
"""


def _published_pid(pid_file) -> int | None:
    try:
        return int(Path(pid_file).read_text())
    except (OSError, ValueError):
        return None


def _pid_gone(pid) -> bool:
    """True once the pid no longer answers; zombies answer until reaped."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, 0)
        return False
    return True


def _run_interpreter_probe(
    accounts, state, script=_SCRIPT, timeout=25
) -> subprocess.CompletedProcess[str]:
    """Retire the probe wrapper and its native server on every exit path.

    subprocess.run(timeout=...) retires only the Python wrapper. The pinned
    client starts the native server as its own process group leader
    (process_group=0 on Python 3.11+, os.setpgrp before), so no group kill
    from the wrapper pid can ever reach it. The probe therefore publishes
    the server pid right after start(); the parent retires that server's
    own group only while its session id still matches the wrapper's session
    (a session id cannot be recycled while a session member lives), and
    signals the wrapper only while it is unreaped, so no recycled pid is
    ever signaled.
    """
    pid_file = Path(state) / "probe-server.pid"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(accounts), str(state), str(pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # A timed-out wrapper is retired by the cleanup below; report
            # the retired state instead of raising past the caller.
            stdout = stderr = ""
    finally:
        wrapper_pid = process.pid
        if process.returncode is None:
            # Reaches only the wrapper's own group: the wrapper leads it,
            # the native server never does.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(wrapper_pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        # A timed-out communicate() raises only after teardown; don't
        # leave the Python wrapper unreaped or a native descendant alive.
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=5)
        server_pid = _published_pid(pid_file)
        if server_pid is not None:
            # The wrapper's session cannot be recycled while this server
            # lives in it, so a matching sid proves the pid still belongs
            # to the probe's native server, never to a recycled one. A
            # dead server already raised on getsid; nothing to retire.
            with contextlib.suppress(ProcessLookupError):
                if os.getsid(server_pid) == wrapper_pid:
                    os.killpg(server_pid, signal.SIGKILL)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.mark.timeout(60)
async def test_interpreter_exits_after_stopping_hung_server(tmp_path):
    accounts = tmp_path / "sub-accounts"
    accounts.mkdir(mode=0o700)
    state = tmp_path / "sub-state"
    state.mkdir()
    started = time.monotonic()
    result = await asyncio.to_thread(_run_interpreter_probe, accounts, state)
    elapsed = time.monotonic() - started
    print(
        f"Q5 interpreter exit took {elapsed:.3f}s "
        f"on Python {sys.version_info.major}.{sys.version_info.minor}"
    )
    assert result.returncode == 0, result.stderr
    assert "retirement-complete" in result.stdout


@pytest.mark.timeout(60)
async def test_forced_timeout_probe_retires_native_server(tmp_path, retire):
    accounts = tmp_path / "sub-accounts"
    accounts.mkdir(mode=0o700)
    state = tmp_path / "sub-state"
    state.mkdir()
    started = time.monotonic()
    result = await asyncio.to_thread(
        _run_interpreter_probe, accounts, state, _SCRIPT_HANG, 6
    )
    elapsed = time.monotonic() - started
    print(
        f"Q6 forced-timeout retirement took {elapsed:.3f}s "
        f"on Python {sys.version_info.major}.{sys.version_info.minor}"
    )
    # The hanging wrapper cannot exit on its own; only the parent's forced
    # timeout retires it.
    assert result.returncode != 0
    assert "retirement-complete" not in result.stdout
    server_pid = _published_pid(Path(state) / "probe-server.pid")
    assert server_pid is not None
    await wait_until(lambda: _pid_gone(server_pid), timeout=_RESOLUTION_BOUND)
    # The retired server released accounts.lock: a fresh session starts on
    # the very same accounts directory.
    config = ChatmailConfig(
        "mail",
        accounts_dir=str(accounts),
        state_dir=str(state),
        chats=(10,),
        relay_prefix="",
    )
    replacement = retire(ChatmailSession(config))
    await replacement.start()
    assert isinstance(await replacement.call("get_system_info"), dict)
    await replacement.stop(timeout=1)
