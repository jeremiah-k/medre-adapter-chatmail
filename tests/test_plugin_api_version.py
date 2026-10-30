"""Provider declares the exact prerelease SDK-free registration contract."""

from medre.adapter_spec import ADAPTER_API_VERSION
from medre.adapters.chatmail.plugin import adapter_spec


def test_provider_declares_current_api_version():
    spec = adapter_spec()
    assert type(spec.api_version) is int
    assert spec.api_version == ADAPTER_API_VERSION
