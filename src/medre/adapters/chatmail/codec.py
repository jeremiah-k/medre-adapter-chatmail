"""Convert complete native text snapshots into stable canonical evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from medre.core.events.canonical import CanonicalEvent, EventRelation, NativeRef
from medre.core.events.metadata import EventMetadata, NativeMetadata, TransportMetadata

from .config import ChatmailConfig, plain_text, sender_name


def native_id(profile: str, message_id: int) -> str:
    """Local message IDs belong to a persistent Chatmail profile namespace."""
    return f"{profile}:{message_id}"


def contact_address(value: object) -> str | None:
    """Accept a bounded native contact address without rewriting it.

    The address is recorded as observed platform identity, never used as a
    contact lookup key. Absent, malformed, padded or whitespace-bearing
    values are omitted rather than guessed.
    """
    if not isinstance(value, str) or not plain_text(value):
        return None
    if not value or value != value.strip() or any(char.isspace() for char in value):
        return None
    return value if len(value) <= 1024 else None


def native_seconds(value: object) -> int | None:
    """Accept a positive native unix-seconds timestamp; omit anything else."""
    return value if type(value) is int and value > 0 else None


def chat_facts(info: Mapping[str, Any]) -> dict[str, Any]:
    """Validated native conversation facts observed at connection setup.

    The pinned RPC ``BasicChat`` shape supplies ``name``, ``chatType`` and
    ``isEncrypted`` (key-contact protection); anything else, including a
    missing or malformed value, is omitted rather than invented.
    """
    facts: dict[str, Any] = {}
    if info.get("chatType") in {"Group", "Single"}:
        facts["chat_type"] = info["chatType"]
    name = info.get("name")
    if (
        isinstance(name, str)
        and plain_text(name)
        and bool(name.strip())
        and name.isprintable()
        and len(name.encode("utf-8")) <= 1024
    ):
        facts["chat_name"] = name
    protected = info.get("isEncrypted")
    if type(protected) is bool:
        facts["chat_protected"] = protected
    return facts


class ChatmailCodec:
    def __init__(
        self,
        config: ChatmailConfig,
        profile: str,
        chat_facts: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.chat_facts: Mapping[int, Mapping[str, Any]] = dict(chat_facts or {})

    def decode(
        self, snapshot: Mapping[str, Any], *, timestamp: datetime
    ) -> CanonicalEvent | None:
        message_id = snapshot.get("id")
        conversation = snapshot.get("chatId")
        sender_id = snapshot.get("fromId")
        text = snapshot.get("text")
        sender = snapshot.get("sender")
        if (
            type(message_id) is not int
            or message_id <= 9
            or type(conversation) is not int
            or conversation not in self.config.chats
            or type(sender_id) is not int
            or sender_id <= 9
            or snapshot.get("state") not in {10, 13, 16}
            or snapshot.get("downloadState") != "Done"
            or snapshot.get("viewType") != "Text"
            or snapshot.get("isInfo") is not False
            or snapshot.get("isEdited") is not False
            or not isinstance(sender, Mapping)
            or sender.get("isBlocked") is not False
            or not plain_text(text)
            or not text.strip()
            or len(text.encode("utf-8")) > self.config.max_text_bytes
        ):
            return None
        channel = str(conversation)
        sent_timestamp = snapshot.get("timestamp")
        if type(sent_timestamp) is int and sent_timestamp > 0:
            try:
                timestamp = datetime.fromtimestamp(sent_timestamp, tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                return None
        reference = native_id(self.profile, message_id)
        identity = json.dumps([self.config.adapter_id, self.profile, message_id])
        relations: tuple[EventRelation, ...] = ()
        quote = snapshot.get("quote")
        if isinstance(quote, Mapping) and quote.get("kind") == "WithMessage":
            quoted_id, quoted_chat = quote.get("messageId"), quote.get("chatId")
            if type(quoted_id) is int and quoted_id > 9 and quoted_chat == conversation:
                relations = (
                    EventRelation(
                        relation_type="reply",
                        target_event_id=None,
                        target_native_ref=NativeRef(
                            self.config.adapter_id,
                            channel,
                            native_id(self.profile, quoted_id),
                        ),
                        key=None,
                        fallback_text=None,
                    ),
                )
        label = sender.get("displayName")
        if not plain_text(label) or not label.strip() or not label.isprintable():
            label = f"contact {sender_id}"
        presentation = snapshot.get("overrideSenderName")
        claimed_sender = (
            {"sender_override": presentation} if sender_name(presentation) else {}
        )
        encrypted_and_signed = snapshot.get("showPadlock") is True
        native: dict[str, Any] = {
            "schema_version": 1,
            "profile_id": self.profile,
            "account_id": self.config.account_id,
            "chat_id": conversation,
            "message_id": message_id,
            "contact_id": sender_id,
            "sender_label": label,
            **claimed_sender,
            "encrypted_and_signed": encrypted_and_signed,
        }
        address = contact_address(sender.get("address"))
        if address is not None:
            native["sender_address"] = address
        timestamp_sent = native_seconds(sent_timestamp)
        if timestamp_sent is not None:
            native["timestamp_sent"] = timestamp_sent
        timestamp_received = native_seconds(snapshot.get("receivedTimestamp"))
        if timestamp_received is not None:
            native["timestamp_received"] = timestamp_received
        conversation_facts = self.chat_facts.get(conversation)
        if conversation_facts:
            native.update(conversation_facts)
        # Explicitly opted-in public projection for core's transport-neutral
        # bridge context; arbitrary native data never crosses the boundary.
        native["public_facts"] = {"encrypted_and_signed": encrypted_and_signed}
        return CanonicalEvent(
            event_id="chatmail:" + hashlib.sha256(identity.encode()).hexdigest(),
            event_kind="message.created",
            schema_version=1,
            timestamp=timestamp,
            source_adapter=self.config.adapter_id,
            source_transport_id=f"{self.profile}:contact:{sender_id}",
            source_channel_id=channel,
            parent_event_id=None,
            lineage=(),
            relations=relations,
            payload={"body": text},
            source_native_ref=NativeRef(self.config.adapter_id, channel, reference),
            metadata=EventMetadata(
                transport=TransportMetadata(protocol="chatmail", substrate="ip"),
                native=NativeMetadata(data={"chatmail": native}),
            ),
        )
