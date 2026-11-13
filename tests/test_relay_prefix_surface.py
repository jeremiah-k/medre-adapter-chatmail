"""Relay prefix surface: template validation and outbound body application."""

from dataclasses import replace

import pytest

from medre.adapters.chatmail.codec import ChatmailCodec
from medre.adapters.chatmail.config import ChatmailConfig
from medre.adapters.chatmail.renderer import ChatmailRenderer
from medre.core.rendering.renderer import RenderingContext

from conftest import NOW, snapshot


def direct_context(origin=None):
    return RenderingContext(
        target_adapter="mail",
        target_channel="10",
        target_platform="chatmail",
        delivery_strategy="direct",
        source_origin_label=origin,
    )


async def relayed_body(config, ctx):
    """Decode the fixture snapshot and return (selected body, outbound text)."""
    event = ChatmailCodec(config, "profile").decode(snapshot(), timestamp=NOW)
    result = await ChatmailRenderer({"mail": config}).render(event, ctx)
    return event.payload["body"], result.payload["text"]


async def test_default_relay_prefix_reaches_outbound_body(config):
    """The stock template shapes attribution and prepends it to the body."""
    config = replace(config, relay_prefix=ChatmailConfig("probe").relay_prefix)
    body, outbound = await relayed_body(config, direct_context(origin="Mesh"))
    assert outbound == "[Mesh] Alice: " + body


async def test_empty_relay_prefix_yields_bare_body(config):
    """The empty template disables prefixing without touching the body."""
    body, outbound = await relayed_body(config, direct_context(origin="Mesh"))
    assert outbound == body


async def test_custom_relay_prefix_resolves_both_placeholders(config):
    """Custom templates resolve {origin_label} and {sender} from attribution."""
    config = replace(config, relay_prefix="{sender} via {origin_label}: ")
    body, outbound = await relayed_body(config, direct_context(origin="Mesh"))
    assert outbound == "Alice via Mesh: " + body


def test_relay_prefix_default_template_is_the_documented_shape():
    config = ChatmailConfig("probe").validate()
    assert config.relay_prefix == "[{origin_label}] {sender}: "


@pytest.mark.parametrize(
    "updates",
    [
        {"relay_prefix": "> {sender}: "},
        {"relay_prefix": ""},
    ],
)
def test_relay_prefix_accepts_templates_and_disablement(config, updates):
    validated = replace(config, **updates).validate()
    assert validated.relay_prefix == updates["relay_prefix"]


@pytest.mark.parametrize(
    "updates",
    [
        {"relay_prefix": True},
        {"relay_prefix": 5},
        {"relay_prefix": None},
    ],
)
def test_relay_prefix_refuses_non_text(config, updates):
    with pytest.raises(ValueError, match="relay_prefix must be valid text"):
        replace(config, **updates).validate()
