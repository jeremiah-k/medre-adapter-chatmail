"""Account connectivity reports reuse the existing native-owner loop and authority."""

import asyncio

import pytest

from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.session import SessionError
from conftest import NativeSession, wait_until


async def test_connectivity_loss_and_recovery_publish_without_probe(config, context):
    """The selected account's observed connectivity drives health without another poller."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)
    adapter = ChatmailAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        await wait_until(lambda: reports and reports[-1].health == "healthy")
        session.connectivity = 1000
        session.wake.set()
        await wait_until(lambda: reports and reports[-1].health == "degraded")
        session.connectivity = 4000
        session.wake.set()
        await wait_until(lambda: reports and reports[-1].health == "healthy")
        count = len(reports)
        adapter._set_state("healthy")
        assert len(reports) == count
    finally:
        await adapter.stop()


async def test_observer_failure_and_retired_epoch_do_not_change_owner(config, context):
    """Retry only failed notifications and reject state changes from a retired loop."""
    ctx, _ = context
    reports = []

    def refused(info):
        """Keep native calls independent of optional diagnostic consumers."""
        raise RuntimeError("observer unavailable")

    ctx.report_health = refused
    adapter = ChatmailAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    try:
        await wait_until(lambda: adapter._state == "healthy")
        ctx.report_health = reports.append
        adapter.session.wake.set()
        await wait_until(lambda: bool(reports))
        epoch = adapter._health_epoch
    finally:
        await adapter.stop()
    await adapter.start(ctx)
    try:
        await wait_until(lambda: adapter._state == "healthy")
        count = len(reports)
        adapter._set_state("failed", epoch=epoch)
        assert adapter._state == "healthy"
        assert len(reports) == count
    finally:
        await adapter.stop()


async def test_native_cleanup_failure_publishes_failed_health(config, context):
    """A worker that cannot be retired remains failed rather than apparently ready."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    session = NativeSession(config)
    adapter = ChatmailAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    original = session.stop

    async def refused(*args, **kwargs):
        """Model native ownership that cannot be safely released."""
        raise SessionError("native cleanup unavailable")

    try:
        await wait_until(lambda: reports and reports[-1].health == "healthy")
        session.started = False
        session.stop = refused
        session.wake.set()
        await wait_until(lambda: reports and reports[-1].health == "failed")
    finally:
        session.stop = original
        await adapter.stop()


async def test_owner_cancellation_before_first_execution_reports_failed(
    config, context
):
    """A task cancelled before its coroutine starts still publishes failed health."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    adapter = ChatmailAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    try:
        task = adapter._task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_until(lambda: reports and reports[-1].health == "failed")
    finally:
        await adapter.stop()


async def test_shutdown_owned_cancellation_does_not_report_failed(config, context):
    """The stop fence retires observers before cancelling the native-owner task."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    adapter = ChatmailAdapter(config, session_factory=NativeSession)
    await adapter.start(ctx)
    await adapter.stop()
    assert all(info.health != "failed" for info in reports)


@pytest.mark.parametrize(
    "connectivity, expected", [(1000, "degraded"), (4000, "healthy")]
)
async def test_startup_waits_for_native_connectivity(
    config, context, connectivity, expected
):
    """Startup cannot fabricate degraded account connectivity before the native observation."""
    ctx, _ = context
    reports = []
    ctx.report_health = reports.append
    entered, release = asyncio.Event(), asyncio.Event()
    session = NativeSession(config)
    session.connectivity = connectivity
    original = session.call

    async def call(name, *args):
        """Hold only the existing connectivity read, without another poller."""
        if name == "get_connectivity":
            entered.set()
            await release.wait()
        return await original(name, *args)

    session.call = call
    adapter = ChatmailAdapter(config, session_factory=lambda _: session)
    await adapter.start(ctx)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert reports == []
        release.set()
        await wait_until(lambda: reports and reports[-1].health == expected)
        assert reports[0].health == expected
    finally:
        release.set()
        await adapter.stop()
