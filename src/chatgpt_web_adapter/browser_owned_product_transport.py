from __future__ import annotations

from typing import Any

from . import browser_owned_product_transport_core as _core
from .product_rich_input_capability_gate_pr9_4 import (
    gate_browser_owned_rich_input_capabilities,
)
from .product_web_search_capability_gate_pr9_3 import (
    gate_browser_owned_web_search_capability,
)


class BrowserOwnedProductTransport(_core.BrowserOwnedProductTransport):
    """Browser-owned transport with statically composed proven capabilities."""

    capabilities = gate_browser_owned_rich_input_capabilities(
        gate_browser_owned_web_search_capability(
            _core.BrowserOwnedProductTransport.capabilities
        )
    )


def __getattr__(name: str) -> Any:
    """Delegate untouched implementation details to the frozen legacy core."""

    return getattr(_core, name)
