from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chatgpt_web_adapter"


def test_product_runtime_has_no_legacy_direct_write_fallback() -> None:
    runtime_shell = (SRC / "product_runtime.py").read_text(encoding="utf-8")
    runtime_core = (SRC / "product_runtime_core.py").read_text(encoding="utf-8")
    transport_source = (SRC / "product_transport.py").read_text(encoding="utf-8")

    assert 'DEFAULT_PRODUCT_TRANSPORT = BROWSER_OWNED_PRODUCT_TRANSPORT' in transport_source
    assert 'BROWSERLESS_REQUEST_PRODUCT_TRANSPORT = "browserless-request"' in transport_source
    assert "BROWSER_OWNED_PRODUCT_TRANSPORT," in transport_source
    assert "BROWSERLESS_REQUEST_PRODUCT_TRANSPORT," in transport_source
    assert '"fallback_transport": None' in runtime_core
    assert '"legacy_direct_write_fallback": False' in runtime_core
    for source in (runtime_shell, runtime_core):
        assert "self.client.send(" not in source
        assert "send_to_conversation(" not in source
        assert "send_payload(" not in source
        assert "CHAT_BACKEND_URL" not in source
        assert "proof_token" not in source.lower()
        assert "turnstile" not in source.lower()


def test_product_runtime_assembly_is_noninteractive_non_sentinel_and_interface_based() -> None:
    runtime_source = (SRC / "product_runtime.py").read_text(encoding="utf-8")
    adapter_shell = (SRC / "browser_owned_product_transport.py").read_text(
        encoding="utf-8"
    )
    adapter_core = (SRC / "browser_owned_product_transport_core.py").read_text(
        encoding="utf-8"
    )

    assert "auto_login=False" in runtime_source
    assert "auto_sentinel=False" in runtime_source
    assert "auto_refresh_auth=auto_refresh_auth" in runtime_source
    assert "ProductWriteTransport" in runtime_source
    assert "CanonicalConversationClient" in runtime_source
    assert "BrowserOwnedProductWriteRuntime" not in runtime_source
    assert "class BrowserOwnedProductTransport" in adapter_shell
    assert "BrowserOwnedProductWriteRuntime" in adapter_core


def test_cli_uses_same_product_runtime_assembly_contract() -> None:
    source = (SRC / "cli.py").read_text(encoding="utf-8")
    assert 'commands.add_parser(\n        "runtime"' in source
    assert 'runtime_commands.add_parser(\n        "status"' in source
    assert 'runtime_commands.add_parser(\n        "send"' in source
    assert "assemble_product_runtime(" in source
    assert "runtime.send_text_observed(" in source
