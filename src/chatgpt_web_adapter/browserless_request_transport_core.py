from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from time import monotonic, sleep
import threading
from typing import Any, Iterator, Mapping
import uuid

from .auth import CHAT_URL
from .client import ChatGPTWebClient
from .exceptions import RequestError, WebChatAdapterError
from .product_capabilities import (
    APPROVALS,
    CANONICAL_READBACK,
    CONTINUATION,
    CONVERSATION_ATTACH,
    CONVERSATION_BRANCHING,
    CONVERSATION_READ,
    CONVERSATION_STATUS,
    FILES,
    IMAGES,
    MODEL_PRESERVATION,
    MODEL_SELECTION,
    MULTIMODAL_CONTINUATION,
    NEW_CHAT,
    ORDINARY_CHATGPT_PRODUCT_SEMANTICS,
    PRODUCT_CAPABILITY_NAMES,
    PRODUCT_MEMORY_PERSONALIZATION,
    REASONING_PRESERVATION,
    REASONING_SELECTION,
    STREAMING,
    TEMPORARY_CHAT,
    TEXT_TURNS,
    TOOLS_CONNECTORS,
    WEB_SEARCH,
    CapabilityOwner,
    CapabilityState,
    ProductCapabilities,
    ProductCapability,
)
from .product_transport import (
    BROWSERLESS_REQUEST_PRODUCT_TRANSPORT,
    ConversationInput,
    EventCallback,
    ProductRuntimeExecution,
    ProductRuntimeHealth,
    TokenCallback,
    require_canonical_conversation_client,
)
from .revision_safe_streaming_pr8_9 import RevisionSafeTextAccumulator
from .sentinel_requirements import (
    SENTINEL_FINALIZE_PATH,
    SENTINEL_PREPARE_PATH,
    build_sentinel_prepare_headers,
)
from .types import AttachedConversation, ChatConversation, ChatMessage, ChatResponse
from .web_session import _sync_device_header, suppress_web_session_debug_trace


_BROWSERLESS_WRITE_PLANE = "DIRECT_REQUEST_EXPERIMENTAL"
_CANONICAL_READ_PLANE = "CANONICAL"
_CANONICAL_SESSION_PLANE = "CANONICAL_SESSION"
_CONVERSATION_PREPARE_PATH = "/backend-api/f/conversation/prepare"
_CONVERSATION_WRITE_PATH = "/backend-api/f/conversation"
_BROWSERLESS_BINDING_OWNER: ContextVar[object | None] = ContextVar(
    "browserless_prepared_binding_owner",
    default=None,
)
_SHARED_WRITE_LOCK_GUARD = threading.Lock()
_SHARED_WRITE_LOCK_ATTR = "_cwa_browserless_request_write_lock"
_EPHEMERAL_WRITE_HEADERS = frozenset(
    {
        "x-conduit-token",
        "openai-sentinel-chat-requirements-token",
        "openai-sentinel-proof-token",
        "openai-sentinel-turnstile-token",
    }
)
_FORBIDDEN_BROWSERLESS_PROTECTION_HEADERS = frozenset(
    {
        "openai-sentinel-proof-token",
        "openai-sentinel-turnstile-token",
    }
)

_PREWRITE_REQUEST_STAGES = frozenset(
    {
        "browserless_provider_guard",
        "browserless_sentinel_prepare",
        "browserless_sentinel_finalize",
        "browserless_prepared_write_binding",
        "browserless_write_queue",
        "browserless_write_deadline",
        "canonical_attach",
        "conversation_prepare",
        "web_session_bootstrap",
    }
)
_EXPLICIT_POSTWRITE_REQUEST_STAGES = frozenset(
    {
        "conversation_stream",
        "conversation_write",
    }
)
_REQUIRED_PREPARE_KEYS = frozenset(
    {"persona", "prepare_token", "proofofwork", "so", "turnstile"}
)
_REQUIRED_FINALIZE_KEYS = frozenset({"persona", "token", "expire_after", "expire_at"})
_NON_CHALLENGE_PREPARE_KEYS = frozenset({"persona", "prepare_token"})

_BROWSERLESS_CAPABILITY_STATES: dict[str, CapabilityState] = {
    TEXT_TURNS: CapabilityState.AVAILABLE,
    NEW_CHAT: CapabilityState.AVAILABLE,
    CONTINUATION: CapabilityState.AVAILABLE,
    CANONICAL_READBACK: CapabilityState.AVAILABLE,
    CONVERSATION_ATTACH: CapabilityState.AVAILABLE,
    CONVERSATION_READ: CapabilityState.AVAILABLE,
    CONVERSATION_STATUS: CapabilityState.AVAILABLE,
    STREAMING: CapabilityState.AVAILABLE,
    IMAGES: CapabilityState.UNIMPLEMENTED,
    FILES: CapabilityState.UNKNOWN,
    WEB_SEARCH: CapabilityState.UNKNOWN,
    TEMPORARY_CHAT: CapabilityState.UNKNOWN,
    MODEL_SELECTION: CapabilityState.UNKNOWN,
    MODEL_PRESERVATION: CapabilityState.UNKNOWN,
    REASONING_SELECTION: CapabilityState.UNKNOWN,
    REASONING_PRESERVATION: CapabilityState.UNKNOWN,
    PRODUCT_MEMORY_PERSONALIZATION: CapabilityState.UNKNOWN,
    TOOLS_CONNECTORS: CapabilityState.UNKNOWN,
    APPROVALS: CapabilityState.UNIMPLEMENTED,
    CONVERSATION_BRANCHING: CapabilityState.UNKNOWN,
    MULTIMODAL_CONTINUATION: CapabilityState.UNIMPLEMENTED,
}

_BROWSERLESS_CAPABILITY_OWNERS: dict[str, CapabilityOwner] = {
    CANONICAL_READBACK: CapabilityOwner.CANONICAL,
    CONVERSATION_ATTACH: CapabilityOwner.CANONICAL,
    CONVERSATION_READ: CapabilityOwner.CANONICAL,
    CONVERSATION_STATUS: CapabilityOwner.CANONICAL,
    PRODUCT_MEMORY_PERSONALIZATION: CapabilityOwner.PRODUCT,
}

