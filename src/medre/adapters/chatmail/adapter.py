"""Durable ingress and deferred SMTP handoff through a dedicated profile."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from time import monotonic

from medre.core.supervision.diagnostic_contract import COMMON_DIAGNOSTIC_KEYS

from medre.core.contracts.adapter import (
    AdapterCapabilities,
    AdapterContext,
    AdapterContract,
    AdapterHandoffResult,
    AdapterInfo,
    AdapterPermanentError,
    AdapterRole,
    AdapterSendError,
)
from medre.core.contracts.delivery import (
    DeferredHandoffCompleted,
    DeferredHandoffFailed,
)
from medre.core.ingress import IngressProvenance
from medre.core.rendering.renderer import RenderingResult

from .codec import ChatmailCodec, native_id
from .config import ChatmailConfig, plain_text, sender_name
from .session import ChatmailSession, RpcCallError, SessionError
from .state import Journal, PendingLimitError

CAPABILITIES = AdapterCapabilities(
    text=True,
    title=False,
    replies="native",
    threads="fallback",
    reactions="unsupported",
    edits="unsupported",
    deletes="unsupported",
    attachments=False,
    metadata_fields=False,
    store_and_forward=True,
    direct_messages=True,
    channels=True,
    identity_encryption=False,
    presence=False,
    topic_rooms=True,
    mesh_routing=False,
    priority_delivery=False,
)


def validate_payload(
    result: RenderingResult, config: ChatmailConfig
) -> tuple[int, str, str | None, str | None, str | None]:
    """Validate the complete rendered claim before durable attempt admission."""
    payload = result.payload
    if result.target_adapter != config.adapter_id or not isinstance(payload, Mapping):
        raise AdapterPermanentError("Invalid Chatmail delivery target or payload")
    try:
        conversation = config.destination(result.target_channel)
    except ValueError:
        raise AdapterPermanentError("Unconfigured Chatmail destination") from None
    text = payload.get("text")
    if (
        type(payload.get("chat_id")) is not int
        or payload["chat_id"] != conversation
        or not {"chat_id", "text"} <= set(payload)
        or set(payload)
        - {
            "chat_id",
            "text",
            "override_sender_name",
            "reply_native_ref",
            "reply_fallback_text",
        }
        or not plain_text(text)
        or not text.strip()
        or len(text.encode("utf-8")) > config.max_text_bytes
    ):
        raise AdapterPermanentError("Invalid rendered Chatmail addressing or text")
    override = payload.get("override_sender_name")
    if "override_sender_name" in payload and (
        not config.native_sender_attribution or not sender_name(override)
    ):
        raise AdapterPermanentError("Invalid rendered Chatmail sender attribution")
    reply_ref = payload.get("reply_native_ref")
    reply_fallback = payload.get("reply_fallback_text")
    if (reply_ref is None) != (reply_fallback is None):
        raise AdapterPermanentError("Chatmail reply target and fallback must be paired")
    if reply_ref is not None and (
        not plain_text(reply_ref)
        or not reply_ref
        or not plain_text(reply_fallback)
        or not reply_fallback.strip()
        or len(reply_fallback.encode("utf-8")) > config.max_text_bytes
    ):
        raise AdapterPermanentError("Invalid rendered Chatmail native reply")
    return (
        conversation,
        text,
        override if isinstance(override, str) else None,
        reply_ref if isinstance(reply_ref, str) else None,
        reply_fallback if isinstance(reply_fallback, str) else None,
    )


def _open_journal(state_dir: str, scope: str) -> Journal:
    """Construct the durable queue as a to_thread-friendly callable."""
    return Journal(state_dir, scope)


class ChatmailAdapter(AdapterContract):
    platform = "chatmail"
    role = AdapterRole.HYBRID

    def __init__(
        self,
        config: ChatmailConfig,
        *,
        session_factory: Callable[[ChatmailConfig], ChatmailSession] = ChatmailSession,
    ) -> None:
        super().__init__()
        self.config = config.validate()
        self.adapter_id = config.adapter_id
        self._capabilities = replace(CAPABILITIES, max_text_bytes=config.max_text_bytes)
        self.ctx: AdapterContext | None = None
        self.session: ChatmailSession | None = None
        self.journal: Journal | None = None
        self.codec: ChatmailCodec | None = None
        self._session_factory: Callable[[ChatmailConfig], ChatmailSession] = (
            session_factory
        )
        self._task: asyncio.Task[None] | None = None
        self._state = "unknown"
        self._health_epoch = 0
        self._health_active = False
        self._reported_health: str | None = None
        self._profile = ""
        self._cursors: dict[int, int] = {}
        self._recovery_heads: dict[int, int] = {}
        self._stopping = False
        self._failed_ingress = 0
        self._reconnects = 0
        self._download_requests: dict[tuple[int, int], float] = {}

    async def start(self, ctx: AdapterContext) -> None:
        if self.session is not None:
            raise RuntimeError("Chatmail adapter is started")
        if (
            not self.config.chats
            or not self.config.accounts_dir
            or not self.config.state_dir
        ):
            raise AdapterPermanentError(
                "Chatmail requires a prepared profile and configured chat IDs"
            )
        if any(
            getattr(ctx, name) is None
            for name in (
                "admit_inbound",
                "load_checkpoint",
                "commit_checkpoint",
                "report_delivery_feedback",
            )
        ):
            raise AdapterPermanentError(
                "Chatmail requires durable admission, checkpoints and delivery feedback"
            )
        if ctx.adapter_id != self.adapter_id:
            raise AdapterPermanentError("Chatmail context adapter identity differs")
        self._health_epoch += 1
        health_epoch = self._health_epoch
        self._health_active = False
        self._reported_health = None
        self.ctx = ctx
        self._mark_started(ctx)
        self._stopping = False
        self._set_state("starting")
        try:
            await self._connect()
            self.journal = await asyncio.to_thread(
                _open_journal, self.config.state_dir, self._profile
            )
            self._health_active = True
            # Keep failure reporting available, but wait for native connectivity
            # before publishing an account-readiness observation.
            self._task = asyncio.create_task(
                self._run(), name=f"chatmail:{self.adapter_id}"
            )
            self._task.add_done_callback(
                lambda task: self._native_task_done(task, health_epoch)
            )
        except BaseException:
            await self.stop()
            self._set_state("failed")
            raise

    async def _connect(self) -> None:
        session = self._session_factory(self.config)
        self.session = session
        await session.start()
        account = self.config.account_id
        await session.call("stop_io_for_all_accounts")
        ctx = self.ctx
        if ctx is None or ctx.load_checkpoint is None:
            raise AdapterPermanentError("Chatmail durable checkpoints are unavailable")
        load_checkpoint = ctx.load_checkpoint
        if account not in await session.call(
            "get_all_account_ids"
        ) or not await session.call("is_configured", account):
            raise AdapterPermanentError("The selected Chatmail account is unconfigured")
        blob_dir = await session.call("get_blob_dir", account)
        if not isinstance(blob_dir, str) or not blob_dir:
            raise AdapterPermanentError("Chatmail profile identity is unavailable")
        profile = Path(blob_dir).parent.name
        if self._profile and profile != self._profile:
            raise AdapterPermanentError(
                "Chatmail native profile changed during recovery"
            )
        self._profile = profile
        self.codec = ChatmailCodec(self.config, profile)
        # Keep the previous snapshot intact until every native conversation is
        # validated and network I/O is successfully re-enabled.
        cursors: dict[int, int] = {}
        recovery_heads: dict[int, int] = {}
        for conversation in self.config.chats:
            info = await session.call("get_basic_chat_info", account, conversation)
            if (
                info.get("isContactRequest") is not False
                or info.get("isDeviceChat") is not False
                or info.get("isSelfTalk") is not False
                or info.get("chatType") not in {"Group", "Single"}
            ):
                raise AdapterPermanentError(
                    "Chatmail destinations must be accepted groups or direct conversations with other contacts"
                )
            members = await session.call("get_chat_contacts", account, conversation)
            if not any(type(member) is int and member > 9 for member in members):
                raise AdapterPermanentError(
                    "Chatmail conversations require at least one other contact"
                )
            stream = f"chatmail:{account}:{conversation}"
            checkpoint = await load_checkpoint(stream)
            ids = await session.call(
                "get_message_ids", account, conversation, False, False
            )
            recovery_heads[conversation] = max(ids, default=0)
            if checkpoint is None:
                # I/O is stopped while first-use baselines are established.
                cursors[conversation] = max(ids, default=0)
                await self._checkpoint(
                    conversation, cursors[conversation], update_cursor=False
                )
            else:
                try:
                    metadata = json.loads(checkpoint.metadata_json)
                    cursor = int(checkpoint.cursor)
                except (ValueError, TypeError):
                    raise AdapterPermanentError(
                        "Invalid Chatmail ingress checkpoint"
                    ) from None
                if metadata != {"profile_id": profile} or cursor < 0:
                    raise AdapterPermanentError(
                        "Chatmail checkpoint belongs to a different native profile"
                    )
                cursors[conversation] = cursor
        await session.call("start_io", account)
        self._cursors = cursors
        self._recovery_heads = recovery_heads
        self._download_requests.clear()
        session.wake.set()

    async def _checkpoint(
        self, conversation: int, cursor: int, *, update_cursor: bool = True
    ) -> None:
        ctx = self.ctx
        if ctx is None or ctx.commit_checkpoint is None:
            raise AdapterPermanentError("Chatmail durable checkpoints are unavailable")
        await ctx.commit_checkpoint(
            f"chatmail:{self.config.account_id}:{conversation}",
            str(cursor),
            json.dumps({"profile_id": self._profile}),
        )
        if update_cursor:
            self._cursors[conversation] = cursor

    async def _scan(self) -> None:
        first_error: Exception | None = None
        for conversation in self.config.chats:
            try:
                await self._scan_chat(conversation)
            except RpcCallError:
                # A rejected lookup/download does not invalidate the process.
                # Re-enumerate on the next interval; other chats still progress.
                if first_error is None:
                    first_error = RuntimeError(
                        "Chatmail conversation scan was rejected"
                    )
            except (asyncio.CancelledError, SessionError, AdapterPermanentError):
                raise
            except Exception as exc:
                # A failed durable admission/checkpoint must not prevent the
                # other configured chats from making forward progress.
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def _scan_chat(self, conversation: int) -> None:
        session = self.session
        codec = self.codec
        ctx = self.ctx
        if session is None or codec is None or ctx is None:
            raise SessionError("Chatmail native session is unavailable")
        ids = await session.call(
            "get_message_ids", self.config.account_id, conversation, False, False
        )
        pending = sorted(i for i in ids if i > self._cursors[conversation])
        if len(pending) > 32:
            session.wake.set()
        for message_id in pending[:32]:
            snapshot = await session.call(
                "get_message", self.config.account_id, message_id
            )
            if snapshot.get("chatId") != conversation:
                raise SessionError(
                    "Chatmail message changed conversation during admission"
                )
            # A partial text message may become complete using the same
            # native ID. Keep its cursor pending rather than lose it.
            if snapshot.get("viewType") == "Text":
                download = snapshot.get("downloadState")
                if download == "Undecipherable":
                    # Native content is terminally unreadable; do not block
                    # newer items forever waiting for an impossible download.
                    self._download_requests.pop((conversation, message_id), None)
                    await self._checkpoint(conversation, message_id)
                    continue
                if download != "Done":
                    # Available content needs an explicit native download. A
                    # successful request produces a MsgsChanged notification;
                    # never spin on every poll while it is in flight.
                    key = (conversation, message_id)
                    now = monotonic()
                    if download in {"Available", "Failure"} and (
                        key not in self._download_requests
                        or (
                            download == "Failure"
                            and now >= self._download_requests[key]
                        )
                    ):
                        # Retry failed native downloads without a wakeup-driven
                        # busy loop. InProgress remains owned by native core.
                        self._download_requests[key] = (
                            now + self.config.poll_interval_seconds
                        )
                        try:
                            await session.call(
                                "download_full_message",
                                self.config.account_id,
                                message_id,
                            )
                        except RpcCallError:
                            # No request is in flight. The scan error makes the
                            # runtime wait one polling interval before retrying.
                            self._download_requests.pop(key, None)
                            raise RuntimeError(
                                "Chatmail download was rejected"
                            ) from None
                    break
                self._download_requests.pop((conversation, message_id), None)
            event = codec.decode(snapshot, timestamp=ctx.clock())
            if event is not None:
                try:
                    provenance: IngressProvenance = (
                        "recovered"
                        if message_id <= self._recovery_heads[conversation]
                        else "live"
                    )
                    await self.admit_inbound(event, provenance)
                except Exception:
                    self._failed_ingress += 1
                    raise
            # Unsupported controls/media do not enter message history.
            # Admission and checkpoint failures leave this ID retryable.
            await self._checkpoint(conversation, message_id)
            await asyncio.sleep(0)

    async def deliver(self, result: RenderingResult) -> AdapterHandoffResult:
        conversation, text, override, reply_ref, reply_fallback = validate_payload(
            result, self.config
        )
        provenance = result.attempt_provenance
        if self.ctx is None:
            raise AdapterSendError("Chatmail adapter is not accepting deliveries")
        if provenance is None or self.ctx.report_delivery_feedback is None:
            raise AdapterPermanentError(
                "Deferred Chatmail delivery requires attempt provenance and feedback"
            )
        if (
            provenance.event_id,
            provenance.target_adapter,
            provenance.target_channel,
        ) != (result.event_id, result.target_adapter, result.target_channel):
            raise AdapterPermanentError(
                "Chatmail attempt provenance contradicts its delivery target"
            )
        if (
            self._stopping
            or self.journal is None
            or self._task is None
            or self._task.done()
        ):
            raise AdapterSendError("Chatmail adapter is not accepting deliveries")
        try:
            await asyncio.to_thread(
                self.journal.enqueue,
                provenance,
                conversation,
                text,
                self.config.max_pending,
                override,
                reply_ref,
                reply_fallback,
            )
        except PendingLimitError:
            raise AdapterSendError(
                "Chatmail durable queue is full",
                retry_after_seconds=self.config.poll_interval_seconds,
            ) from None
        if self.session is not None:
            self.session.wake.set()
        return AdapterHandoffResult(
            disposition="deferred",
            confirmation_level="local_queue",
            native_channel_id=result.target_channel,
            note="Retained in the private Chatmail queue; SMTP handoff is pending",
        )

    def _reply_id(self, reference: str) -> int | None:
        """Parse a profile-scoped local message ID without accepting aliases."""
        prefix = f"{self._profile}:"
        if not reference.startswith(prefix):
            return None
        value = reference[len(prefix) :]
        if (
            not 1 <= len(value) <= 10
            or not value.isascii()
            or not value.isdecimal()
            or value.startswith("0")
        ):
            return None
        number = int(value)
        return number if 10 <= number <= 4_294_967_295 else None

    async def _flush(self) -> None:
        session = self.session
        journal = self.journal
        ctx = self.ctx
        report = ctx.report_delivery_feedback if ctx is not None else None
        if session is None or journal is None or ctx is None or report is None:
            raise AdapterSendError("Chatmail adapter is not accepting deliveries")
        for row in await asyncio.to_thread(journal.rows):
            provenance = journal.provenance(row)
            feedback: DeferredHandoffCompleted | DeferredHandoffFailed
            if row["status"] == "queued":
                # No native handoff has occurred yet. Check deterministically
                # before committing a potentially-uncertain submission claim.
                rejected = row["chat"] not in self.config.chats
                if not rejected:
                    members = await session.call(
                        "get_chat_contacts", self.config.account_id, row["chat"]
                    )
                    rejected = not any(
                        type(member) is int and member > 9 for member in members
                    )
                if rejected:
                    await asyncio.to_thread(journal.update, row["identity"], "rejected")
                    row["status"] = "rejected"
                else:
                    # Native local IDs may be recycled in another profile/chat.
                    # Verify existence and conversation ownership while still
                    # unreserved. If it disappeared, use the immutable readable
                    # fallback prepared during rendering instead of guessing.
                    quote_id = None
                    body = row["body"]
                    reference = row["reply_ref"]
                    if reference is not None:
                        candidate = self._reply_id(reference)
                        if row["fallback_body"] is None:
                            raise AdapterPermanentError(
                                "Chatmail queued reply fallback is missing"
                            )
                        quoted = None
                        if candidate is not None:
                            try:
                                quoted = await session.call(
                                    "get_message", self.config.account_id, candidate
                                )
                            except RpcCallError:
                                pass
                        if (
                            isinstance(quoted, Mapping)
                            and type(quoted.get("id")) is int
                            and quoted["id"] == candidate
                            and quoted.get("chatId") == row["chat"]
                        ):
                            quote_id = candidate
                        else:
                            body = row["fallback_body"]
                    await asyncio.to_thread(
                        journal.update, row["identity"], "submitting"
                    )
                    # Commit the claim immediately before invoking native send.
                    # A crash or RPC rejection after this point is uncertain.
                    message = {"text": body}
                    if quote_id is not None:
                        message["quotedMessageId"] = quote_id
                    if row["sender_override"] is not None:
                        message["overrideSenderName"] = row["sender_override"]
                    try:
                        message_id = await session.call(
                            "send_msg",
                            self.config.account_id,
                            row["chat"],
                            message,
                        )
                    except RpcCallError:
                        session.wake.set()
                        continue
                    if type(message_id) is not int or message_id <= 9:
                        raise SessionError(
                            "Chatmail returned an invalid queued message ID"
                        )
                    await asyncio.to_thread(
                        journal.update, row["identity"], "pending", message_id
                    )
                    row["status"], row["native"] = "pending", message_id
            if row["status"] == "rejected":
                feedback = DeferredHandoffFailed(
                    attempt_provenance=provenance,
                    outcome="permanent_failed",
                    error="Chatmail destination is not configured or has no eligible contacts",
                )
            elif row["status"] == "expired":
                feedback = DeferredHandoffFailed(
                    attempt_provenance=provenance,
                    outcome="abandoned",
                    error="Chatmail queued attempt expired before native submission; message was not sent",
                )
            elif row["status"] in {"submitting", "abandoned"}:
                feedback = DeferredHandoffFailed(
                    attempt_provenance=provenance,
                    outcome="abandoned",
                    error="Native submission outcome is uncertain; inspect Chatmail before resending",
                )
            else:
                try:
                    snapshot = await session.call(
                        "get_message", self.config.account_id, row["native"]
                    )
                except RpcCallError:
                    await asyncio.to_thread(
                        journal.update, row["identity"], "abandoned"
                    )
                    session.wake.set()
                    continue
                if (
                    snapshot.get("id"),
                    snapshot.get("chatId"),
                    snapshot.get("fromId"),
                ) != (row["native"], row["chat"], 1):
                    raise AdapterPermanentError(
                        "Chatmail queued message identity does not match its attempt"
                    )
                state = snapshot.get("state")
                if state in {26, 28}:
                    feedback = DeferredHandoffCompleted(
                        attempt_provenance=provenance,
                        handoff=AdapterHandoffResult(
                            disposition="transport_handoff",
                            confirmation_level="remote_service",
                            native_message_id=native_id(self._profile, row["native"]),
                            native_channel_id=str(row["chat"]),
                            note="Chatmail reports SMTP acceptance; recipient delivery and reading are unconfirmed",
                        ),
                    )
                elif state == 24:
                    feedback = DeferredHandoffFailed(
                        attempt_provenance=provenance,
                        outcome="permanent_failed",
                        error="Chatmail reports terminal native send failure",
                    )
                else:
                    continue
            await report(feedback)
            await asyncio.to_thread(journal.remove, row["identity"])

    async def _run(self) -> None:
        epoch = self._health_epoch
        while not self._stopping:
            session = self.session
            if session is None:
                raise SessionError("Chatmail native session is unavailable")
            try:
                session.wake.clear()
                if not session.alive():
                    raise SessionError("Chatmail native process is unavailable")
                scan_error: Exception | None = None
                try:
                    await self._scan()
                except (asyncio.CancelledError, SessionError, AdapterPermanentError):
                    raise
                except Exception as exc:
                    scan_error = exc
                # Ingress failure must not starve already queued outbound work.
                await self._flush()
                if scan_error is not None:
                    raise scan_error
                connectivity = await session.call(
                    "get_connectivity", self.config.account_id
                )
                self._set_state(
                    "healthy" if connectivity >= 3000 else "degraded", epoch=epoch
                )
                try:
                    await asyncio.wait_for(
                        session.wake.wait(), self.config.poll_interval_seconds
                    )
                except TimeoutError:  # nosec B110 - periodic scan fallback
                    pass
            except asyncio.CancelledError:
                if self._health_active:
                    self._set_state("failed", epoch=epoch)
                raise
            except AdapterPermanentError:
                self._set_state("failed", epoch=epoch)
                return
            except SessionError:
                self._set_state("degraded", epoch=epoch)
                try:
                    await session.stop()
                except Exception:
                    self._set_state("failed", epoch=epoch)
                    return
                await asyncio.sleep(self.config.reconnect_delay_seconds)
                try:
                    await self._connect()
                    self._reconnects += 1
                except AdapterPermanentError:
                    self._set_state("failed", epoch=epoch)
                    return
                except Exception:
                    self._set_state("degraded", epoch=epoch)
                    # A reconnect may fail after stop_io or some cursors have
                    # been loaded. The next attempt must start a fresh session.
                    session = self.session
                    if session is None:
                        raise
                    try:
                        await session.stop()
                    except Exception:
                        self._set_state("failed", epoch=epoch)
                        return
            except Exception:
                # Storage/admission/feedback remains owned by its durable
                # source. Retry without discarding native or queued work.
                self._set_state("degraded", epoch=epoch)
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def stop(self, timeout: float = 5.0) -> None:
        self._health_active = False
        self._health_epoch += 1
        self._reported_health = None
        self._stopping = True
        self._set_state("stopping")
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.session is not None:
            session, self.session = self.session, None
            await session.stop(timeout=timeout)
        if self.journal is not None:
            journal, self.journal = self.journal, None
            await asyncio.to_thread(journal.close)
        self._set_state("unknown")

    def diagnostics(self) -> dict[str, Any]:
        """Expose retained health and explicit unknowns without native I/O."""
        return {
            **dict.fromkeys(sorted(COMMON_DIAGNOSTIC_KEYS)),
            "health": self._state,
            "mode": "live",
            "state": self._state,
            "failed_ingress": self._failed_ingress,
            "reconnects": self._reconnects,
            "account_id": self.config.account_id,
            "configured_chats": len(self.config.chats),
        }

    def _native_task_done(self, task: asyncio.Task[None], epoch: int) -> None:
        """Observe unexpected native-owner exit, including cancellation before first execution."""
        if not self._health_active or epoch != self._health_epoch:
            return
        if task.cancelled() or task.exception() is not None:
            self._set_state("failed", epoch=epoch)

    def _set_state(self, state: str, *, epoch: int | None = None) -> None:
        """Publish existing native-owner observations without adding polling or send authority."""
        if epoch is not None and epoch != self._health_epoch:
            return
        self._state = state
        if (
            not self._health_active
            or self.ctx is None
            or state not in {"healthy", "degraded", "failed"}
        ):
            return
        report = getattr(self.ctx, "report_health", None)
        if report is None or self._reported_health == state:
            return
        try:
            report(self._health_info())
            self._reported_health = state
        except Exception as exc:
            self.ctx.logger.warning(
                "chatmail health notification failed (%s)", type(exc).__name__
            )

    async def health_check(self) -> AdapterInfo:
        """Read the native owner's retained local health without another native call."""
        return self._health_info()

    def _health_info(self) -> AdapterInfo:
        """Use the same native-owner state for explicit probes and reports."""
        return AdapterInfo(
            self.adapter_id,
            self.platform,
            self.role,
            "0.1.0",
            self._capabilities,
            health=self._state,
        )
