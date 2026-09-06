from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from .product_capabilities import (
    FILES,
    IMAGES,
    MULTIMODAL_CONTINUATION,
    CapabilityState,
    ProductCapabilities,
    ProductCapability,
)
from .product_model_profile_pr8_10 import ProductModelProfileProvider

if TYPE_CHECKING:
    from .browser_owned_product_transport import BrowserOwnedProductTransport

_PR94_RICH_INPUT_CAPABILITY_GATE_MARKER = "__pr94_rich_input_capability_gate__"
_PR94_RICH_INPUT_CAPABILITY_NAMES = frozenset(
    {IMAGES, FILES, MULTIMODAL_CONTINUATION}
)
_PR94_RICH_INPUT_LIVE_EVIDENCE = (
    "PR9.2 schema-29 authenticated live closure: image new chat, general file new chat, "
    "and multimodal continuation each produced attachment-dependent answers with exact "
    "attachment count, validated-click request-body correlation, CANONICAL_READBACK "
    "finality, no automatic write retry, and no fallback transport"
)
_PR94_PROVEN_SEND_TEXT_IMPLEMENTATION = ProductModelProfileProvider.send_text
_PR94_PROVEN_RPC_IMPLEMENTATION = ProductModelProfileProvider._rpc


def _uses_frozen_bound_implementation(
    provider: Any,
    name: str,
    implementation: Callable[..., Any],
) -> bool:
    value = getattr(provider, name, None)
    if not callable(value):
        return False
    return getattr(value, "__func__", value) is implementation


def _provider_uses_proven_pr92_rich_input_path(provider: Any) -> bool:
    """Return whether a provider preserves the live-proven PR9.2 write path."""

    if not isinstance(provider, ProductModelProfileProvider):
        return False
    return (
        _uses_frozen_bound_implementation(
            provider,
            "send_text",
            _PR94_PROVEN_SEND_TEXT_IMPLEMENTATION,
        )
        and _uses_frozen_bound_implementation(
            provider,
            "_rpc",
            _PR94_PROVEN_RPC_IMPLEMENTATION,
        )
    )


def gate_browser_owned_rich_input_capabilities(
    capabilities: Callable[..., ProductCapabilities],
) -> Callable[..., ProductCapabilities]:
    """Graduate PR9.2 rich-input capabilities only on the proven provider path."""

    if getattr(capabilities, _PR94_RICH_INPUT_CAPABILITY_GATE_MARKER, False):
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

        provider = getattr(self, "provider", None)
        if not _provider_uses_proven_pr92_rich_input_path(provider):
            return declared

        changed = False
        entries: list[ProductCapability] = []
        for entry in declared.entries:
            if (
                entry.name in _PR94_RICH_INPUT_CAPABILITY_NAMES
                and entry.state is not CapabilityState.AVAILABLE
            ):
                changed = True
                entries.append(
                    ProductCapability(
                        name=entry.name,
                        state=CapabilityState.AVAILABLE,
                        owner=entry.owner,
                        evidence=_PR94_RICH_INPUT_LIVE_EVIDENCE,
                    )
                )
            else:
                entries.append(entry)

        if not changed:
            return declared
        return ProductCapabilities.from_entries(
            transport=declared.transport,
            product_semantics=declared.product_semantics,
            entries=entries,
        )

    setattr(gated, _PR94_RICH_INPUT_CAPABILITY_GATE_MARKER, True)
    return gated


def install_browser_owned_rich_input_capability_gate() -> None:
    """Compatibility installer for callers that explicitly request legacy wiring."""

    from .browser_owned_product_transport import BrowserOwnedProductTransport

    current = BrowserOwnedProductTransport.capabilities
    BrowserOwnedProductTransport.capabilities = gate_browser_owned_rich_input_capabilities(
        current
    )
