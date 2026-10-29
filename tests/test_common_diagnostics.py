"""Raw and normalized diagnostics retain native health without fabricated counters."""

import json

import pytest

from medre.adapters.chatmail.adapter import ChatmailAdapter
from medre.adapters.chatmail.fake import FakeChatmailAdapter
from medre.core.supervision.diagnostic_contract import (
    COMMON_DIAGNOSTIC_KEYS,
    normalize_diagnostics,
)


def test_real_diagnostics_preserve_reported_native_health(config):
    """A healthy native owner remains healthy in the core diagnostic projection."""
    adapter = ChatmailAdapter(config)
    adapter._state = "healthy"
    raw = adapter.diagnostics()
    assert COMMON_DIAGNOSTIC_KEYS <= raw.keys()
    assert raw["health"] == "healthy"
    assert raw["mode"] == "live"
    normalized = normalize_diagnostics(raw)
    assert normalized["health"] == "healthy"
    assert normalized["mode"] == "live"
    assert normalized["transient_delivery_failures"] is None
    assert normalized["permanent_delivery_failures"] is None
    json.dumps(raw)


@pytest.mark.parametrize("fake", [False, True])
async def test_unstarted_diagnostics_match_explicit_health(config, fake):
    """SDK-free and native instances expose the same health value without native I/O."""
    adapter = (
        FakeChatmailAdapter(adapter_id=config.adapter_id)
        if fake
        else ChatmailAdapter(config)
    )
    info = await adapter.health_check()
    raw = adapter.diagnostics()
    assert COMMON_DIAGNOSTIC_KEYS <= raw.keys()
    assert raw["health"] == info.health
    assert raw["mode"] == ("fake" if fake else "live")
    assert raw["connected"] is None
    assert raw["reconnecting"] is None
    assert raw["reconnect_attempts"] is None
    assert raw["last_error"] is None
    assert raw["transient_delivery_failures"] is None
    assert raw["permanent_delivery_failures"] is None
    json.dumps(raw)
