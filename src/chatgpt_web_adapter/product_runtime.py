from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from . import product_runtime_core as _core
from .auth import DEFAULT_AUTH_FILE
from .client import DEFAULT_TIMEOUT_SECONDS, ChatGPTWebClient
from .product_runtime_observation_gate import gate_product_runtime_send_text_observed
from .product_submission import ProductSubmissionAck
from .product_transport import (
    BROWSER_OWNED_PRODUCT_TRANSPORT,
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
from .types import ChatResponse, MediaItem

ProductConversationModeUnavailableError = _core.ProductConversationModeUnavailableError
ProductRichInputUnavailableError = _core.ProductRichInputUnavailableError

# Keep historical internal helpers import-compatible while making the public runtime
# class and assembly functions explicit in this module.
_assemble_default_write_transport = _core._assemble_default_write_transport


def __getattr__(name: str) -> Any:
    """Delegate untouched runtime implementation details to the frozen core."""

    return getattr(_core, name)


class ChatGPTProductRuntime(_core.ChatGPTProductRuntime):
    """Explicitly composed ordinary ChatGPT product runtime."""

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

    @gate_product_runtime_send_text_observed
    def send_text_observed(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = "normal",
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ProductRuntimeExecution:
        return super().send_text_observed(
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

    def submit(
        self,
        text: str,
        *,
        conversation: ConversationInput = None,
        timeout: float = 150.0,
        poll_interval: float = 0.5,
        on_token: TokenCallback = None,
        on_event: EventCallback = None,
        conversation_mode: str = "normal",
        browser_authority_policy: str | None = None,
        browser_authority_ttl_ms: int | None = None,
        model_profile: str | None = None,
        media: Sequence[MediaItem] | None = None,
    ) -> ProductSubmissionAck:
        return super().submit(
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
        return super().await_final(submission)

    def submission_lifecycle_snapshot(self) -> dict[str, Any]:
        return super().submission_lifecycle_snapshot()

    def observe_ui_liveness(
        self,
        *,
        timeout: float = 3.0,
    ) -> BrowserUILivenessObservation:
        return super().observe_ui_liveness(timeout=timeout)

    def governance(self) -> dict[str, Any]:
        return super().governance()


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
    """Assemble an explicit ordinary-ChatGPT product runtime without fallback."""

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
