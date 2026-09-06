from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Sequence

from .auth import CHAT_URL
from .conversation_prepare import prepare_text_turn
from .exceptions import RequestError
from .legacy_client_core import (
    DEFAULT_STREAM_RECOVERY_POLL_INTERVAL_SECONDS,
    DEFAULT_STREAM_RECOVERY_POLL_TIMEOUT_SECONDS,
)
from .sentinel_bundle import start_finalized_sentinel_bundle_refill
from .types import (
    ChatConversation,
    ChatMetrics,
    ChatRequestDiagnostics,
    ChatResponse,
    MediaItem,
)

CONVERSATION_PATH = "/backend-api/f/conversation"
_SAFE_STREAM_DIAGNOSTIC_KEYS = (
    "finish_reason",
    "observed_model",
    "observed_reasoning_effort",
)


def _clear_prefetched_requirements(client: Any) -> None:
    client.prefetched_requirements = None
    client.prefetched_proof_header = None
    client.prefetched_ts = 0.0


def _metadata_finish_reason(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    finish_details = metadata.get("finish_details")
    if not isinstance(finish_details, dict):
        return None
    value = finish_details.get("type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _finish_reason(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    return _metadata_finish_reason(message.get("metadata"))


def _copy_safe_stream_diagnostics(source: Any, target: dict[str, Any]) -> None:
    """Copy only response metadata that is safe to retain outside the parser."""

    if not isinstance(source, dict):
        return
    for key in _SAFE_STREAM_DIAGNOSTIC_KEYS:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            target[key] = value.strip()


def send_existing_text_prepared(
    self: Any,
    prompt: str,
    *,
    model: str,
    conversation: ChatConversation | dict[str, Any],
    system: str | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    temporary: bool = False,
    media: Sequence[MediaItem] | None = None,
    on_token: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> ChatResponse:
    """Send one ordinary text turn through the observed prepare/conduit contract.

    This path is intentionally scoped to an existing conversation with no media.
    New-chat and multimodal writes continue through the legacy ``send()`` path
    until they receive independent live-contract evidence. ``system`` is accepted
    for send-surface compatibility but, as before for an existing conversation,
    is not injected as a new system message.
    """

    if media:
        raise ValueError("prepared existing-text send does not accept media")

    conversation_dict = self._conversation_to_dict(conversation)
    if not isinstance(conversation_dict, dict):
        raise ValueError("conversation is required")
    conversation_id = conversation_dict.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation.conversation_id is required")
    conversation_id = conversation_id.strip()

    parent_message_id = (
        conversation_dict.get("parent_message_id")
        or conversation_dict.get("message_id")
    )
    if not isinstance(parent_message_id, str) or not parent_message_id.strip():
        raise ValueError("conversation parent/message id is required")
    parent_message_id = parent_message_id.strip()

    normalized_effort = self._normalize_reasoning_effort(reasoning_effort)
    resolved_model = self._resolve_model(model, reasoning_effort)
    messages = self._create_messages(
        prompt,
        None,
        system_hints=["search"] if web_search else None,
    )
    user_message = messages[-1] if messages else None
    user_message_id = user_message.get("id") if isinstance(user_message, dict) else None
    if not isinstance(user_message_id, str) or not user_message_id.strip():
        raise RequestError(
            "conversation prepare could not resolve user message id",
            request_stage="conversation_prepare",
        )

    # Discard only legacy single-step warmup material. The prepared-send wrapper
    # has already reserved the rolling two-phase bundle used by this write.
    _clear_prefetched_requirements(self)
    self._emit_event(
        on_event,
        "conversation_prepare_started",
        existing_conversation=True,
        ordinary_text=True,
        partial_query_message_id_present=True,
    )
    prepare_result, _prepare_payload = prepare_text_turn(
        self,
        prompt,
        model=resolved_model,
        conversation=conversation_dict,
        reasoning_effort=normalized_effort,
        web_search=web_search,
        temporary=temporary,
        partial_query_message_id=user_message_id,
    )
    if not prepare_result.status_ok:
        raise RequestError(
            f"conversation prepare status={prepare_result.status_code}: rejected",
            status_code=prepare_result.status_code,
            request_stage="conversation_prepare",
        )
    conduit_token = prepare_result.conduit_token
    if not isinstance(conduit_token, str) or not conduit_token.strip():
        raise RequestError(
            "conversation prepare response missing conduit_token",
            request_stage="conversation_prepare",
        )
    conduit_token = conduit_token.strip()
    self._emit_event(
        on_event,
        "conversation_prepare_succeeded",
        status_code=prepare_result.status_code,
        conduit_token_present=True,
    )

    started_at = time.perf_counter()
    observed_conversation_id: str | None = None
    observed_message_id: str | None = None
    text = ""
    stream_diagnostics: dict[str, Any] = {}
    observed_model: str | None = None
    observed_reasoning_effort: str | None = None
    finish_reason: str | None = None

    try:
        requirements, proof_header = self._get_ready_requirements()
        chat_token = requirements.get("token") if isinstance(requirements, dict) else None
        if not isinstance(chat_token, str) or not chat_token:
            raise RequestError("chat-requirements token is missing")

        payload: dict[str, Any] = {
            "action": "next",
            "fork_from_shared_post": False,
            "conversation_id": conversation_id,
            "parent_message_id": parent_message_id,
            "model": resolved_model,
            "client_prepare_state": "success",
            "conversation_mode": {"kind": "primary_assistant"},
            "enable_message_followups": False,
            "system_hints": ["search"] if web_search else [],
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "client_contextual_info": {"app_name": "chatgpt.com"},
            "messages": messages,
        }
        if temporary:
            payload["history_and_training_disabled"] = True
        if normalized_effort is not None:
            payload["thinking_effort"] = normalized_effort

        headers = self._build_headers(
            {
                "accept": "text/event-stream",
                "content-type": "application/json",
                "origin": CHAT_URL.rstrip("/"),
                "referer": f"{CHAT_URL.rstrip('/')}/c/{conversation_id}",
                "x-openai-target-path": CONVERSATION_PATH,
                "x-openai-target-route": CONVERSATION_PATH,
                "x-conduit-token": conduit_token,
                "x-oai-turn-trace-id": str(uuid.uuid4()),
                "openai-sentinel-chat-requirements-token": chat_token,
                "openai-sentinel-proof-token": proof_header,
                "openai-sentinel-turnstile-token": self.auth.turnstile_token
                if (requirements.get("turnstile") or {}).get("required")
                else None,
            }
        )
        self._emit_event(
            on_event,
            "prepared_conversation_write_started",
            client_prepare_state="success",
            conduit_token_present=True,
            turn_trace_id_present=True,
        )
        start_finalized_sentinel_bundle_refill(self, on_event=on_event)

        # `_stream_backend_payload()` owns the actual SSE loop and calls
        # `_parse_event()` for every parsed payload. Capture only an allowlisted
        # subset of that real parser state instead of exposing raw SSE events or
        # sensitive handoff/resume material outside the transport. A handoff is
        # retained only as a boolean so a partial prefix can never be mistaken
        # for a completed response.
        original_parse_event = self._parse_event
        instance_dict = getattr(self, "__dict__", None)
        had_instance_parse_event = (
            isinstance(instance_dict, dict) and "_parse_event" in instance_dict
        )
        previous_instance_parse_event = (
            instance_dict.get("_parse_event") if had_instance_parse_event else None
        )

        def capture_parse_event(event_payload: Any, state: dict[str, Any]):
            if (
                isinstance(event_payload, dict)
                and event_payload.get("type") == "stream_handoff"
            ):
                stream_diagnostics["handoff_seen"] = True
            tokens, maybe_title = original_parse_event(event_payload, state)
            _copy_safe_stream_diagnostics(state, stream_diagnostics)
            return tokens, maybe_title

        # Expanded send instrumentation already emits one structured
        # `assistant_token` event from the on_token callback. The transport also
        # emits its own assistant_token event, so suppress only that duplicate
        # while forwarding any other transport event unchanged.
        def forward_non_token_stream_event(event: dict[str, Any]) -> None:
            if isinstance(event, dict) and event.get("type") == "assistant_token":
                return
            if on_event is not None:
                on_event(event)

        self._parse_event = capture_parse_event
        try:
            observed_conversation_id, observed_message_id, text = (
                self._stream_backend_payload(
                    payload,
                    headers,
                    on_token=on_token,
                    on_event=forward_non_token_stream_event,
                )
            )
        finally:
            if had_instance_parse_event:
                self._parse_event = previous_instance_parse_event
            else:
                try:
                    delattr(self, "_parse_event")
                except AttributeError:
                    self._parse_event = original_parse_event

        observed_model = stream_diagnostics.get("observed_model")
        observed_reasoning_effort = stream_diagnostics.get("observed_reasoning_effort")
        finish_reason = stream_diagnostics.get("finish_reason")
        handoff_seen = bool(stream_diagnostics.get("handoff_seen"))

        effective_conversation_id = observed_conversation_id or conversation_id
        if (
            handoff_seen
            or not text
            or not observed_message_id
            or observed_message_id == parent_message_id
        ):
            streamed_prefix = text
            message, polled_text, _polled_payload = self._poll_conversation_after_prepare(
                effective_conversation_id,
                previous_message_id=parent_message_id,
                timeout=max(
                    DEFAULT_STREAM_RECOVERY_POLL_TIMEOUT_SECONDS,
                    float(self.timeout),
                ),
                interval=DEFAULT_STREAM_RECOVERY_POLL_INTERVAL_SECONDS,
                on_token=None if streamed_prefix else on_token,
                on_event=on_event,
                reason="prepared_text_send_handoff_recovery"
                if handoff_seen
                else "prepared_text_send_recovery",
                allow_global_fallback=False,
            )
            if handoff_seen and not isinstance(message, dict):
                raise RequestError(
                    "prepared stream handoff recovery did not reach a completed assistant message",
                    request_stage="prepared_stream_handoff_recovery",
                )
            if isinstance(message, dict):
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id:
                    observed_message_id = message_id
                if polled_text:
                    if (
                        streamed_prefix
                        and on_token is not None
                        and polled_text.startswith(streamed_prefix)
                    ):
                        suffix = polled_text[len(streamed_prefix) :]
                        if suffix:
                            on_token(suffix)
                    text = polled_text
                polled_finish_reason = _finish_reason(message)
                if polled_finish_reason:
                    finish_reason = polled_finish_reason
                diagnostics: dict[str, Any] = {}
                self._capture_message_diagnostics(message, diagnostics)
                if diagnostics.get("observed_model") is not None:
                    observed_model = diagnostics.get("observed_model")
                if diagnostics.get("observed_reasoning_effort") is not None:
                    observed_reasoning_effort = diagnostics.get("observed_reasoning_effort")
        observed_conversation_id = effective_conversation_id
    finally:
        _clear_prefetched_requirements(self)

    total_latency = time.perf_counter() - started_at
    self._emit_event(
        on_event,
        "prepared_conversation_write_completed",
        conversation_id_present=bool(observed_conversation_id),
        message_id_present=bool(observed_message_id),
        response_text_present=bool(text),
        handoff_recovery_used=bool(stream_diagnostics.get("handoff_seen")),
    )
    return ChatResponse(
        text=text,
        conversation=ChatConversation(
            conversation_id=observed_conversation_id or conversation_id,
            message_id=observed_message_id or parent_message_id,
            user_id=conversation_dict.get("user_id"),
            finish_reason=finish_reason or "stop",
            parent_message_id=observed_message_id or parent_message_id,
            is_thinking=False,
        ),
        metrics=ChatMetrics(total=total_latency),
        request=ChatRequestDiagnostics(
            requested_model=model.strip()
            if isinstance(model, str) and model.strip()
            else None,
            requested_reasoning_effort=reasoning_effort.strip()
            if isinstance(reasoning_effort, str) and reasoning_effort.strip()
            else None,
            sent_model=resolved_model,
            sent_reasoning_effort=normalized_effort,
            conversation_id=observed_conversation_id or conversation_id,
            parent_message_id=parent_message_id,
            is_continuation=True,
            web_search=web_search,
            temporary=temporary,
            has_media=False,
            message_count=len(messages),
            observed_model=observed_model,
            observed_reasoning_effort=observed_reasoning_effort,
        ),
    )
