from __future__ import annotations

from dataclasses import replace
from functools import wraps
from typing import Any, Callable

from .product_artifact_observation_pr10_1 import ProductArtifactObservationCollector
from .product_transport import ProductRuntimeExecution

_PR93_PRODUCT_OBSERVATION_GATE_MARKER = "__pr93_product_observation_gate__"


def gate_product_runtime_send_text_observed(
    send_text_observed: Callable[..., ProductRuntimeExecution],
) -> Callable[..., ProductRuntimeExecution]:
    """Attach runtime-owned typed observations to observed product executions.

    The wrapped runtime remains the sole owner of product write/provenance/finality.
    This gate only listens to the already-standardized ``on_event`` stream and
    replaces the returned execution's observation tuple with collector-owned,
    privacy-filtered values. A transport cannot acquire typed-observation authority
    by pre-populating ``ProductRuntimeExecution.observations`` itself.

    The function is intentionally side-effect-free at import/composition time.
    Historical activity precedence, browser-owned capability graduation, submission
    lifecycle, and UI-liveness ownership now live in their intrinsic modules/classes
    rather than being installed as a consequence of importing this gate.

    Canonical source/citation observation still uses the historical runtime-time
    compatibility gate. That gate is installed only when an observed execution is
    actually requested; eliminating that call-time patch is outside PR12.3's
    import-time mutation scope.
    """

    if getattr(send_text_observed, _PR93_PRODUCT_OBSERVATION_GATE_MARKER, False):
        return send_text_observed

    @wraps(send_text_observed)
    def gated(
        self: Any,
        text: str,
        *args: Any,
        **kwargs: Any,
    ) -> ProductRuntimeExecution:
        from .canonical_product_observation_gate_pr9_3 import (
            install_canonical_product_observation_gate,
        )

        install_canonical_product_observation_gate()

        caller_on_event = kwargs.get("on_event")
        collector = ProductArtifactObservationCollector()

        def collect_and_forward(event: dict[str, Any]) -> None:
            try:
                collector.consume(event)
            except Exception:
                # Structured observation is explicitly non-authoritative. A
                # collector defect cannot invalidate or replay a delegated write.
                collector.dropped_event_count += 1
            if caller_on_event is not None:
                caller_on_event(event)

        kwargs["on_event"] = collect_and_forward
        execution = send_text_observed(self, text, *args, **kwargs)
        if not isinstance(execution, ProductRuntimeExecution):
            raise TypeError(
                "ChatGPTProductRuntime.send_text_observed() must return "
                "ProductRuntimeExecution"
            )

        return replace(
            execution,
            observations=collector.observations,
            dropped_observation_event_count=collector.dropped_event_count,
        )

    setattr(gated, _PR93_PRODUCT_OBSERVATION_GATE_MARKER, True)
    return gated
