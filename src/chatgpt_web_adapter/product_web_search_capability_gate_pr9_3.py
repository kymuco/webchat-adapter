from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from .browser_native_client import _provider_supports_revision_safe_streaming
from .product_capabilities import (
    WEB_SEARCH,
    CapabilityState,
    ProductCapabilities,
    ProductCapability,
)

if TYPE_CHECKING:
    from .browser_owned_product_transport import BrowserOwnedProductTransport

_PR93_WEB_SEARCH_CAPABILITY_GATE_MARKER = "__pr93_web_search_capability_gate__"
_PR93_WEB_SEARCH_LIVE_EVIDENCE = (
    "PR9.3 live web-search observation gate: one browser-owned ordinary ChatGPT turn "
    "observed SEARCH activity plus canonical SOURCE and CITATION evidence before "
    "canonical_text_finalized; citation-to-source relation and ranges were valid, "
    "canonical completion was proven by CANONICAL_READBACK, automatic write retry was "
    "false, fallback transport was null, private-thought text was not exposed, and no "
    "observation events were dropped"
)


def gate_browser_owned_web_search_capability(
    capabilities: Callable[..., ProductCapabilities],
) -> Callable[..., ProductCapabilities]:
    """Graduate WEB_SEARCH only for providers with the proven observation channel."""

    if getattr(capabilities, _PR93_WEB_SEARCH_CAPABILITY_GATE_MARKER, False):
        return capabilities

    @wraps(capabilities)
    def gated(
        self: BrowserOwnedProductTransport,
        *args: Any,
        **kwargs: Any,
    ) -> ProductCapabilities:
        declared = capabilities(self, *args, **kwargs)
        if not isinstance(declared, ProductCapabilities):
            raise TypeError(
                "BrowserOwnedProductTransport.capabilities() must return "
                "ProductCapabilities"
            )

        current = declared.get(WEB_SEARCH)
        if current is None or current.state is not CapabilityState.UNKNOWN:
            return declared

        provider = getattr(self, "provider", None)
        if not _provider_supports_revision_safe_streaming(provider):
            return declared

        entries = tuple(
            ProductCapability(
                name=entry.name,
                state=CapabilityState.AVAILABLE,
                owner=entry.owner,
                evidence=_PR93_WEB_SEARCH_LIVE_EVIDENCE,
            )
            if entry.name == WEB_SEARCH
            else entry
            for entry in declared.entries
        )
        return ProductCapabilities.from_entries(
            transport=declared.transport,
            product_semantics=declared.product_semantics,
            entries=entries,
        )

    setattr(gated, _PR93_WEB_SEARCH_CAPABILITY_GATE_MARKER, True)
    return gated


def install_browser_owned_web_search_capability_gate() -> None:
    """Compatibility installer for callers that explicitly request legacy wiring."""

    from .browser_owned_product_transport import BrowserOwnedProductTransport

    current = BrowserOwnedProductTransport.capabilities
    BrowserOwnedProductTransport.capabilities = gate_browser_owned_web_search_capability(
        current
    )
