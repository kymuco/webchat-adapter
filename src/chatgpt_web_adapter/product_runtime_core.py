from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from .auth import DEFAULT_AUTH_FILE
from .client import DEFAULT_TIMEOUT_SECONDS, ChatGPTWebClient
from .product_capabilities import ProductCapabilities
from .product_media import browser_owned_media_scope
from .product_provenance import (
    CompletionSource,
    ConversationMode,
    ConversationModeEvidenceSource,
    ProductConversationModeProvenance,
    ProductExecutionProvenance,
    ProductTemporaryLifecycleProvenance,
    TemporaryLifecycleEvidenceSource,
    TemporaryLifecycleState,
    build_product_execution_provenance,
)
from .product_submission import ProductSubmissionAck
from .product_transport import (
    BROWSER_OWNED_PRODUCT_TRANSPORT,
    BROWSERLESS_REQUEST_PRODUCT_TRANSPORT,
    DEFAULT_PRODUCT_TRANSPORT,
    SUPPORTED_PRODUCT_TRANSPORTS,
    CanonicalConversationClient,
    ConversationInput,
    EventCallback,
    ProductRuntimeExecution,
    ProductRuntimeHealth,
    ProductWriteTransport,
    TokenCallback,
    normalize_product_transport,
    require_canonical_conversation_client,
    require_product_write_transport,
)
from .product_ui_liveness import BrowserUILivenessObservation
from .types import ChatMessage, ChatResponse, ConversationStatus, MediaItem

_NORMAL_CONVERSATION_MODE = "normal"
_TEMPORARY_CONVERSATION_MODE = "temporary"
_SUPPORTED_CONVERSATION_MODES: tuple[str, ...] = (
    _NORMAL_CONVERSATION_MODE,
    _TEMPORARY_CONVERSATION_MODE,
)


