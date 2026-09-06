from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chatgpt_web_adapter"


def test_pr86_reclassifies_without_runtime_deprecation_warning() -> None:
    init_source = (SRC / "__init__.py").read_text(encoding="utf-8")
    surface_source = (SRC / "public_surface.py").read_text(encoding="utf-8")

    assert "warnings.warn" not in init_source
    assert "warnings.warn" not in surface_source
    assert "DeprecationWarning" not in init_source
    assert "DeprecationWarning" not in surface_source


def test_public_surface_registry_is_policy_only_not_transport_implementation() -> None:
    source = (SRC / "public_surface.py").read_text(encoding="utf-8")

    for forbidden in (
        "browser_owned_write_runtime",
        "browser_native_provider",
        "sentinel_transaction",
        "send_text(",
        "send_browser_native(",
        "turnstile",
        "proof_token",
        "chrome.debugger",
    ):
        assert forbidden not in source


def test_product_runtime_still_has_no_legacy_write_fallback() -> None:
    shell = (SRC / "product_runtime.py").read_text(encoding="utf-8")
    core = (SRC / "product_runtime_core.py").read_text(encoding="utf-8")

    assert '"fallback_transport": None' in core
    assert '"legacy_direct_write_fallback": False' in core
    for source in (shell, core):
        assert "self.client.send(" not in source
        assert "send_to_conversation(" not in source
        assert "send_payload(" not in source
    assert "auto_sentinel=False" in shell


def test_legacy_and_low_level_symbols_remain_present_for_compatibility() -> None:
    init_source = (SRC / "__init__.py").read_text(encoding="utf-8")

    assert "ChatGPTWebClient" in init_source
    assert "WebChatClient = ChatGPTWebClient" in init_source
    assert "SentinelBundleProvider" in init_source
    assert "BrowserNativeTurnProvider" in init_source
    assert "PayloadBuilder" in init_source
