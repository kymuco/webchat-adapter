from __future__ import annotations

from typing import Any

from . import legacy_client_core as _core
from .attach import attach_conversation as _attach_conversation
from .auth_refresh import refresh_auth_session as _refresh_auth_session
from .browser_native_client import (
    send_browser_native as _send_browser_native,
    set_browser_native_turn_provider as _set_browser_native_turn_provider,
)
from .browserless_request_guards import gate_browserless_poll_deadline
from .conversation_send import send_to_conversation as _send_to_conversation
from .diagnostic_metrics import send_with_expanded_metrics as _send_with_expanded_metrics
from .export import export_conversation as _export_conversation
from .messages import get_messages as _get_messages
from .model_registry import (
    DEFAULT_MODEL as DEFAULT_MODEL,
    DEFAULT_THINKING_MODEL as DEFAULT_THINKING_MODEL,
    MODEL_ALIASES as MODEL_ALIASES,
    normalize_reasoning_effort as _normalize_reasoning_effort,
    resolve_model as _resolve_model,
)
from .payload_validation import validate_payload as _validate_payload
from .policy_approval import approve_pending_action as _policy_approve_pending_action
from .policy_approval import send_and_auto_approve as _policy_send_and_auto_approve
from .policy_approval import (
    wait_and_approve_pending_actions as _policy_wait_and_approve_pending_actions,
)
from .prepared_text_send import send_existing_text_prepared as _send_existing_text_prepared
from .raw_payload import send_payload as _send_payload
from .required_action import get_required_action as _get_required_action
from .sentinel_bundle import (
    gate_prepared_build_headers as _gate_prepared_build_headers,
    gate_prepared_get_ready_requirements as _gate_prepared_get_ready_requirements,
    gate_prepared_text_send as _gate_prepared_text_send,
    get_prepared_sentinel_bundle as _get_prepared_sentinel_bundle,
    prefetch_finalized_sentinel_bundle as _prefetch_finalized_sentinel_bundle,
    redact_ephemeral_write_headers as _redact_ephemeral_write_headers,
    start_finalized_sentinel_bundle_refill as _start_finalized_sentinel_bundle_refill,
)
from .sentinel_transaction import (
    set_sentinel_bundle_provider as _set_sentinel_bundle_provider,
    set_sentinel_challenge_provider as _set_sentinel_challenge_provider,
)
from .status import get_pending_approval as _get_pending_approval
from .status import get_status as _get_status
from .wait import wait_until_completed as _wait_until_completed
from .web_session import (
    gate_debug_trace_writer as _gate_debug_trace_writer,
    gate_get_ready_requirements as _gate_get_ready_requirements,
    redact_web_session_headers as _redact_web_session_headers,
)

# Stable constants used by modules that participate in client composition.
DEFAULT_TIMEOUT_SECONDS = _core.DEFAULT_TIMEOUT_SECONDS
DEFAULT_STREAM_RECOVERY_POLL_TIMEOUT_SECONDS = (
    _core.DEFAULT_STREAM_RECOVERY_POLL_TIMEOUT_SECONDS
)
DEFAULT_STREAM_RECOVERY_POLL_INTERVAL_SECONDS = (
    _core.DEFAULT_STREAM_RECOVERY_POLL_INTERVAL_SECONDS
)


def __getattr__(name: str) -> Any:
    """Delegate untouched legacy module attributes to the frozen core.

    ``ChatGPTWebClient`` deliberately does not delegate while this module is being
    initialized: a composition helper importing the class too early must fail
    loudly rather than silently capture the uncomposed legacy base class.
    """

    if name == "ChatGPTWebClient":
        raise AttributeError(name)
    return getattr(_core, name)


_original_send = _core.ChatGPTWebClient.send


class ChatGPTWebClient(_core.ChatGPTWebClient):
    """Explicitly composed compatibility client over the frozen historical core."""

    _normalize_reasoning_effort = staticmethod(_normalize_reasoning_effort)
    _resolve_model = staticmethod(_resolve_model)
    _poll_conversation_after_prepare = gate_browserless_poll_deadline(
        _core.ChatGPTWebClient._poll_conversation_after_prepare
    )

    _get_ready_requirements = _gate_prepared_get_ready_requirements(
        _gate_get_ready_requirements(_core.ChatGPTWebClient._get_ready_requirements)
    )
    _get_prepared_sentinel_bundle = _get_prepared_sentinel_bundle
    prefetch_sentinel_bundle = _prefetch_finalized_sentinel_bundle
    start_sentinel_bundle_refill = _start_finalized_sentinel_bundle_refill
    set_sentinel_challenge_provider = _set_sentinel_challenge_provider
    set_sentinel_bundle_provider = _set_sentinel_bundle_provider
    set_browser_native_turn_provider = _set_browser_native_turn_provider
    send_browser_native = _send_browser_native
    refresh_auth = _refresh_auth_session

    _build_headers = _gate_prepared_build_headers(_core.ChatGPTWebClient._build_headers)
    _sanitize_header_value = _redact_ephemeral_write_headers(
        _redact_web_session_headers(_core.ChatGPTWebClient._sanitize_header_value)
    )
    _write_debug_trace = _gate_debug_trace_writer(
        _core.ChatGPTWebClient._write_debug_trace
    )

    approve_pending_action = _policy_approve_pending_action(
        _core.ChatGPTWebClient.approve_pending_action
    )
    attach_conversation = _attach_conversation
    export_conversation = _export_conversation
    get_messages = _get_messages
    get_pending_approval = _get_pending_approval
    get_required_action = _get_required_action
    get_status = _get_status

    send = _send_with_expanded_metrics(
        _gate_prepared_text_send(_original_send, require_provider=False)
    )
    _send_existing_text_prepared = _send_with_expanded_metrics(
        _gate_prepared_text_send(_send_existing_text_prepared)
    )
    send_and_auto_approve = _policy_send_and_auto_approve(
        _core.ChatGPTWebClient.send_and_auto_approve
    )
    send_payload = _send_payload
    send_to_conversation = _send_to_conversation
    wait_and_approve_pending_actions = _policy_wait_and_approve_pending_actions
    wait_until_completed = _wait_until_completed


# Keep the historical module-level validation helper available through the public
# module without mutating the frozen legacy core.
validate_payload = _validate_payload