_BROWSERLESS_CAPABILITY_EVIDENCE: dict[str, str] = {
    TEXT_TURNS: (
        "PR9.1 current two-phase Sentinel preflight plus current conversation "
        "prepare/conduit write path; protected sessions fail closed before mutation"
    ),
    NEW_CHAT: (
        "PR9.1 prepared direct-request path can create a conversation when current "
        "server policy admits an unprotected write"
    ),
    CONTINUATION: (
        "PR9.1 canonical attach resolves current parent before prepared direct continuation"
    ),
    CANONICAL_READBACK: (
        "PR9.1 successful writes require canonical completed status and canonical assistant text"
    ),
    CONVERSATION_ATTACH: "shared canonical ChatGPTWebClient attach surface",
    CONVERSATION_READ: "shared canonical ChatGPTWebClient message-read surface",
    CONVERSATION_STATUS: "shared canonical ChatGPTWebClient status surface",
    STREAMING: (
        "PR9.1 direct SSE tokens are provisional revision-safe deltas finalized by canonical readback"
    ),
    IMAGES: "PR9.1 browserless transport is text-only; rich input is deferred to PR9.2",
    TEMPORARY_CHAT: "browserless Temporary product semantics are not yet proven",
    MODEL_SELECTION: "browserless product-profile equivalence is not yet proven",
    REASONING_SELECTION: "browserless product-profile equivalence is not yet proven",
    APPROVALS: "PR9.1 browserless transport has no approval continuation surface",
    MULTIMODAL_CONTINUATION: "PR9.1 browserless transport is text-only",
}


def _build_browserless_capabilities() -> ProductCapabilities:
    return ProductCapabilities.from_entries(
        transport=BROWSERLESS_REQUEST_PRODUCT_TRANSPORT,
        product_semantics=ORDINARY_CHATGPT_PRODUCT_SEMANTICS,
        entries=(
            ProductCapability(
                name=name,
                state=_BROWSERLESS_CAPABILITY_STATES[name],
                owner=_BROWSERLESS_CAPABILITY_OWNERS.get(name, CapabilityOwner.TRANSPORT),
                evidence=_BROWSERLESS_CAPABILITY_EVIDENCE.get(name),
            )
            for name in PRODUCT_CAPABILITY_NAMES
        ),
    )


_BROWSERLESS_CAPABILITIES = _build_browserless_capabilities()


def _shared_browserless_write_lock(client: Any) -> Any:
    """Return one re-entrant browserless mutation lock per canonical client."""

    instance_dict = getattr(client, "__dict__", None)
    if not isinstance(instance_dict, dict):
        return threading.RLock()
    with _SHARED_WRITE_LOCK_GUARD:
        lock = instance_dict.get(_SHARED_WRITE_LOCK_ATTR)
        if lock is None:
            lock = threading.RLock()
            instance_dict[_SHARED_WRITE_LOCK_ATTR] = lock
        elif not callable(getattr(lock, "acquire", None)) or not callable(
            getattr(lock, "release", None)
        ):
            raise TypeError("browserless shared-client write lock state is invalid")
        return lock


