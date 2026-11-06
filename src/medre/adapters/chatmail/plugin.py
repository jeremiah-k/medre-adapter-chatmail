"""Lazy discovery through released MEDRE provider contracts."""

from dataclasses import replace
from typing import Any

from medre import adapter_spec as registration
from medre.adapter_spec import AdapterSpec, SymbolRef

_SETUP_TITLE = "Provision a Chatmail profile"

_SETUP_SUMMARY = (
    "Provisioning proceeds through explicit stages with the provider's own "
    "commands: create the profile, confirm the configured account, accept "
    "conversations, and discover chat IDs. Offline configuration and "
    "verification then continue with core's own commands (medre config check, "
    "medre routes plan). A deferred local_queue result records only private "
    "journal retention; relay-side SMTP acceptance arrives later through "
    "delivery feedback. Recipient delivery and "
    "reading are never proven by provisioning."
)

# Ordered exactly like the README's staged journey. Commands are verbatim
# provider contributions under the core-owned `medre adapter chatmail`
# namespace; core renders them informationally and never executes them.
_SETUP_STEPS = (
    (
        "medre adapter chatmail init --adapter mail",
        "Create or configure the private Chatmail profile from the relay's "
        "private QR payload.",
        "A completed init proves only that the native core reports this "
        "account configured. It is not relay acceptance, delivery, or read "
        "evidence, grants no send authority, and queues no bridge message; relay-side "
        "limits surface only through later delivery feedback.",
    ),
    (
        "medre adapter chatmail accounts --adapter mail",
        "List local account IDs and their configured state without exposing "
        "credentials.",
        "A completed accounts listing proves only what the local profile "
        "reports about configured state. It is not relay acceptance, "
        "delivery, or read evidence and grants no send authority.",
    ),
    (
        "medre adapter chatmail join --adapter mail",
        "Accept one contact or group invitation by completing the native "
        "secure join and waiting for native send readiness.",
        "A joined chat proves that this conversation is accepted and "
        "natively sendable. It does not prove that bridge messages reach "
        "their recipients: it is not relay acceptance, delivery, or read "
        "evidence and grants no send authority.",
    ),
    (
        "medre adapter chatmail chats --adapter mail --groups --json",
        "List the profile's group chat IDs and names for route configuration.",
        "Discovered chat IDs are local conversation identifiers for "
        "configuration only. Completing discovery is not relay acceptance, "
        "delivery, or read evidence and grants no send authority.",
    ),
)


def _setup_instructions() -> Any | None:
    """Build the typed journey only when the running core provides the seam."""
    instructions_type = getattr(registration, "NativeSetupInstructions", None)
    step_type = getattr(registration, "NativeSetupStep", None)
    if (
        instructions_type is None
        or step_type is None
        or not hasattr(AdapterSpec, "native_setup_instructions")
    ):
        return None
    return instructions_type(
        title=_SETUP_TITLE,
        summary=_SETUP_SUMMARY,
        steps=tuple(
            step_type(command=command, purpose=purpose, evidence_note=evidence_note)
            for command, purpose, evidence_note in _SETUP_STEPS
        ),
    )


def adapter_spec() -> AdapterSpec:
    journey = _setup_instructions()
    spec = AdapterSpec(
        api_version=1,
        transport="chatmail",
        config=SymbolRef("medre.adapters.chatmail.config", "ChatmailConfig"),
        runtime_config=None,
        adapter=SymbolRef("medre.adapters.chatmail.adapter", "ChatmailAdapter"),
        fake_adapter=SymbolRef("medre.adapters.chatmail.fake", "FakeChatmailAdapter"),
        renderer_factory=SymbolRef(
            "medre.adapters.chatmail.renderer", "build_renderer"
        ),
        runtime_config_preparer=SymbolRef(
            "medre.adapters.chatmail.config", "prepare_config"
        ),
        dependency_probe=SymbolRef("medre.adapters.chatmail.compat", "HAS_CHATMAIL"),
        distribution="deltachat-rpc-client",
        import_names=("deltachat_rpc_client",),
        sdk_import_roots=("deltachat_rpc_client", "deltachat_rpc_server"),
        native_namespace_reader=SymbolRef(
            "medre.adapters.chatmail.metadata", "native_namespace"
        ),
        versioned_namespace_reader=SymbolRef(
            "medre.adapters.chatmail.metadata", "versioned"
        ),
        attribution_projector=SymbolRef(
            "medre.adapters.chatmail.metadata", "project_attribution"
        ),
        sample_config=SymbolRef("medre.adapters.chatmail.sample", "sample_config"),
        stopped_state_declaration=SymbolRef(
            "medre.adapters.chatmail.stopped_declaration", "declaration"
        ),
        cli_register=SymbolRef("medre.adapters.chatmail.cli_contrib", "register_cli"),
        cli_dispatch=SymbolRef("medre.adapters.chatmail.cli_contrib", "dispatch_cli"),
        support_endpoint_fields=("account_id", "chats"),
        support_secret_fields=("accounts_dir", "state_dir"),
    )
    return replace(spec, native_setup_instructions=journey) if journey else spec
