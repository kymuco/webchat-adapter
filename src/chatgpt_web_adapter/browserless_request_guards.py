from __future__ import annotations

import time as _stdlib_time
from contextvars import ContextVar
from functools import update_wrapper, wraps
from types import FunctionType
from typing import Any, Callable

from .browserless_request_scope import _BROWSERLESS_REQUEST_SCOPE_OWNER


_POLL_SLEEP_DEADLINE: ContextVar[float | None] = ContextVar(
    "browserless_poll_sleep_deadline",
    default=None,
)


class _DeadlineAwareClientTime:
    """Proxy a client method's time API while clamping scoped poll sleeps."""

    _cwa_browserless_poll_time_proxy = True

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def sleep(self, seconds: float) -> None:
        deadline = _POLL_SLEEP_DEADLINE.get()
        if deadline is None:
            self._delegate.sleep(seconds)
            return

        remaining = deadline - self._delegate.monotonic()
        if remaining <= 0:
            return
        requested = max(0.0, float(seconds))
        self._delegate.sleep(min(requested, remaining))


def _deadline_aware_poll_clone(original: Callable[..., Any]) -> Callable[..., Any]:
    """Clone a legacy poller with a private deadline-aware ``time`` global.

    The historical method can stay byte-identical in the legacy core. Only the
    cloned function sees the proxy, so no module global or stdlib ``time`` object
    is mutated during import or execution.
    """

    if not isinstance(original, FunctionType):
        raise TypeError("browserless poll deadline guard requires a Python function")
    delegate_time = original.__globals__.get("time")
    if delegate_time is None:
        raise RuntimeError("conversation recovery poller does not expose time")
    guarded_globals = dict(original.__globals__)
    guarded_globals["time"] = _DeadlineAwareClientTime(delegate_time)
    cloned = FunctionType(
        original.__code__,
        guarded_globals,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    cloned.__kwdefaults__ = original.__kwdefaults__
    cloned.__annotations__ = dict(getattr(original, "__annotations__", {}))
    update_wrapper(cloned, original)
    return cloned


def gate_browserless_poll_deadline(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Return a statically composable browserless deadline guard."""

    if getattr(original, "_cwa_browserless_poll_deadline_guard", False):
        return original
    guarded_original = _deadline_aware_poll_clone(original)

    @wraps(original)
    def poll(
        self: Any,
        conversation_id: str,
        *,
        previous_message_id: str | None,
        timeout: float,
        interval: float,
        on_token: Any = None,
        on_event: Any = None,
        reason: str = "approval_poll",
        allow_global_fallback: bool = True,
    ) -> Any:
        target = (
            original
            if _BROWSERLESS_REQUEST_SCOPE_OWNER.get() is None
            else guarded_original
        )
        if target is original:
            return target(
                self,
                conversation_id,
                previous_message_id=previous_message_id,
                timeout=timeout,
                interval=interval,
                on_token=on_token,
                on_event=on_event,
                reason=reason,
                allow_global_fallback=allow_global_fallback,
            )

        deadline = _stdlib_time.monotonic() + max(0.0, float(timeout))
        token = _POLL_SLEEP_DEADLINE.set(deadline)
        try:
            return target(
                self,
                conversation_id,
                previous_message_id=previous_message_id,
                timeout=timeout,
                interval=interval,
                on_token=on_token,
                on_event=on_event,
                reason=reason,
                allow_global_fallback=allow_global_fallback,
            )
        finally:
            _POLL_SLEEP_DEADLINE.reset(token)

    setattr(poll, "_cwa_browserless_poll_deadline_guard", True)
    return poll


def install_browserless_poll_deadline_guard(
    client_module: Any,
    client_class: type[Any],
) -> None:
    """Compatibility installer for callers that still request the old surface.

    New production composition uses :func:`gate_browserless_poll_deadline` inside
    the public client class body and therefore performs no import-time mutation.
    ``client_module`` is retained only for source compatibility.
    """

    del client_module
    original = getattr(client_class, "_poll_conversation_after_prepare", None)
    if not callable(original):
        raise RuntimeError("ChatGPTWebClient is missing conversation recovery polling")
    client_class._poll_conversation_after_prepare = gate_browserless_poll_deadline(
        original
    )


def _normalized_message_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


class _StaleCompletedStatusView:
    """Expose a foreign completed snapshot as pending without mutating it."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    @property
    def status(self) -> str:
        return "in_progress"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _SubmittedTurnCanonicalClientView:
    """Make completion polling wait for the submitted assistant identity."""

    def __init__(self, delegate: Any, submitted_message_id: str) -> None:
        self._delegate = delegate
        self._submitted_message_id = submitted_message_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def get_status(self, conversation: Any) -> Any:
        status = self._delegate.get_status(conversation)
        if getattr(status, "status", None) != "completed":
            return status
        status_message_id = _normalized_message_id(
            getattr(status, "message_id", None)
        )
        if status_message_id == self._submitted_message_id:
            return status
        return _StaleCompletedStatusView(status)


class _CanonicalFinalizeTransportView:
    """Override only canonical status observation for one finalize invocation."""

    def __init__(self, delegate: Any, canonical_client: Any) -> None:
        self._delegate = delegate
        self.canonical_client = canonical_client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def gate_browserless_canonical_finalize(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    """Require completion and readback to identify the submitted assistant."""

    @wraps(original)
    def canonical_finalize(
        self: Any,
        response: Any,
        *,
        previous_message_id: str | None,
        timeout: float,
        poll_interval: float,
    ) -> Any:
        conversation = getattr(response, "conversation", None)
        submitted_message_id = _normalized_message_id(
            getattr(conversation, "message_id", None)
        )
        if submitted_message_id is None:
            from .browserless_request_transport import BrowserlessRequestTransportError

            raise BrowserlessRequestTransportError(
                "submitted browserless assistant identity is missing; canonical "
                "finality cannot be correlated to this turn",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )

        canonical_client = getattr(self, "canonical_client")
        polling_client = _SubmittedTurnCanonicalClientView(
            canonical_client,
            submitted_message_id,
        )
        polling_self = _CanonicalFinalizeTransportView(self, polling_client)
        result = original(
            polling_self,
            response,
            previous_message_id=previous_message_id,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        status, canonical_assistant, _text = result
        status_message_id = _normalized_message_id(
            getattr(status, "message_id", None)
        )
        canonical_message_id = _normalized_message_id(
            getattr(canonical_assistant, "message_id", None)
        )

        if status_message_id != submitted_message_id:
            from .browserless_request_transport import BrowserlessRequestTransportError

            raise BrowserlessRequestTransportError(
                "canonical completion status identity does not match the submitted "
                "browserless turn",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )

        if canonical_message_id != submitted_message_id:
            from .browserless_request_transport import BrowserlessRequestTransportError

            raise BrowserlessRequestTransportError(
                "canonical readback assistant identity does not match the submitted "
                "browserless turn",
                request_stage="canonical_reconciliation",
                write_may_have_been_submitted=True,
                reconciliation_required=True,
            )
        return result

    return canonical_finalize