def _strip_ephemeral_write_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Remove stale one-shot Sentinel/conduit credentials from inherited headers."""

    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).strip().lower() not in _EPHEMERAL_WRITE_HEADERS
        and value is not None
    }


class BrowserlessRequestTransportError(WebChatAdapterError):
    """Structured browserless failure with explicit write-ambiguity semantics."""

    def __init__(
        self,
        message: str,
        *,
        request_stage: str,
        status_code: int | None = None,
        endpoint: str | None = None,
        write_may_have_been_submitted: bool,
        reconciliation_required: bool,
    ) -> None:
        self.request_stage = request_stage
        self.status_code = status_code
        self.endpoint = endpoint
        self.write_may_have_been_submitted = bool(write_may_have_been_submitted)
        self.reconciliation_required = bool(reconciliation_required)
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": str(self),
            "request_stage": self.request_stage,
            "status_code": self.status_code,
            "endpoint": self.endpoint,
            "write_may_have_been_submitted": self.write_may_have_been_submitted,
            "reconciliation_required": self.reconciliation_required,
        }


class BrowserlessChallengeBoundaryError(BrowserlessRequestTransportError):
    """Current product policy requires browser-bound challenge evidence."""

    def __init__(
        self,
        challenges: tuple[str, ...],
        *,
        status_code: int | None = None,
        request_stage: str = "browserless_sentinel_prepare",
    ) -> None:
        normalized = tuple(
            sorted(
                {
                    item.strip().lower()
                    for item in challenges
                    if isinstance(item, str) and item.strip()
                }
            )
        )
        self.challenges = normalized
        detail = ", ".join(normalized) if normalized else "unknown-protection"
        endpoint = (
            SENTINEL_PREPARE_PATH
            if request_stage == "browserless_sentinel_prepare"
            else SENTINEL_FINALIZE_PATH
        )
        super().__init__(
            "BROWSERLESS_CHALLENGE_BOUNDARY: protected ChatGPT product write requires "
            f"challenge evidence ({detail}); browserless transport will not solve, synthesize, "
            "emulate, replay, or fall back to a browser",
            request_stage=request_stage,
            status_code=status_code,
            endpoint=endpoint,
            write_may_have_been_submitted=False,
            reconciliation_required=False,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["challenges"] = list(self.challenges)
        payload["challenge_bypass_attempted"] = False
        return payload


class BrowserlessProtocolDriftError(BrowserlessRequestTransportError):
    """Current private web protocol no longer matches bounded PR9.1 assumptions."""

    def __init__(
        self,
        message: str,
        *,
        request_stage: str = "browserless_sentinel_prepare",
        endpoint: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(
            f"BROWSERLESS_PROTOCOL_DRIFT: {message}",
            request_stage=request_stage,
            endpoint=endpoint,
            status_code=status_code,
            write_may_have_been_submitted=False,
            reconciliation_required=False,
        )


@dataclass(frozen=True)
class BrowserlessRequestObservation:
    transport: str
    write_plane: str
    requirements_persona: str | None
    requirements_token_present: bool
    protected_challenges: tuple[str, ...]
    sentinel_protocol: str
    conversation_prepare_protocol: str
    canonical_status: str
    canonical_message_id: str | None
    reconciliation: str
    stream_observation_count: int
    stream_revision_count: int
    stream_delta_count: int
    stream_delivery_incomplete: bool
    automatic_write_retry: bool = False
    fallback_transport: str | None = None
    experimental: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["protected_challenges"] = list(self.protected_challenges)
        return payload


def _message_text(message: ChatMessage) -> str | None:
    if message.role != "assistant" or not isinstance(message.text, str):
        return None
    return message.text


def _latest_assistant(messages: list[ChatMessage]) -> ChatMessage | None:
    for message in reversed(messages):
        if _message_text(message) is not None:
            return message
    return None


def _required_nonempty_text(
    value: Any,
    *,
    field: str,
    stage: str,
    endpoint: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrowserlessProtocolDriftError(
            f"{field} is missing or empty",
            request_stage=stage,
            endpoint=endpoint,
        )
    return value.strip()


def _validate_required_descriptors(
    data: Mapping[str, Any],
    *,
    stage: str,
    endpoint: str,
) -> tuple[str, ...]:
    """Validate known and future mapping descriptors without permissive fallback."""

    for name in ("proofofwork", "so", "turnstile"):
        value = data.get(name)
        if not isinstance(value, Mapping):
            raise BrowserlessProtocolDriftError(
                f"{name} descriptor is missing or is not an object",
                request_stage=stage,
                endpoint=endpoint,
            )
        if not isinstance(value.get("required"), bool):
            raise BrowserlessProtocolDriftError(
                f"{name}.required is not boolean",
                request_stage=stage,
                endpoint=endpoint,
            )

    required: set[str] = set()
    for raw_name, value in data.items():
        normalized_name = str(raw_name).strip().lower() or "unknown"
        if normalized_name in _NON_CHALLENGE_PREPARE_KEYS:
            continue
        if not isinstance(value, Mapping):
            continue
        if "required" not in value:
            raise BrowserlessProtocolDriftError(
                f"{normalized_name}.required is missing",
                request_stage=stage,
                endpoint=endpoint,
            )
        required_flag = value.get("required")
        if not isinstance(required_flag, bool):
            raise BrowserlessProtocolDriftError(
                f"{normalized_name}.required is not boolean",
                request_stage=stage,
                endpoint=endpoint,
            )
        if required_flag:
            required.add(normalized_name)
    return tuple(sorted(required))


class BrowserlessRequestTransport:
    """Experimental current-protocol direct-request transport.

    The transport uses current two-phase Sentinel prepare/finalize and current
    conversation prepare/conduit sequencing. It only proceeds when Sentinel says
    no challenge evidence is required. It never generates or replays protected
    evidence, starts Chrome, retries an ambiguous write, or falls back to another
    transport/protocol.
    """

    transport_id = BROWSERLESS_REQUEST_PRODUCT_TRANSPORT

    def __init__(self, canonical_client: Any) -> None:
        self.canonical_client = require_canonical_conversation_client(canonical_client)
        self.client = canonical_client
        for name in ("send", "_json_request", "_build_headers"):
            if not callable(getattr(self.client, name, None)):
                raise TypeError(
                    "browserless request transport requires a ChatGPTWebClient-compatible "
                    f"direct-request surface; missing callable {name}()"
                )
        if not isinstance(getattr(self.client, "base_headers", None), Mapping):
            raise TypeError("browserless direct-request client must expose base_headers")
        if getattr(self.client, "auth", None) is None:
            raise TypeError("browserless direct-request client must expose auth state")
        if self._challenge_provider_configured():
            raise ValueError(
                "browserless request transport forbids configured Sentinel/browser challenge providers"
            )
        self._direct_send = self._resolve_direct_send()
        self._write_lock = _shared_browserless_write_lock(self.client)

    def _challenge_provider_configured(self) -> bool:
        return callable(getattr(self.client, "_sentinel_challenge_provider", None)) or callable(
            getattr(self.client, "_sentinel_bundle_provider", None)
        )

    def _assert_execution_provider_boundary(self) -> None:
        if self._challenge_provider_configured():
            raise BrowserlessRequestTransportError(
                "browserless request transport refuses execution while a Sentinel/browser "
                "challenge provider is configured",
                request_stage="browserless_provider_guard",
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )

    @staticmethod
    def _remaining_before_write(
        deadline: float,
        *,
        request_stage: str,
        endpoint: str | None = None,
    ) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BrowserlessRequestTransportError(
                "browserless request deadline expired before conversation mutation",
                request_stage=request_stage,
                endpoint=endpoint,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )
        return remaining

    @staticmethod
    def _remaining_stream_deadline(
        deadline: float,
        *,
        write_started: bool,
    ) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BrowserlessRequestTransportError(
                "browserless total deadline expired during direct stream/recovery",
                request_stage=("conversation_stream" if write_started else "conversation_prepare"),
                write_may_have_been_submitted=write_started,
                reconciliation_required=write_started,
            )
        return remaining

    def _resolve_direct_send(self) -> Any:
        if isinstance(self.client, ChatGPTWebClient):
            # Package installation wraps ChatGPTWebClient.send with Sentinel and
            # diagnostic policy. Browserless intentionally bypasses that class-level
            # Sentinel-provider wrapper and supplies its own already-classified,
            # challenge-free prepared-turn context below.
            from . import _original_send

            return _original_send.__get__(self.client, type(self.client))
        return self.client.send

    def health(self, conversation: ConversationInput = None) -> ProductRuntimeHealth:
        if conversation is None:
            return ProductRuntimeHealth(
                transport=self.transport_id,
                ready=True,
                reason="BROWSERLESS_REQUEST_READY_SENTINEL_PREFLIGHT_PENDING",
                conversation_id=None,
                canonical_status=None,
                canonical_read_checked=False,
                read_plane=_CANONICAL_READ_PLANE,
                session_plane=_CANONICAL_SESSION_PLANE,
                write_plane=_BROWSERLESS_WRITE_PLANE,
                automatic_write_retry=False,
                fallback_transport=None,
            )
        try:
            status = self.canonical_client.get_status(conversation)
        except Exception as error:
            return ProductRuntimeHealth(
                transport=self.transport_id,
                ready=False,
                reason=f"CANONICAL_STATUS_UNAVAILABLE:{type(error).__name__}",
                conversation_id=self._conversation_id_hint(conversation),
                canonical_status=None,
                canonical_read_checked=True,
                read_plane=_CANONICAL_READ_PLANE,
                session_plane=_CANONICAL_SESSION_PLANE,
                write_plane=_BROWSERLESS_WRITE_PLANE,
                automatic_write_retry=False,
                fallback_transport=None,
            )
        status_value = getattr(status, "status", None)
        return ProductRuntimeHealth(
            transport=self.transport_id,
            ready=status_value == "completed",
            reason=(
                "BROWSERLESS_REQUEST_CONTINUATION_READY_SENTINEL_PREFLIGHT_PENDING"
                if status_value == "completed"
                else f"CANONICAL_CONVERSATION_NOT_COMPLETED:{status_value or 'unknown'}"
            ),
            conversation_id=self._conversation_id_hint(conversation),
            canonical_status=status_value if isinstance(status_value, str) else None,
            canonical_read_checked=True,
            read_plane=_CANONICAL_READ_PLANE,
            session_plane=_CANONICAL_SESSION_PLANE,
            write_plane=_BROWSERLESS_WRITE_PLANE,
            automatic_write_retry=False,
            fallback_transport=None,
        )

    def capabilities(self) -> ProductCapabilities:
        return _BROWSERLESS_CAPABILITIES

    def send_text(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
    ) -> ChatResponse:
        return self._execute(
            text,
            conversation=conversation,
            timeout=timeout,
            poll_interval=poll_interval,
            on_token=on_token,
            on_event=on_event,
        ).response

    def send_text_observed(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
    ) -> ProductRuntimeExecution:
        return self._execute(
            text,
            conversation=conversation,
            timeout=timeout,
            poll_interval=poll_interval,
            on_token=on_token,
            on_event=on_event,
        )

    def governance(self) -> dict[str, Any]:
        return {
            "product_semantics": ORDINARY_CHATGPT_PRODUCT_SEMANTICS,
            "write_plane": _BROWSERLESS_WRITE_PLANE,
            "read_plane": _CANONICAL_READ_PLANE,
            "session_plane": _CANONICAL_SESSION_PLANE,
            "canonical_readback_required": True,
            "automatic_write_retry": False,
            "fallback_transport": None,
            "legacy_direct_write_fallback": False,
            "ambiguous_write_requires_reconciliation": True,
            "incremental_observation_is_canonical_finality": False,
            "browserless_request_transport_experimental": True,
            "browserless_sentinel_protocol": "TWO_PHASE_PREPARE_FINALIZE",
            "browserless_conversation_write_protocol": "PREPARE_CONDUIT_FINAL_WRITE",
            "browserless_legacy_single_step_requirements_fallback": False,
            "browserless_legacy_unprepared_conversation_write_fallback": False,
            "browserless_requirements_preflight": "UNPROTECTED_TWO_PHASE_ONLY",
            "browserless_challenge_boundary": "FAIL_CLOSED_BEFORE_WRITE",
            "browserless_shared_client_binding_scope": "EXECUTION_CONTEXT",
            "browserless_shared_client_write_serialization": "PER_CANONICAL_CLIENT",
            "browserless_timeout_scope": "EXECUTION_CONTEXT_TOTAL_DEADLINE",
            "browserless_ephemeral_header_policy": "STRIP_INHERITED_ALLOW_CURRENT_REQUIREMENTS_CONDUIT",
            "challenge_bypass_supported": False,
            "turnstile_solving_supported": False,
            "proof_token_generation_supported": False,
            "browser_protection_emulation_supported": False,
            "protected_credential_replay_supported": False,
            "browser_fallback_supported": False,
            "browser_authority_product_runtime_policy_supported": False,
            "model_profile_product_runtime_selection_supported": False,
            "temporary_chat_product_runtime_selection_supported": False,
            "streaming_supported": True,
            "streaming_contract_version": 1,
            "streaming_event_surface": "on_event",
            "streaming_event_types": [
                "assistant_text_delta",
                "canonical_text_finalized",
            ],
            "streaming_source": "DIRECT_HTTP_SSE",
            "streaming_delivery": "PROVISIONAL_APPEND_PLUS_CANONICAL_RECONCILIATION",
            "streaming_canonical_finality": "CANONICAL_CONVERSATION_READBACK",
            "streaming_canonical_finality_authoritative": True,
            "streaming_automatic_write_retry": False,
        }

    def _execute(
        self,
        text: str,
        *,
        conversation: ConversationInput,
        timeout: float,
        poll_interval: float,
        on_token: TokenCallback,
        on_event: EventCallback,
    ) -> ProductRuntimeExecution:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")

        started = monotonic()
        deadline = started + timeout
        with self._write_lock:
            self._remaining_before_write(
                deadline,
                request_stage="browserless_write_queue",
            )
            self._assert_execution_provider_boundary()
            resolved_conversation = self._resolve_conversation(conversation)
            previous_message_id = self._parent_message_id(resolved_conversation)
            requirements = self._acquire_unprotected_requirements()
            self._assert_execution_provider_boundary()
            accumulator = RevisionSafeTextAccumulator()
            sequence = 0
            write_state = {"final_write_started": False}

            def stream_token(token: str) -> None:
                nonlocal sequence
                if not isinstance(token, str) or not token:
                    return
                sequence += 1
                event = {
                    "type": "assistant_text_delta",
                    "sequence": sequence,
                    "delta": token,
                }
                normalized = accumulator.apply(event)
                if normalized is not None and on_event is not None:
                    on_event(normalized)
                if on_token is not None:
                    on_token(token)

            self._remaining_before_write(
                deadline,
                request_stage="browserless_prepared_write_binding",
            )
            try:
                with self._bind_current_prepared_write(
                    requirements,
                    write_state=write_state,
                    deadline=deadline,
                ):
                    response = self._direct_send(
                        text,
                        conversation=resolved_conversation,
                        on_token=stream_token,
                        on_event=None,
                    )
            except BrowserlessRequestTransportError:
                raise
            except RequestError as error:
                raise self._classify_request_error(
                    error,
                    final_write_started=bool(write_state["final_write_started"]),
                ) from error
            except Exception as error:
                ambiguous = bool(write_state["final_write_started"])
                raise BrowserlessRequestTransportError(
                    f"browserless direct request failed: {type(error).__name__}: {error}",
                    request_stage=(
                        "conversation_stream" if ambiguous else "conversation_prepare"
                    ),
                    write_may_have_been_submitted=ambiguous,
                    reconciliation_required=ambiguous,
                ) from error

            if not isinstance(response, ChatResponse):
                raise BrowserlessRequestTransportError(
                    "browserless direct request returned an unexpected response type",
                    request_stage="conversation_stream",
                    write_may_have_been_submitted=True,
                    reconciliation_required=True,
                )

            remaining = max(0.0, deadline - monotonic())
            canonical_status, canonical_message, canonical_text = self._canonical_finalize(
                response,
                previous_message_id=previous_message_id,
                timeout=remaining,
                poll_interval=poll_interval,
            )
            reconciliation = accumulator.reconcile(canonical_text)

            response.text = canonical_text
            response.conversation.message_id = canonical_message.message_id
            response.conversation.parent_message_id = canonical_message.message_id
            finish_reason = getattr(canonical_status, "finish_reason", None)
            if isinstance(finish_reason, str) and finish_reason.strip():
                response.conversation.finish_reason = finish_reason.strip()
            elif canonical_message.finish_reason:
                response.conversation.finish_reason = canonical_message.finish_reason

            conversation_id = response.conversation.conversation_id
            if not isinstance(conversation_id, str) or not conversation_id.strip():
                raise BrowserlessRequestTransportError(
                    "canonical finality succeeded without a conversation id",
                    request_stage="canonical_reconciliation",
                    write_may_have_been_submitted=True,
                    reconciliation_required=True,
                )

            if on_event is not None:
                on_event(
                    accumulator.finalization_event(
                        canonical_text=canonical_text,
                        conversation_id=conversation_id,
                        message_id=canonical_message.message_id,
                        model=(
                            canonical_message.model
                            or getattr(response.request, "observed_model", None)
                        ),
                        finish_reason=response.conversation.finish_reason,
                    )
                )

            persona = requirements.get("persona")
            observation = BrowserlessRequestObservation(
                transport=self.transport_id,
                write_plane=_BROWSERLESS_WRITE_PLANE,
                requirements_persona=(
                    persona.strip() if isinstance(persona, str) and persona.strip() else None
                ),
                requirements_token_present=True,
                protected_challenges=(),
                sentinel_protocol="TWO_PHASE_PREPARE_FINALIZE",
                conversation_prepare_protocol="PREPARE_CONDUIT_FINAL_WRITE",
                canonical_status="completed",
                canonical_message_id=canonical_message.message_id,
                reconciliation=reconciliation,
                stream_observation_count=accumulator.observation_count,
                stream_revision_count=accumulator.revision_count,
                stream_delta_count=accumulator.delta_count,
                stream_delivery_incomplete=accumulator.delivery_incomplete,
            )
            return ProductRuntimeExecution(
                transport=self.transport_id,
                response=response,
                observation=observation,
            )

    def _acquire_unprotected_requirements(self) -> dict[str, Any]:
        prepare_url = f"{CHAT_URL.rstrip('/')}{SENTINEL_PREPARE_PATH}"
        try:
            prepare_headers = _strip_ephemeral_write_headers(
                build_sentinel_prepare_headers(self.client)
            )
            with suppress_web_session_debug_trace():
                status, data = self.client._json_request(
                    "POST",
                    prepare_url,
                    {"p": None},
                    prepare_headers,
                )
        except RequestError as error:
            status_code = getattr(error, "status_code", None)
            if status_code == 403:
                raise BrowserlessChallengeBoundaryError(
                    ("sentinel-prepare-http-403",),
                    status_code=403,
                ) from error
            raise BrowserlessRequestTransportError(
                f"browserless Sentinel prepare failed: {error}",
                request_stage="browserless_sentinel_prepare",
                status_code=status_code,
                endpoint=SENTINEL_PREPARE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            ) from error
        except Exception as error:
            raise BrowserlessRequestTransportError(
                f"browserless Sentinel prepare failed before write: "
                f"{type(error).__name__}: {error}",
                request_stage="browserless_sentinel_prepare",
                endpoint=SENTINEL_PREPARE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            ) from error

        if status == 403:
            raise BrowserlessChallengeBoundaryError(
                ("sentinel-prepare-http-403",), status_code=403
            )
        if status == 401:
            raise BrowserlessRequestTransportError(
                "browserless Sentinel prepare rejected authentication: status=401",
                request_stage="browserless_sentinel_prepare",
                status_code=401,
                endpoint=SENTINEL_PREPARE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )
        if status >= 400:
            raise BrowserlessRequestTransportError(
                f"browserless Sentinel prepare failed: status={status}",
                request_stage="browserless_sentinel_prepare",
                status_code=int(status),
                endpoint=SENTINEL_PREPARE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )
        if not isinstance(data, Mapping):
            raise BrowserlessProtocolDriftError(
                "Sentinel prepare response is not an object",
                endpoint=SENTINEL_PREPARE_PATH,
            )
        if not _REQUIRED_PREPARE_KEYS.issubset(data.keys()):
            missing = sorted(_REQUIRED_PREPARE_KEYS.difference(data.keys()))
            raise BrowserlessProtocolDriftError(
                f"Sentinel prepare response is missing observed keys: {', '.join(missing)}",
                endpoint=SENTINEL_PREPARE_PATH,
            )

        prepare_token = _required_nonempty_text(
            data.get("prepare_token"),
            field="prepare_token",
            stage="browserless_sentinel_prepare",
            endpoint=SENTINEL_PREPARE_PATH,
        )
        persona = _required_nonempty_text(
            data.get("persona"),
            field="persona",
            stage="browserless_sentinel_prepare",
            endpoint=SENTINEL_PREPARE_PATH,
        )
        challenges = _validate_required_descriptors(
            data,
            stage="browserless_sentinel_prepare",
            endpoint=SENTINEL_PREPARE_PATH,
        )
        if challenges:
            raise BrowserlessChallengeBoundaryError(challenges)

        return self._finalize_unprotected_requirements(
            prepare_token=prepare_token,
            persona=persona,
        )

    def _finalize_unprotected_requirements(
        self,
        *,
        prepare_token: str,
        persona: str,
    ) -> dict[str, Any]:
        finalize_url = f"{CHAT_URL.rstrip('/')}{SENTINEL_FINALIZE_PATH}"
        payload = {
            "prepare_token": prepare_token,
            "proofofwork": None,
            "turnstile": None,
        }
        try:
            headers = _strip_ephemeral_write_headers(
                self.client._build_headers(
                    {
                        "accept": "*/*",
                        "content-type": "application/json",
                        "origin": CHAT_URL.rstrip("/"),
                        "referer": CHAT_URL,
                        "x-openai-target-path": SENTINEL_FINALIZE_PATH,
                        "x-openai-target-route": SENTINEL_FINALIZE_PATH,
                    }
                )
            )
            with suppress_web_session_debug_trace():
                status, data = self.client._json_request(
                    "POST",
                    finalize_url,
                    payload,
                    headers,
                )
        except RequestError as error:
            status_code = getattr(error, "status_code", None)
            if status_code == 403:
                raise BrowserlessChallengeBoundaryError(
                    ("sentinel-finalize-http-403",),
                    status_code=403,
                    request_stage="browserless_sentinel_finalize",
                ) from error
            raise BrowserlessRequestTransportError(
                f"browserless Sentinel finalize failed: {error}",
                request_stage="browserless_sentinel_finalize",
                status_code=status_code,
                endpoint=SENTINEL_FINALIZE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            ) from error
        except Exception as error:
            raise BrowserlessRequestTransportError(
                f"browserless Sentinel finalize failed before write: "
                f"{type(error).__name__}: {error}",
                request_stage="browserless_sentinel_finalize",
                endpoint=SENTINEL_FINALIZE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            ) from error

        if status == 403:
            raise BrowserlessChallengeBoundaryError(
                ("sentinel-finalize-http-403",),
                status_code=403,
                request_stage="browserless_sentinel_finalize",
            )
        if status == 401:
            raise BrowserlessRequestTransportError(
                "browserless Sentinel finalize rejected authentication: status=401",
                request_stage="browserless_sentinel_finalize",
                status_code=401,
                endpoint=SENTINEL_FINALIZE_PATH,
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )
        if status >= 400:
            raise BrowserlessProtocolDriftError(
                "server did not accept challenge-free Sentinel finalize",
                request_stage="browserless_sentinel_finalize",
                endpoint=SENTINEL_FINALIZE_PATH,
                status_code=int(status),
            )
        if not isinstance(data, Mapping):
            raise BrowserlessProtocolDriftError(
                "Sentinel finalize response is not an object",
                request_stage="browserless_sentinel_finalize",
                endpoint=SENTINEL_FINALIZE_PATH,
            )
        if not _REQUIRED_FINALIZE_KEYS.issubset(data.keys()):
            missing = sorted(_REQUIRED_FINALIZE_KEYS.difference(data.keys()))
            raise BrowserlessProtocolDriftError(
                f"Sentinel finalize response is missing observed keys: {', '.join(missing)}",
                request_stage="browserless_sentinel_finalize",
                endpoint=SENTINEL_FINALIZE_PATH,
            )
        token = _required_nonempty_text(
            data.get("token"),
            field="token",
            stage="browserless_sentinel_finalize",
            endpoint=SENTINEL_FINALIZE_PATH,
        )
        finalized_persona = data.get("persona")
        if isinstance(finalized_persona, str) and finalized_persona.strip():
            persona = finalized_persona.strip()

        return {
            "token": token,
            "persona": persona,
            "proofofwork": {"required": False},
            "so": {"required": False},
            "turnstile": {"required": False},
        }

    @contextmanager
    def _bind_current_prepared_write(
        self,
        requirements: dict[str, Any],
        *,
        write_state: dict[str, bool],
        deadline: float,
    ) -> Iterator[None]:
        """Use current conversation prepare/conduit without Sentinel bundle machinery.

        The server-issued challenge-free requirements token is already finalized.
        Instance hooks remain visible on the shared compatibility client, so every
        override delegates to the original bound method unless the current
        execution context owns this browserless binding. Browserless mutation
        transactions sharing one canonical client are serialized by the shared
        client lock, while ordinary concurrent callers stay outside the binding.
        The browserless total deadline is applied only in the owner execution
        context; the shared client's ordinary timeout field is never mutated.
        """

        from .sentinel_bundle import _PREPARED_SEND_ACTIVE

        if _PREPARED_SEND_ACTIVE.get():
            raise BrowserlessRequestTransportError(
                "browserless prepared write cannot nest inside another prepared transaction",
                request_stage="browserless_prepared_write_binding",
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            )

        instance_dict = getattr(self.client, "__dict__", None)
        if not isinstance(instance_dict, dict):
            raise BrowserlessProtocolDriftError(
                "direct-request client does not expose instance state for prepared binding",
                request_stage="browserless_prepared_write_binding",
            )

        delegate_ready = getattr(self.client, "_get_ready_requirements", None)
        delegate_headers = getattr(self.client, "_build_headers", None)
        delegate_refill = getattr(self.client, "start_sentinel_bundle_refill", None)
        delegate_curl = getattr(self.client, "_build_curl_command", None)
        delegate_ws_async = getattr(self.client, "_stream_handoff_via_ws_topic_async", None)
        delegate_poll = getattr(self.client, "_poll_conversation_after_prepare", None)
        if not callable(delegate_ready) or not callable(delegate_headers):
            raise BrowserlessProtocolDriftError(
                "direct-request client is missing prepared-write delegate methods",
                request_stage="browserless_prepared_write_binding",
            )

        _sync_device_header(self.client)
        marker = object()
        previous_ready = instance_dict.get("_get_ready_requirements", marker)
        previous_headers = instance_dict.get("_build_headers", marker)
        previous_refill = instance_dict.get("start_sentinel_bundle_refill", marker)
        previous_curl = instance_dict.get("_build_curl_command", marker)
        previous_ws_async = instance_dict.get("_stream_handoff_via_ws_topic_async", marker)
        previous_poll = instance_dict.get("_poll_conversation_after_prepare", marker)
        owner = object()
        consumed = False
        turn_trace_id = str(uuid.uuid4())
        requirements_token = _required_nonempty_text(
            requirements.get("token"),
            field="token",
            stage="browserless_prepared_write_binding",
            endpoint=_CONVERSATION_PREPARE_PATH,
        )

        def owns_binding() -> bool:
            return _BROWSERLESS_BINDING_OWNER.get() is owner

        def get_ready_requirements() -> tuple[dict[str, Any], str | None]:
            nonlocal consumed
            if not owns_binding():
                return delegate_ready()
            if consumed:
                raise BrowserlessRequestTransportError(
                    "browserless prepared write requested requirements more than once",
                    request_stage="requirements_reuse",
                    write_may_have_been_submitted=bool(write_state["final_write_started"]),
                    reconciliation_required=bool(write_state["final_write_started"]),
                )
            consumed = True
            return dict(requirements), None

        def build_headers(extra: dict[str, str | None] | None = None) -> dict[str, str]:
            if not owns_binding():
                return delegate_headers(extra)
            headers = _strip_ephemeral_write_headers(
                dict(getattr(self.client, "base_headers", {}) or {})
            )
            auth = getattr(self.client, "auth", None)
            access_token = getattr(auth, "accessToken", None)
            if isinstance(access_token, str) and access_token:
                headers["authorization"] = f"Bearer {access_token}"
            cookies = getattr(auth, "cookies", None)
            if isinstance(cookies, Mapping) and cookies:
                headers["cookie"] = "; ".join(
                    f"{key}={value}" for key, value in cookies.items()
                )
            patched = {
                key: value
                for key, value in dict(extra or {}).items()
                if value is not None
            }
            target_path = patched.get("x-openai-target-path")
            for raw_key, raw_value in tuple(patched.items()):
                normalized_key = str(raw_key).strip().lower()
                if normalized_key in _FORBIDDEN_BROWSERLESS_PROTECTION_HEADERS:
                    raise BrowserlessProtocolDriftError(
                        f"browserless prepared path attempted protected header {normalized_key}",
                        request_stage="browserless_prepared_write_binding",
                        endpoint=(target_path if isinstance(target_path, str) else None),
                    )
                if normalized_key == "openai-sentinel-chat-requirements-token":
                    if not isinstance(raw_value, str) or raw_value != requirements_token:
                        raise BrowserlessProtocolDriftError(
                            "browserless prepared path attempted a non-current requirements token",
                            request_stage="browserless_prepared_write_binding",
                            endpoint=(target_path if isinstance(target_path, str) else None),
                        )
                if normalized_key == "x-conduit-token":
                    if target_path != _CONVERSATION_WRITE_PATH or not isinstance(raw_value, str) or not raw_value:
                        raise BrowserlessProtocolDriftError(
                            "browserless prepared path attempted an invalid conduit token placement",
                            request_stage="browserless_prepared_write_binding",
                            endpoint=(target_path if isinstance(target_path, str) else None),
                        )
            if target_path in {_CONVERSATION_PREPARE_PATH, _CONVERSATION_WRITE_PATH}:
                patched["x-oai-turn-trace-id"] = turn_trace_id
            headers.update(patched)
            if target_path == _CONVERSATION_WRITE_PATH:
                self._remaining_before_write(
                    deadline,
                    request_stage="browserless_write_deadline",
                    endpoint=_CONVERSATION_WRITE_PATH,
                )
                # From this point the final mutation endpoint is about to be
                # dispatched. A subsequent generic transport failure is therefore
                # conservatively ambiguous.
                write_state["final_write_started"] = True
            return headers

        def start_refill(*args: Any, **kwargs: Any) -> Any:
            if owns_binding():
                return False
            if callable(delegate_refill):
                return delegate_refill(*args, **kwargs)
            return False

        def build_curl_command(*args: Any, **kwargs: Any) -> Any:
            if not callable(delegate_curl):
                raise BrowserlessProtocolDriftError(
                    "direct-request client is missing curl-command delegate method",
                    request_stage="browserless_prepared_write_binding",
                )
            command = delegate_curl(*args, **kwargs)
            if not owns_binding():
                return command
            remaining = self._remaining_stream_deadline(
                deadline,
                write_started=bool(write_state["final_write_started"]),
            )
            if not isinstance(command, list):
                raise BrowserlessProtocolDriftError(
                    "direct-request curl command is not a list",
                    request_stage="browserless_prepared_write_binding",
                )
            patched_command = list(command)
            try:
                max_time_index = patched_command.index("--max-time")
            except ValueError as error:
                raise BrowserlessProtocolDriftError(
                    "direct-request curl command is missing --max-time",
                    request_stage="browserless_prepared_write_binding",
                ) from error
            if max_time_index + 1 >= len(patched_command):
                raise BrowserlessProtocolDriftError(
                    "direct-request curl command has no --max-time value",
                    request_stage="browserless_prepared_write_binding",
                )
            patched_command[max_time_index + 1] = str(max(0.001, remaining))
            return patched_command

        async def stream_handoff_via_ws_topic_async(*args: Any, **kwargs: Any) -> Any:
            if not callable(delegate_ws_async):
                raise BrowserlessProtocolDriftError(
                    "direct-request client is missing WebSocket handoff delegate method",
                    request_stage="browserless_prepared_write_binding",
                )
            if not owns_binding():
                return await delegate_ws_async(*args, **kwargs)
            remaining = self._remaining_stream_deadline(
                deadline,
                write_started=bool(write_state["final_write_started"]),
            )
            try:
                return await asyncio.wait_for(
                    delegate_ws_async(*args, **kwargs),
                    timeout=remaining,
                )
            except asyncio.TimeoutError as error:
                ambiguous = bool(write_state["final_write_started"])
                raise BrowserlessRequestTransportError(
                    "browserless total deadline expired during WebSocket stream recovery",
                    request_stage=("conversation_stream" if ambiguous else "conversation_prepare"),
                    write_may_have_been_submitted=ambiguous,
                    reconciliation_required=ambiguous,
                ) from error

        def poll_conversation_after_prepare(*args: Any, **kwargs: Any) -> Any:
            if not callable(delegate_poll):
                raise BrowserlessProtocolDriftError(
                    "direct-request client is missing conversation recovery delegate method",
                    request_stage="browserless_prepared_write_binding",
                )
            if not owns_binding():
                return delegate_poll(*args, **kwargs)
            remaining = self._remaining_stream_deadline(
                deadline,
                write_started=bool(write_state["final_write_started"]),
            )
            patched_kwargs = dict(kwargs)
            requested_timeout = patched_kwargs.get("timeout")
            if isinstance(requested_timeout, (int, float)):
                patched_kwargs["timeout"] = max(
                    0.001,
                    min(float(requested_timeout), remaining),
                )
            else:
                patched_kwargs["timeout"] = max(0.001, remaining)
            return delegate_poll(*args, **patched_kwargs)

        self.client._get_ready_requirements = get_ready_requirements
        self.client._build_headers = build_headers
        self.client.start_sentinel_bundle_refill = start_refill
        if callable(delegate_curl):
            self.client._build_curl_command = build_curl_command
        if callable(delegate_ws_async):
            self.client._stream_handoff_via_ws_topic_async = stream_handoff_via_ws_topic_async
        if callable(delegate_poll):
            self.client._poll_conversation_after_prepare = poll_conversation_after_prepare
        owner_token = _BROWSERLESS_BINDING_OWNER.set(owner)
        active_token = _PREPARED_SEND_ACTIVE.set(True)
        try:
            yield
        finally:
            _PREPARED_SEND_ACTIVE.reset(active_token)
            _BROWSERLESS_BINDING_OWNER.reset(owner_token)
            if previous_ready is marker:
                try:
                    delattr(self.client, "_get_ready_requirements")
                except AttributeError:
                    pass
            else:
                self.client._get_ready_requirements = previous_ready
            if previous_headers is marker:
                try:
                    delattr(self.client, "_build_headers")
                except AttributeError:
                    pass
            else:
                self.client._build_headers = previous_headers
            if previous_refill is marker:
                try:
                    delattr(self.client, "start_sentinel_bundle_refill")
                except AttributeError:
                    pass
            else:
                self.client.start_sentinel_bundle_refill = previous_refill
            if callable(delegate_curl):
                if previous_curl is marker:
                    try:
                        delattr(self.client, "_build_curl_command")
                    except AttributeError:
                        pass
                else:
                    self.client._build_curl_command = previous_curl
            if callable(delegate_ws_async):
                if previous_ws_async is marker:
                    try:
                        delattr(self.client, "_stream_handoff_via_ws_topic_async")
                    except AttributeError:
                        pass
                else:
                    self.client._stream_handoff_via_ws_topic_async = previous_ws_async
            if callable(delegate_poll):
                if previous_poll is marker:
                    try:
                        delattr(self.client, "_poll_conversation_after_prepare")
                    except AttributeError:
                        pass
                else:
                    self.client._poll_conversation_after_prepare = previous_poll

    def _resolve_conversation(
        self,
        conversation: ConversationInput,
    ) -> ChatConversation | dict[str, Any] | None:
        if conversation is None:
            return None

        # Continuation authority is canonical, even when the caller supplies a
        # seemingly complete ChatConversation/dict. This avoids writing from a
        # stale parent supplied by a long-lived application object.
        try:
            attached = self.canonical_client.attach_conversation(conversation)
        except Exception as error:
            raise BrowserlessRequestTransportError(
                f"canonical continuation attach failed before browserless write: "
                f"{type(error).__name__}: {error}",
                request_stage="canonical_attach",
                write_may_have_been_submitted=False,
                reconciliation_required=False,
            ) from error
        if isinstance(attached, AttachedConversation):
            return attached.conversation
        if isinstance(attached, ChatConversation):
            return attached
        nested = getattr(attached, "conversation", None)
        if isinstance(nested, ChatConversation):
            return nested
        if isinstance(nested, dict):
            return dict(nested)
        if isinstance(attached, dict):
            nested = attached.get("conversation")
            if isinstance(nested, dict):
                return dict(nested)
        raise BrowserlessRequestTransportError(
            "canonical attach did not return a usable continuation conversation",
            request_stage="canonical_attach",
            write_may_have_been_submitted=False,
            reconciliation_required=False,
        )

    def _canonical_finalize(
        self,
        response: ChatResponse,
        *,
        previous_message_id: str | None,
        timeout: float,
        poll_interval: float,
    ) -> tuple[Any, ChatMessage, str]:
        conversation_id = response.conversation.conversation_id
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise BrowserlessRequestTransportError(
                "browserless write returned no conversation id for canonical reconciliation",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )

        deadline = monotonic() + max(0.0, timeout)
        status: Any = None
        while True:
            try:
                status = self.canonical_client.get_status(response.conversation)
            except Exception as error:
                raise BrowserlessRequestTransportError(
                    f"canonical status read failed after browserless write: {type(error).__name__}: {error}",
                    request_stage="canonical_reconciliation",
                    write_may_have_been_submitted=True,
                    reconciliation_required=True,
                ) from error
            status_value = getattr(status, "status", None)
            if status_value == "completed":
                break
            if monotonic() >= deadline:
                raise BrowserlessRequestTransportError(
                    f"canonical completion was not proven before timeout; status={status_value!r}",
                    request_stage="canonical_reconciliation",
                    write_may_have_been_submitted=True,
                    reconciliation_required=True,
                )
            sleep(min(poll_interval, max(0.01, deadline - monotonic())))

        try:
            messages = self.canonical_client.get_messages(response.conversation)
        except Exception as error:
            raise BrowserlessRequestTransportError(
                f"canonical message read failed after browserless write: {type(error).__name__}: {error}",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            ) from error
        if not isinstance(messages, list) or not all(
            isinstance(item, ChatMessage) for item in messages
        ):
            raise BrowserlessRequestTransportError(
                "canonical message read returned an unexpected shape after browserless write",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )
        assistant = _latest_assistant(messages)
        if assistant is None or not assistant.message_id:
            raise BrowserlessRequestTransportError(
                "canonical assistant message identity is unavailable after browserless write",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )
        if previous_message_id and assistant.message_id == previous_message_id:
            raise BrowserlessRequestTransportError(
                "canonical readback did not advance beyond the prewrite parent message",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )
        return status, assistant, assistant.text

    @staticmethod
    def _classify_request_error(
        error: RequestError,
        *,
        final_write_started: bool,
    ) -> BrowserlessRequestTransportError:
        stage = getattr(error, "request_stage", None)
        normalized_stage = stage if isinstance(stage, str) and stage else "transport"
        if normalized_stage in _PREWRITE_REQUEST_STAGES:
            prewrite = True
        elif normalized_stage in _EXPLICIT_POSTWRITE_REQUEST_STAGES:
            prewrite = False
        else:
            # Generic/unknown transport failures need execution evidence. Before
            # final-write headers exist they are proven prewrite; afterwards the
            # mutation outcome is conservatively ambiguous.
            prewrite = not final_write_started
        if prewrite and normalized_stage == "transport":
            normalized_stage = "conversation_prepare"
        return BrowserlessRequestTransportError(
            f"browserless direct request failed: {error}",
            request_stage=normalized_stage,
            status_code=getattr(error, "status_code", None),
            endpoint=getattr(error, "endpoint", None),
            write_may_have_been_submitted=not prewrite,
            reconciliation_required=not prewrite,
        )

    @staticmethod
    def _parent_message_id(
        conversation: ChatConversation | dict[str, Any] | None,
    ) -> str | None:
        if isinstance(conversation, ChatConversation):
            return conversation.parent_message_id or conversation.message_id
        if isinstance(conversation, dict):
            value = conversation.get("parent_message_id") or conversation.get("message_id")
            return value.strip() if isinstance(value, str) and value.strip() else None
        return None

    @staticmethod
    def _conversation_id_hint(conversation: ConversationInput) -> str | None:
        if isinstance(conversation, str):
            value = conversation.strip()
            return value or None
        if isinstance(conversation, ChatConversation):
            return conversation.conversation_id
        if isinstance(conversation, dict):
            value = conversation.get("conversation_id")
            return value.strip() if isinstance(value, str) and value.strip() else None
        value = getattr(conversation, "conversation_id", None)
        return value.strip() if isinstance(value, str) and value.strip() else None
