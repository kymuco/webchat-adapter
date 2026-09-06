from __future__ import annotations

from typing import Any

from . import browserless_request_transport_core as _core
from .browserless_request_guards import gate_browserless_canonical_finalize
from .browserless_request_scope import (
    gate_browserless_request_execute,
    gate_browserless_request_health,
)
from .browserless_shared_write_fence import gate_browserless_transport_init

BrowserlessRequestTransportError = _core.BrowserlessRequestTransportError
BrowserlessChallengeBoundaryError = _core.BrowserlessChallengeBoundaryError
BrowserlessProtocolDriftError = _core.BrowserlessProtocolDriftError
BrowserlessRequestObservation = _core.BrowserlessRequestObservation


class BrowserlessRequestTransport(_core.BrowserlessRequestTransport):
    """Explicitly composed browserless transport over the frozen historical core."""

    __init__ = gate_browserless_transport_init(
        _core.BrowserlessRequestTransport.__init__
    )
    _canonical_finalize = gate_browserless_canonical_finalize(
        _core.BrowserlessRequestTransport._canonical_finalize
    )
    health = gate_browserless_request_health(_core.BrowserlessRequestTransport.health)
    _execute = gate_browserless_request_execute(
        _core.BrowserlessRequestTransport._execute
    )


def __getattr__(name: str) -> Any:
    """Delegate untouched implementation details to the frozen legacy core."""

    return getattr(_core, name)