def _normalize_conversation_mode(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("conversation_mode must be a string")
    normalized = value.strip().lower()
    if normalized not in _SUPPORTED_CONVERSATION_MODES:
        supported = ", ".join(_SUPPORTED_CONVERSATION_MODES)
        raise ValueError(
            f"unsupported conversation_mode {value!r}; expected one of: {supported}"
        )
    return normalized


def _normal_conversation_mode_provenance() -> ProductConversationModeProvenance:
    return ProductConversationModeProvenance(
        requested_conversation_mode=ConversationMode.NORMAL,
        observed_conversation_mode=ConversationMode.NORMAL,
        observed_mode_evidence_source=(
            ConversationModeEvidenceSource.TRANSPORT_SEMANTICS_CONTRACT
        ),
        observed_mode_proven=True,
        proof_detail=(
            "normal request dispatched through ordinary-mode ProductWriteTransport"
        ),
    )


def _not_established_temporary_lifecycle_provenance() -> (
    ProductTemporaryLifecycleProvenance
):
    return ProductTemporaryLifecycleProvenance(
        temporary_lifecycle_state=TemporaryLifecycleState.NOT_ESTABLISHED,
        lifecycle_evidence_source=(
            TemporaryLifecycleEvidenceSource.RUNTIME_GOVERNANCE_CONTRACT
        ),
        lifecycle_state_proven=True,
        live_write_authority_proven=False,
        proof_detail="request blocked before Temporary lifecycle establishment",
    )


class ProductConversationModeUnavailableError(RuntimeError):
    """Fail-closed refusal before any product write for an unavailable mode."""

    def __init__(self, requested_mode: str) -> None:
        normalized = _normalize_conversation_mode(requested_mode)
        requested = ConversationMode(normalized.upper())
        self.conversation_mode_provenance = ProductConversationModeProvenance(
            requested_conversation_mode=requested,
            observed_conversation_mode=ConversationMode.UNKNOWN,
            observed_mode_evidence_source=ConversationModeEvidenceSource.NONE,
            observed_mode_proven=False,
            proof_detail="request blocked before ProductWriteTransport dispatch",
        )
        self.temporary_lifecycle_provenance = (
            _not_established_temporary_lifecycle_provenance()
        )
        super().__init__(
            "PRODUCT_CONVERSATION_MODE_UNAVAILABLE: "
            f"requested={requested.value} observed=UNKNOWN "
            f"conversation_mode={normalized!r} is unavailable on the selected "
            "production transport; fallback=none"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": str(self),
            "conversation_mode": self.conversation_mode_provenance.to_dict(),
            "temporary_lifecycle": self.temporary_lifecycle_provenance.to_dict(),
        }


class ProductRichInputUnavailableError(RuntimeError):
    """Fail-closed refusal before write when rich input lacks proven routing."""

    def __init__(self, *, transport: str, conversation_mode: str) -> None:
        self.transport = transport
        self.conversation_mode = _normalize_conversation_mode(conversation_mode)
        super().__init__(
            "PRODUCT_RICH_INPUT_UNAVAILABLE: "
            f"transport={transport!r} conversation_mode={self.conversation_mode!r}; "
            "fallback=none"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": str(self),
            "transport": self.transport,
            "conversation_mode": self.conversation_mode,
            "write_may_have_been_submitted": False,
            "fallback_transport": None,
        }


def _conversation_mode_override_kwargs(
    write_transport: ProductWriteTransport,
    *,
    conversation_mode: str,
) -> tuple[str, dict[str, Any]]:
    """Resolve a caller mode without widening the generic PR8.4 protocol.

    Normal remains the compatibility/default path and therefore adds no transport
    kwarg. Temporary is dispatched only when the selected transport explicitly
    advertises the PR8.13 mode-aware route; otherwise it fails before write.
    """

    mode = _normalize_conversation_mode(conversation_mode)
    if mode == _NORMAL_CONVERSATION_MODE:
        return mode, {}

    governance = dict(write_transport.governance())
    if governance.get("temporary_chat_product_runtime_selection_supported") is not True:
        raise ProductConversationModeUnavailableError(mode)
    return mode, {"conversation_mode": _TEMPORARY_CONVERSATION_MODE}


def _known_browser_owned_rich_input_transport(
    write_transport: ProductWriteTransport,
) -> bool:
    """Return whether the selected writer is the proven PR9.2 implementation.

    ProductWriteTransport intentionally remains a text-oriented protocol. A custom
    transport may reuse the ``browser-owned`` identity without consuming the
    execution-local media scope, so transport identity alone is not rich-input
    authority. Until the generic protocol grows an explicit media capability, only
    the exact concrete BrowserOwnedProductTransport implementation may opt into
    PR9.2; subclasses can override the send path and therefore are not authority.
    """

    if write_transport.transport_id.strip().lower() != BROWSER_OWNED_PRODUCT_TRANSPORT:
        return False
    from .browser_owned_product_transport import BrowserOwnedProductTransport

    return type(write_transport) is BrowserOwnedProductTransport


def _rich_input_scope(
    write_transport: ProductWriteTransport,
    *,
    media: Sequence[MediaItem] | None,
    conversation_mode: str,
):
    """Resolve PR9.2 media without widening the transport protocol.

    Browser-owned normal turns through the known BrowserOwnedProductTransport are
    the only implementation target in this milestone. Browserless, Temporary, and
    injected/custom writers fail before transport dispatch; no fallback to
    text-only or another transport is permitted. An empty media sequence is
    ordinary text-only input, matching the historical client contract for
    dynamically assembled attachment lists.
    """

    if media is None or len(media) == 0:
        return nullcontext()
    mode = _normalize_conversation_mode(conversation_mode)
    transport_id = write_transport.transport_id.strip().lower()
    if (
        mode != _NORMAL_CONVERSATION_MODE
        or not _known_browser_owned_rich_input_transport(write_transport)
    ):
        raise ProductRichInputUnavailableError(
            transport=transport_id,
            conversation_mode=mode,
        )
    return browser_owned_media_scope(media)


def _validate_or_attach_normal_mode_provenance(
    provenance: ProductExecutionProvenance,
) -> ProductExecutionProvenance:
    mode = provenance.conversation_mode
    if mode is None:
        return replace(
            provenance,
            conversation_mode=_normal_conversation_mode_provenance(),
        )
    if mode.requested_conversation_mode is not ConversationMode.NORMAL:
        raise RuntimeError(
            "write transport returned conversation-mode provenance for unexpected "
            f"requested mode {mode.requested_conversation_mode.value!r}"
        )
    if (
        mode.observed_conversation_mode is not ConversationMode.NORMAL
        or not mode.observed_mode_proven
    ):
        raise RuntimeError(
            "write transport did not prove NORMAL observed conversation mode for "
            "a successful normal production execution"
        )
    return provenance


def _validate_temporary_mode_provenance(
    provenance: ProductExecutionProvenance,
) -> ProductExecutionProvenance:
    mode = provenance.conversation_mode
    lifecycle = provenance.temporary_lifecycle
    if mode is None:
        raise RuntimeError(
            "Temporary execution is missing conversation-mode provenance"
        )
    if (
        mode.requested_conversation_mode is not ConversationMode.TEMPORARY
        or mode.observed_conversation_mode is not ConversationMode.TEMPORARY
        or mode.observed_mode_evidence_source
        is not ConversationModeEvidenceSource.PRODUCT_MODE_OBSERVATION
        or not mode.observed_mode_proven
    ):
        raise RuntimeError(
            "successful Temporary execution did not prove requested/observed TEMPORARY mode"
        )
    if lifecycle is None:
        raise RuntimeError("Temporary execution is missing lifecycle provenance")
    if (
        lifecycle.temporary_lifecycle_state is not TemporaryLifecycleState.LIVE
        or lifecycle.lifecycle_evidence_source
        is not TemporaryLifecycleEvidenceSource.PRODUCT_LIFECYCLE_OBSERVATION
        or not lifecycle.lifecycle_state_proven
        or not lifecycle.live_write_authority_proven
    ):
        raise RuntimeError(
            "successful Temporary execution did not prove LIVE lifecycle write authority"
        )
    if (
        provenance.completion.source is not CompletionSource.TRANSPORT_RETURN
        or provenance.completion.canonical_completion_proven
    ):
        raise RuntimeError(
            "Temporary execution must use page-owned transport finality without "
            "fabricating ordinary canonical completion"
        )
    return provenance


def _browser_authority_override_kwargs(
    write_transport: ProductWriteTransport,
    *,
    browser_authority_policy: str | None,
    browser_authority_ttl_ms: int | None,
) -> dict[str, Any]:
    if browser_authority_policy is None and browser_authority_ttl_ms is None:
        return {}

    governance = dict(write_transport.governance())
    if governance.get("browser_authority_product_runtime_policy_supported") is not True:
        raise ValueError(
            "browser authority policy overrides are unavailable for the selected "
            "write transport"
        )
    return {
        "browser_authority_policy": browser_authority_policy,
        "browser_authority_ttl_ms": browser_authority_ttl_ms,
    }


def _model_profile_override_kwargs(
    write_transport: ProductWriteTransport,
    *,
    model_profile: str | None,
) -> dict[str, Any]:
    if model_profile is None:
        return {}

    governance = dict(write_transport.governance())
    if governance.get("model_profile_product_runtime_selection_supported") is not True:
        raise ValueError(
            "model profile selection is unavailable for the selected write transport"
        )
    return {"model_profile": model_profile}


def _assemble_default_write_transport(
    client: CanonicalConversationClient,
    *,
    transport: str,
    provider: Any | None,
    browser_authority_policy: str | None = None,
    browser_authority_ttl_ms: int | None = None,
) -> ProductWriteTransport:
    if transport == BROWSERLESS_REQUEST_PRODUCT_TRANSPORT:
        if provider is not None:
            raise ValueError(
                "browserless-request does not accept browser-native or Sentinel providers"
            )
        if browser_authority_policy is not None or browser_authority_ttl_ms is not None:
            raise ValueError(
                "browser authority policy is unavailable for browserless-request"
            )
        from .browserless_request_transport import BrowserlessRequestTransport

        return BrowserlessRequestTransport(client)

    if transport != BROWSER_OWNED_PRODUCT_TRANSPORT:
        raise ValueError(f"no product transport assembler registered for {transport!r}")

    from .browser_owned_product_transport import BrowserOwnedProductTransport

    transport_kwargs: dict[str, Any] = {"provider": provider}
    if browser_authority_policy is not None or browser_authority_ttl_ms is not None:
        transport_kwargs.update(
            {
                "browser_authority_policy": browser_authority_policy,
                "browser_authority_ttl_ms": browser_authority_ttl_ms,
            }
        )
    return BrowserOwnedProductTransport(client, **transport_kwargs)


class ChatGPTProductRuntime:
    """Implementation-independent ordinary ChatGPT product runtime."""

    def __init__(
        self,
        client: Any,
        *,
        transport: str = DEFAULT_PRODUCT_TRANSPORT,
        provider: Any | None = None,
        write_transport: ProductWriteTransport | None = None,
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
    ) -> None:
        self.transport = normalize_product_transport(transport)
        self.client = require_canonical_conversation_client(client)
        self.canonical = self.client

        if write_transport is not None and provider is not None:
            raise ValueError("provider and write_transport are mutually exclusive")
        if write_transport is not None and (
            browser_authority_policy is not None or browser_authority_ttl_ms is not None
        ):
            raise ValueError(
                "browser authority runtime defaults require runtime-owned transport assembly"
            )

        if write_transport is None:
            assembly_kwargs: dict[str, Any] = {
                "transport": self.transport,
                "provider": provider,
            }
            if (
                browser_authority_policy is not None
                or browser_authority_ttl_ms is not None
            ):
                assembly_kwargs.update(
                    {
                        "browser_authority_policy": browser_authority_policy,
                        "browser_authority_ttl_ms": browser_authority_ttl_ms,
                    }
                )
            write_transport = _assemble_default_write_transport(
                self.canonical,
                **assembly_kwargs,
            )
        else:
            write_transport = require_product_write_transport(write_transport)
            injected_id = write_transport.transport_id.strip().lower()
            if injected_id != self.transport:
                raise ValueError(
                    "write transport identity does not match selected transport: "
                    f"{injected_id!r} != {self.transport!r}"
                )

        self.write_transport = write_transport
        self._transport = write_transport
        self._writer = getattr(write_transport, "_runtime", write_transport)

    def health(
        self,
        conversation: ConversationInput = None,
    ) -> ProductRuntimeHealth:
        return self.write_transport.health(conversation)

    readiness = health

    def capabilities(self) -> ProductCapabilities:
        capabilities = self.write_transport.capabilities()
        if not isinstance(capabilities, ProductCapabilities):
            raise TypeError(
                "write transport capabilities() must return ProductCapabilities"
            )
        if capabilities.transport != self.transport:
            raise RuntimeError(
                "write transport returned capabilities for unexpected transport "
                f"{capabilities.transport!r}"
            )
        return capabilities

    def send_text(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = _NORMAL_CONVERSATION_MODE,
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ChatResponse:
        mode, mode_kwargs = _conversation_mode_override_kwargs(
            self.write_transport,
            conversation_mode=conversation_mode,
        )
        transport_kwargs = _browser_authority_override_kwargs(
            self.write_transport,
            browser_authority_policy=browser_authority_policy,
            browser_authority_ttl_ms=browser_authority_ttl_ms,
        )
        transport_kwargs.update(
            _model_profile_override_kwargs(
                self.write_transport,
                model_profile=model_profile,
            )
        )
        transport_kwargs.update(mode_kwargs)
        with _rich_input_scope(
            self.write_transport,
            media=media,
            conversation_mode=mode,
        ):
            return self.write_transport.send_text(
                text,
                conversation=conversation,
                timeout=timeout,
                poll_interval=poll_interval,
                on_token=on_token,
                on_event=on_event,
                **transport_kwargs,
            )

    def send(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = _NORMAL_CONVERSATION_MODE,
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ChatResponse:
        return self.send_text(
            text,
            conversation=conversation,
            timeout=timeout,
            poll_interval=poll_interval,
            on_token=on_token,
            on_event=on_event,
            conversation_mode=conversation_mode,
            browser_authority_policy=browser_authority_policy,
            browser_authority_ttl_ms=browser_authority_ttl_ms,
            model_profile=model_profile,
            media=media,
        )

    def send_text_observed(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = _NORMAL_CONVERSATION_MODE,
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ProductRuntimeExecution:
        mode, mode_kwargs = _conversation_mode_override_kwargs(
            self.write_transport,
            conversation_mode=conversation_mode,
        )
        transport_kwargs = _browser_authority_override_kwargs(
            self.write_transport,
            browser_authority_policy=browser_authority_policy,
            browser_authority_ttl_ms=browser_authority_ttl_ms,
        )
        transport_kwargs.update(
            _model_profile_override_kwargs(
                self.write_transport,
                model_profile=model_profile,
            )
        )
        transport_kwargs.update(mode_kwargs)

        with _rich_input_scope(
            self.write_transport,
            media=media,
            conversation_mode=mode,
        ):
            execution = self.write_transport.send_text_observed(
                text,
                conversation=conversation,
                timeout=timeout,
                poll_interval=poll_interval,
                on_token=on_token,
                on_event=on_event,
                **transport_kwargs,
            )
        if execution.transport != self.transport:
            raise RuntimeError(
                "write transport returned execution for unexpected transport "
                f"{execution.transport!r}"
            )

        provenance = execution.provenance
        if mode == _NORMAL_CONVERSATION_MODE:
            expected_mode = _normal_conversation_mode_provenance()
            if provenance is None:
                provenance = build_product_execution_provenance(
                    transport=self.transport,
                    response=execution.response,
                    observation=execution.observation,
                    governance=self.write_transport.governance(),
                    conversation_mode=expected_mode,
                )
            elif not isinstance(provenance, ProductExecutionProvenance):
                raise TypeError(
                    "write transport execution provenance must be ProductExecutionProvenance or None"
                )
            elif provenance.transport != self.transport:
                raise RuntimeError(
                    "write transport returned provenance for unexpected transport "
                    f"{provenance.transport!r}"
                )
            else:
                provenance = _validate_or_attach_normal_mode_provenance(provenance)
        else:
            if not isinstance(provenance, ProductExecutionProvenance):
                raise RuntimeError(
                    "Temporary production execution requires transport-proven mode/lifecycle provenance"
                )
            if provenance.transport != self.transport:
                raise RuntimeError(
                    "write transport returned Temporary provenance for unexpected transport "
                    f"{provenance.transport!r}"
                )
            provenance = _validate_temporary_mode_provenance(provenance)

        return ProductRuntimeExecution(
            transport=execution.transport,
            response=execution.response,
            observation=execution.observation,
            provenance=provenance,
        )

    def end_temporary_chat(self) -> bool:
        end = getattr(self.write_transport, "end_temporary_lifecycle", None)
        if not callable(end):
            raise ProductConversationModeUnavailableError(_TEMPORARY_CONVERSATION_MODE)
        return bool(end())

    def temporary_lifecycle_snapshot(self) -> dict[str, Any]:
        snapshot = getattr(self.write_transport, "temporary_lifecycle_snapshot", None)
        if not callable(snapshot):
            return {
                "state": "NOT_ESTABLISHED",
                "conversation_id": None,
                "token_present": False,
                "token_exported": False,
            }
        value = snapshot()
        return dict(value) if isinstance(value, dict) else {}

    def submit(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = _NORMAL_CONVERSATION_MODE,
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ProductSubmissionAck:
        """Submit one turn and return after browser-owned write acceptance."""

        from .product_submission_runtime import submit_product_turn

        return submit_product_turn(
            self,
            text,
            conversation=conversation,
            timeout=timeout,
            poll_interval=poll_interval,
            on_token=on_token,
            on_event=on_event,
            conversation_mode=conversation_mode,
            browser_authority_policy=browser_authority_policy,
            browser_authority_ttl_ms=browser_authority_ttl_ms,
            model_profile=model_profile,
            media=media,
        )

    def await_final(self, submission: ProductSubmissionAck) -> ChatResponse:
        """Resolve canonical finality for a previously accepted submission."""

        from .product_submission_runtime import await_product_submission

        return await_product_submission(self, submission)

    def submission_lifecycle_snapshot(self) -> dict[str, Any]:
        """Return bounded state for the optional split submission lifecycle."""

        from .product_submission_runtime import submission_lifecycle_snapshot

        return submission_lifecycle_snapshot(self)

    def observe_ui_liveness(
        self,
        *,
        timeout: float = 3.0,
    ) -> BrowserUILivenessObservation:
        """Observe browser UI liveness without granting authority or finality."""

        from .product_ui_liveness_runtime import observe_product_ui_liveness

        return observe_product_ui_liveness(self, timeout=timeout)

    def get_status(self, conversation: Any) -> ConversationStatus:
        return self.canonical.get_status(conversation)

    def get_messages(self, conversation: Any, **kwargs: Any) -> list[ChatMessage]:
        return self.canonical.get_messages(conversation, **kwargs)

    def attach_conversation(self, conversation: Any) -> Any:
        return self.canonical.attach_conversation(conversation)

    def governance(self) -> dict[str, Any]:
        transport_governance = dict(self.write_transport.governance())
        browser_authority_supported = (
            transport_governance.get(
                "browser_authority_product_runtime_policy_supported"
            )
            is True
        )
        model_profile_supported = (
            transport_governance.get(
                "model_profile_product_runtime_selection_supported"
            )
            is True
        )
        temporary_supported = (
            transport_governance.get(
                "temporary_chat_product_runtime_selection_supported"
            )
            is True
        )
        rich_input_browser_owned = _known_browser_owned_rich_input_transport(
            self.write_transport
        )
        transport_governance.update(
            {
                "transport": self.transport,
                "transport_selection_explicit": True,
                "supported_product_transports": list(SUPPORTED_PRODUCT_TRANSPORTS),
                "fallback_transport": None,
                "legacy_direct_write_fallback": False,
                "conversation_mode_request_values": list(_SUPPORTED_CONVERSATION_MODES),
                "default_conversation_mode": _NORMAL_CONVERSATION_MODE,
                "conversation_mode_fallback": None,
                "silent_conversation_mode_fallback": False,
                "temporary_mode_production_enabled": temporary_supported,
                "temporary_mode_fail_closed_before_write": True,
                "temporary_mode_requires_mode_aware_write_routing": True,
                "conversation_mode_provenance_model": "ProductConversationModeProvenance",
                "requested_conversation_mode_is_caller_input": True,
                "normal_observed_mode_evidence_source": (
                    ConversationModeEvidenceSource.TRANSPORT_SEMANTICS_CONTRACT.value
                ),
                "blocked_temporary_observed_mode": (
                    None if temporary_supported else ConversationMode.UNKNOWN.value
                ),
                "temporary_mode_observation_required_before_write": True,
                "conversation_mode_state_scope": "REQUEST",
                "conversation_mode_state_persisted": False,
                "temporary_mode_denial_mutates_runtime_mode_state": False,
                "normal_mode_requires_fresh_request_resolution": True,
                "normal_mode_inherits_temporary_identity": False,
                "normal_mode_inherits_temporary_lifecycle": False,
                "normal_mode_inherits_temporary_provenance": False,
                "temporary_mode_requires_fresh_request_resolution": True,
                "normal_mode_success_mutates_temporary_authority": False,
                "temporary_mode_inherits_normal_identity": False,
                "temporary_mode_inherits_normal_lifecycle": False,
                "temporary_mode_inherits_normal_provenance": False,
                "ordinary_runtime_tab_is_temporary_mode_proof": False,
                "ordinary_conversation_identity_is_temporary_mode_proof": False,
                "temporary_lifecycle_provenance_model": "ProductTemporaryLifecycleProvenance",
                "temporary_lifecycle_authority_scope": "LIVE_PRODUCT_LIFECYCLE",
                "temporary_lifecycle_state_persisted_by_product_runtime": False,
                "cold_runtime_implies_temporary_lifecycle": False,
                "warm_runtime_implies_temporary_lifecycle": False,
                "runtime_reassembly_preserves_temporary_lifecycle": False,
                "runtime_tab_presence_implies_temporary_lifecycle": False,
                "runtime_tab_recreation_restores_temporary_lifecycle": False,
                "browser_authority_recreation_restores_temporary_lifecycle": False,
                "temporary_lifecycle_requires_fresh_proof_after_runtime_recreation": True,
                "temporary_lifecycle_requires_fresh_proof_after_tab_recreation": True,
                "post_close_route_recovery_restores_temporary_lifecycle": False,
                "temporary_lifecycle_explicit_end_surface": (
                    "ChatGPTProductRuntime.end_temporary_chat"
                    if temporary_supported
                    else None
                ),
                "browser_authority_policy_high_level_surface": True,
                "browser_authority_selected_transport_policy_support": (
                    browser_authority_supported
                ),
                "browser_authority_policy_override_requires_transport_support": True,
                "browser_authority_runtime_default_requires_runtime_owned_transport_assembly": True,
                "browser_authority_policy_contract_scope": "RESOURCE_LIFECYCLE_ONLY",
                "browser_authority_policy_changes_conversation_identity": False,
                "browser_authority_policy_changes_conversation_mode": False,
                "browser_authority_policy_changes_canonical_finality": False,
                "browser_authority_policy_recreates_temporary_lifecycle": False,
                "browser_authority_policy_exposes_browser_mechanics": False,
                "browser_authority_runtime_tab_identity_required_by_caller": False,
                "browser_authority_native_messaging_details_required_by_caller": False,
                "model_profile_high_level_surface": True,
                "model_profile_selected_transport_support": model_profile_supported,
                "model_profile_override_requires_transport_support": True,
                "model_profile_fallback": None,
                "silent_model_profile_fallback": False,
                "model_profile_state_scope": "TURN_REQUIREMENT",
                "model_profile_preservation_scope_proven": False,
                "rich_input_high_level_surface": True,
                "rich_input_argument": "media",
                "rich_input_item_contract": "MediaItem",
                "rich_input_browser_owned_implementation_present": rich_input_browser_owned,
                "rich_input_browserless_implementation_present": False,
                "rich_input_normal_mode_only": True,
                "rich_input_temporary_mode_supported": False,
                "rich_input_fallback_transport": None,
                "rich_input_silent_text_only_fallback": False,
                "rich_input_bytes_cross_native_messaging": False,
                "rich_input_official_page_owns_upload": rich_input_browser_owned,
                "rich_input_official_page_owns_protected_write": rich_input_browser_owned,
                "rich_input_canonical_finality_unchanged": rich_input_browser_owned,
                "new_chat_supported": True,
                "continuation_supported": True,
                "daily_use_entrypoint": "ChatGPTProductRuntime.send",
                "canonical_lifecycle_access": True,
                "canonical_interface": "CanonicalConversationClient",
                "write_transport_interface": "ProductWriteTransport",
                "runtime_depends_on_concrete_browser_transport": False,
                "capability_model": "ProductCapabilities",
                "capability_states": [
                    "AVAILABLE",
                    "UNSUPPORTED",
                    "UNKNOWN",
                    "UNIMPLEMENTED",
                ],
                "provenance_model": "ProductExecutionProvenance",
                "finish_reason_is_optional_observed_metadata": True,
            }
        )
        from .product_ui_liveness_runtime import augment_product_ui_liveness_governance

        return augment_product_ui_liveness_governance(self, transport_governance)


def assemble_product_runtime(
    *,
    transport: str = DEFAULT_PRODUCT_TRANSPORT,
    client: Any | None = None,
    provider: Any | None = None,
    write_transport: ProductWriteTransport | None = None,
    browser_authority_policy: str | None = None,
    browser_authority_ttl_ms: int | None = None,
    auth_file: str | Path = DEFAULT_AUTH_FILE,
    client_timeout: int = DEFAULT_TIMEOUT_SECONDS,
    auto_refresh_auth: bool = True,
    persist_refreshed_auth: bool = True,
) -> ChatGPTProductRuntime:
    """Assemble an explicit ordinary-ChatGPT product runtime.

    Assembly never performs interactive browser login and never enables legacy
    Sentinel machinery. ``browser-owned`` remains the default production path;
    ``browserless-request`` is explicit and experimental. No transport fallback
    is performed.
    """

    normalized = normalize_product_transport(transport)
    if client is None:
        client = ChatGPTWebClient(
            auth_file=auth_file,
            timeout=client_timeout,
            auto_refresh_auth=auto_refresh_auth,
            persist_refreshed_auth=persist_refreshed_auth,
            auto_login=False,
            auto_sentinel=False,
        )

    canonical = require_canonical_conversation_client(client)

    runtime_browser_authority_policy = browser_authority_policy
    runtime_browser_authority_ttl_ms = browser_authority_ttl_ms
    if write_transport is None:
        assembly_kwargs: dict[str, Any] = {
            "transport": normalized,
            "provider": provider,
        }
        if browser_authority_policy is not None or browser_authority_ttl_ms is not None:
            assembly_kwargs.update(
                {
                    "browser_authority_policy": browser_authority_policy,
                    "browser_authority_ttl_ms": browser_authority_ttl_ms,
                }
            )
        write_transport = _assemble_default_write_transport(
            canonical,
            **assembly_kwargs,
        )
        provider = None
        runtime_browser_authority_policy = None
        runtime_browser_authority_ttl_ms = None

    return ChatGPTProductRuntime(
        canonical,
        transport=normalized,
        provider=provider,
        write_transport=write_transport,
        browser_authority_policy=runtime_browser_authority_policy,
        browser_authority_ttl_ms=runtime_browser_authority_ttl_ms,
    )
