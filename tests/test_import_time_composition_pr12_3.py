from __future__ import annotations

import ast
import importlib
from pathlib import Path

import chatgpt_web_adapter
import chatgpt_web_adapter.product_observations as product_observations
import chatgpt_web_adapter.product_runtime_observation_gate as observation_gate
from chatgpt_web_adapter.browser_owned_product_transport import (
    BrowserOwnedProductTransport,
)
from chatgpt_web_adapter.browserless_request_transport import BrowserlessRequestTransport
from chatgpt_web_adapter.client import ChatGPTWebClient
from chatgpt_web_adapter.product_runtime import ChatGPTProductRuntime

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "chatgpt_web_adapter"


def _assert_no_top_level_runtime_mutation(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            assert all(not isinstance(target, ast.Attribute) for target in node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            assert not isinstance(node.target, ast.Attribute)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            function = node.value.func
            if isinstance(function, ast.Name):
                assert not function.id.startswith("install_")


def test_package_root_is_export_only_without_class_mutation() -> None:
    _assert_no_top_level_runtime_mutation(PACKAGE / "__init__.py")
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    for token in (
        "ChatGPTWebClient.send =",
        "ChatGPTProductRuntime.send_text_observed =",
        "_BrowserlessRequestTransport.__init__ =",
        "_BrowserlessRequestTransport._execute =",
    ):
        assert token not in source


def test_observation_gate_import_is_side_effect_free() -> None:
    before = (
        ChatGPTProductRuntime.send_text_observed,
        BrowserOwnedProductTransport.capabilities,
        product_observations._activity_observation_kind,
    )

    importlib.reload(observation_gate)

    after = (
        ChatGPTProductRuntime.send_text_observed,
        BrowserOwnedProductTransport.capabilities,
        product_observations._activity_observation_kind,
    )
    assert after == before
    _assert_no_top_level_runtime_mutation(
        PACKAGE / "product_runtime_observation_gate.py"
    )


def test_package_reload_preserves_composed_class_and_method_identity() -> None:
    classes_before = (
        ChatGPTWebClient,
        BrowserlessRequestTransport,
        BrowserOwnedProductTransport,
        ChatGPTProductRuntime,
    )
    methods_before = (
        ChatGPTWebClient.send,
        BrowserlessRequestTransport._execute,
        BrowserOwnedProductTransport.capabilities,
        ChatGPTProductRuntime.send_text_observed,
        ChatGPTProductRuntime.submit,
        ChatGPTProductRuntime.await_final,
        ChatGPTProductRuntime.observe_ui_liveness,
    )

    reloaded = importlib.reload(chatgpt_web_adapter)

    assert reloaded.ChatGPTWebClient is classes_before[0]
    assert reloaded.WebChatClient is classes_before[0]
    assert reloaded.ChatGPTProductRuntime is classes_before[3]
    assert (
        ChatGPTWebClient,
        BrowserlessRequestTransport,
        BrowserOwnedProductTransport,
        ChatGPTProductRuntime,
    ) == classes_before
    assert (
        ChatGPTWebClient.send,
        BrowserlessRequestTransport._execute,
        BrowserOwnedProductTransport.capabilities,
        ChatGPTProductRuntime.send_text_observed,
        ChatGPTProductRuntime.submit,
        ChatGPTProductRuntime.await_final,
        ChatGPTProductRuntime.observe_ui_liveness,
    ) == methods_before


def test_public_runtime_and_transports_own_static_composition_points() -> None:
    assert "send" in ChatGPTWebClient.__dict__
    assert "_poll_conversation_after_prepare" in ChatGPTWebClient.__dict__
    assert "__init__" in BrowserlessRequestTransport.__dict__
    assert "_execute" in BrowserlessRequestTransport.__dict__
    assert "capabilities" in BrowserOwnedProductTransport.__dict__

    for name in (
        "__init__",
        "send_text_observed",
        "submit",
        "await_final",
        "submission_lifecycle_snapshot",
        "observe_ui_liveness",
        "governance",
    ):
        method = ChatGPTProductRuntime.__dict__.get(name)
        assert callable(method)
        assert method.__module__ == "chatgpt_web_adapter.product_runtime"
