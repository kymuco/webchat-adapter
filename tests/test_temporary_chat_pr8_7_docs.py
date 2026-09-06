from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "temporary_chat_pr8_7.md"
LIVE_DOC = ROOT / "docs" / "temporary_chat_pr8_7_live_characterization.md"
REVIEW_DOC = ROOT / "docs" / "temporary_chat_pr8_7_capability_graduation_review.md"
CLOSURE_DOC = ROOT / "docs" / "temporary_chat_pr8_13_closure.md"
TRANSPORT = (
    ROOT
    / "src"
    / "chatgpt_web_adapter"
    / "browser_owned_product_transport_core.py"
)


def test_pr87_docs_keep_temporary_chat_evidence_first_and_fail_closed() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "production Temporary Chat is **not enabled by this document**" in text
    assert 'production conversation_mode="temporary" = NOT ENABLED' in text
    assert "temporary_product_conversation_id" in text
    assert "temporary_live_write_authority" in text
    assert "No hidden fallback is allowed." in text
    assert "TEMP -> NORMAL" in text
    assert "NORMAL -> TEMP" in text
    assert "Browser Authority Lease" in text
    assert "Temporary Lifecycle" in text


def test_pr87_live_evidence_preserves_corrected_temporary_ground_truth() -> None:
    text = LIVE_DOC.read_text(encoding="utf-8")

    assert "Earlier automated activation result is an ordinary durable control" in text
    assert "https://chatgpt.com/?temporary-chat=true" in text
    assert "T2 true Temporary page-owned visible text turn = PASS" in text
    assert "T7b post-close product-route recovery = STABLE_RECOVERED / PASS" in text
    assert "post-close controlled continuation = REJECTED / HTTP 404" in text
    assert "normal multi-turn conversation semantics = PROVEN" in text
    assert "temporary_chat = UNKNOWN" in text
    assert "production conversation_mode=\"temporary\" = NOT ENABLED" in text


def test_pr87_review_remains_historical_while_pr813_closure_graduates_transport() -> None:
    review = REVIEW_DOC.read_text(encoding="utf-8")
    closure = CLOSURE_DOC.read_text(encoding="utf-8")
    transport = TRANSPORT.read_text(encoding="utf-8")

    # PR8.7 remains an immutable record of the decision made before the
    # production Temporary route existed.
    assert "temporary_chat = UNIMPLEMENTED" in review
    assert 'production conversation_mode="temporary" = DISABLED' in review
    assert "UNKNOWN -> UNIMPLEMENTED" in review
    assert "AVAILABLE graduation          = DENIED" in review

    # PR8.13 is the later production graduation record.
    assert "CLOSED / PASS" in closure
    assert "temporary_chat = AVAILABLE" in closure
    assert "1222 passed in 23.19s" in closure
    assert "post-end continuation blocked before write" in closure

    assert "TEMPORARY_CHAT: CapabilityState.AVAILABLE" in transport
    assert "PR8.13 production live gate" in transport
    assert '"temporary_chat_capability_live_graduated": True' in transport
    assert '"temporary_chat_durable_fallback": False' in transport
