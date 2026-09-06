from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chatgpt_web_adapter"


def test_capability_and_provenance_models_are_browser_independent() -> None:
    capabilities = (SRC / "product_capabilities.py").read_text(encoding="utf-8")
    provenance = (SRC / "product_provenance.py").read_text(encoding="utf-8")

    for source in (capabilities, provenance):
        for forbidden in (
            "browser_native",
            "BrowserNative",
            "chrome.debugger",
            "runtime_tab_id",
            "Sentinel",
            "turnstile",
            "proof_token",
        ):
            assert forbidden not in source


def test_provenance_model_preserves_nullable_finish_reason() -> None:
    source = (SRC / "product_provenance.py").read_text(encoding="utf-8")

    assert "finish_reason_observed" in source
    assert "finish_reason=\"stop\"" not in source
    assert "finality_detail=None" in source
    assert "CompletionSource.CANONICAL_READBACK" in source


def test_capability_model_does_not_collapse_four_states_to_boolean() -> None:
    source = (SRC / "product_capabilities.py").read_text(encoding="utf-8")

    for state in ("AVAILABLE", "UNSUPPORTED", "UNKNOWN", "UNIMPLEMENTED"):
        assert f'{state} = "{state}"' in source
    assert "bool(state)" not in source


def test_pr85_does_not_modify_proven_browser_write_runtime_or_extension_contract() -> None:
    writer = (SRC / "browser_owned_write_runtime.py").read_text(encoding="utf-8")
    adapter = (SRC / "browser_owned_product_transport_core.py").read_text(
        encoding="utf-8"
    )

    assert "class BrowserOwnedProductWriteRuntime" in writer
    assert "self._runtime.send_text(" in adapter
    assert "self._runtime.send_text_observed(" in adapter
