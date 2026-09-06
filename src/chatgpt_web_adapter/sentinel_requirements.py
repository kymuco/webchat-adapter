from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import legacy_client_core as client_mod
from .auth import CHAT_URL
from .web_session import suppress_web_session_debug_trace

SENTINEL_PREPARE_PATH = "/backend-api/sentinel/chat-requirements/prepare"
SENTINEL_FINALIZE_PATH = "/backend-api/sentinel/chat-requirements/finalize"

OBSERVED_PREPARE_REQUEST_KEYS = ("p",)
OBSERVED_PREPARE_RESPONSE_KEYS = (
    "persona",
    "prepare_token",
    "proofofwork",
    "so",
    "turnstile",
)
OBSERVED_TURNSTILE_KEYS = ("dx", "required")
OBSERVED_PROOFOFWORK_KEYS = ("difficulty", "required", "seed")
OBSERVED_SO_KEYS = ("collector_dx", "required", "snapshot_dx")
OBSERVED_FINALIZE_REQUEST_KEYS = (
    "prepare_token",
    "proofofwork",
    "turnstile",
)
OBSERVED_FINALIZE_RESPONSE_KEYS = (
    "persona",
    "token",
    "expire_after",
    "expire_at",
)


def _contains_observed_keys(
    actual: tuple[str, ...],
    expected: tuple[str, ...],
) -> bool:
    return set(expected).issubset(actual)


@dataclass(frozen=True)
class SentinelPrepareProbeResult:
    """Structural result of the current two-phase Sentinel prepare request.

    Credential and challenge values are deliberately not retained. This type is
    evidence-only; it does not solve, replay, or finalize browser challenges.
    """

    status_code: int
    status_ok: bool
    persona_present: bool
    prepare_token_present: bool
    response_keys: tuple[str, ...]
    turnstile_present: bool
    turnstile_required: bool
    turnstile_keys: tuple[str, ...]
    proofofwork_present: bool
    proofofwork_required: bool
    proofofwork_keys: tuple[str, ...]
    so_present: bool
    so_required: bool
    so_keys: tuple[str, ...]

    @property
    def observed_shape_matches(self) -> bool:
        """Whether all live-observed structural keys are still present.

        Extra keys are allowed so additive server changes do not cause a false
        rejection. Challenge ``required`` values are deliberately not frozen:
        their booleans may vary by current server policy/session risk.
        """

        return (
            self.status_ok
            and self.persona_present
            and self.prepare_token_present
            and _contains_observed_keys(
                self.response_keys,
                OBSERVED_PREPARE_RESPONSE_KEYS,
            )
            and self.turnstile_present
            and _contains_observed_keys(
                self.turnstile_keys,
                OBSERVED_TURNSTILE_KEYS,
            )
            and self.proofofwork_present
            and _contains_observed_keys(
                self.proofofwork_keys,
                OBSERVED_PROOFOFWORK_KEYS,
            )
            and self.so_present
            and _contains_observed_keys(
                self.so_keys,
                OBSERVED_SO_KEYS,
            )
        )

    @property
    def verdict(self) -> str:
        if self.observed_shape_matches:
            return "TWO_PHASE_SENTINEL_PREPARE_OBSERVED"
        if self.status_ok:
            return "SENTINEL_PREPARE_PARTIAL_SHAPE"
        return "SENTINEL_PREPARE_REJECTED"


def _mapping_keys(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(str(key) for key in value))


def _required(value: Any) -> bool:
    return bool(value.get("required")) if isinstance(value, dict) else False


def _build_prepare_probe_result(status: int, data: Any) -> SentinelPrepareProbeResult:
    response = data if isinstance(data, dict) else {}
    prepare_token = response.get("prepare_token")
    turnstile = response.get("turnstile")
    proofofwork = response.get("proofofwork")
    so = response.get("so")
    return SentinelPrepareProbeResult(
        status_code=int(status),
        status_ok=200 <= int(status) < 300 and isinstance(data, dict),
        persona_present=isinstance(response.get("persona"), str)
        and bool(response.get("persona")),
        prepare_token_present=isinstance(prepare_token, str)
        and bool(prepare_token.strip()),
        response_keys=_mapping_keys(response),
        turnstile_present=isinstance(turnstile, dict),
        turnstile_required=_required(turnstile),
        turnstile_keys=_mapping_keys(turnstile),
        proofofwork_present=isinstance(proofofwork, dict),
        proofofwork_required=_required(proofofwork),
        proofofwork_keys=_mapping_keys(proofofwork),
        so_present=isinstance(so, dict),
        so_required=_required(so),
        so_keys=_mapping_keys(so),
    )


def build_sentinel_prepare_headers(client: Any) -> dict[str, str]:
    return client._build_headers(
        {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": CHAT_URL.rstrip("/"),
            "referer": CHAT_URL,
            "x-openai-target-path": SENTINEL_PREPARE_PATH,
            "x-openai-target-route": SENTINEL_PREPARE_PATH,
        }
    )


def probe_sentinel_requirements_prepare(client: Any) -> SentinelPrepareProbeResult:
    """Probe only the current Sentinel prepare phase and retain structure only.

    The browser-observed finalize phase requires turn-scoped challenge evidence.
    This helper intentionally stops before finalize and before any conversation
    write; it never synthesizes or replays Turnstile/Sentinel challenge values.
    """

    req_input = None
    proof_token = getattr(getattr(client, "auth", None), "proof_token", None)
    if isinstance(proof_token, list):
        try:
            req_input = client_mod._get_requirements_token(proof_token)
        except Exception:
            req_input = None

    headers = build_sentinel_prepare_headers(client)
    payload = {"p": req_input}

    trace_dir_marker = object()
    trace_dir = getattr(client, "debug_trace_dir", trace_dir_marker)
    trace_enabled = trace_dir is not trace_dir_marker and trace_dir is not None

    with suppress_web_session_debug_trace():
        status, data = client._json_request(
            "POST",
            f"{CHAT_URL.rstrip('/')}{SENTINEL_PREPARE_PATH}",
            payload,
            headers,
        )

    result = _build_prepare_probe_result(int(status), data)

    if trace_enabled:
        writer = getattr(client, "_write_debug_trace", None)
        if callable(writer):
            writer(
                "sentinel-prepare",
                {
                    "method": "POST",
                    "url": f"{CHAT_URL.rstrip('/')}{SENTINEL_PREPARE_PATH}",
                    "request_keys": list(OBSERVED_PREPARE_REQUEST_KEYS),
                    "p_present": req_input is not None,
                    "response_status": result.status_code,
                    "response_keys": list(result.response_keys),
                    "prepare_token_present": result.prepare_token_present,
                    "turnstile_present": result.turnstile_present,
                    "turnstile_required": result.turnstile_required,
                    "turnstile_keys": list(result.turnstile_keys),
                    "proofofwork_present": result.proofofwork_present,
                    "proofofwork_required": result.proofofwork_required,
                    "proofofwork_keys": list(result.proofofwork_keys),
                    "so_present": result.so_present,
                    "so_required": result.so_required,
                    "so_keys": list(result.so_keys),
                    "observed_shape_matches": result.observed_shape_matches,
                    "raw_request_recorded": False,
                    "raw_response_recorded": False,
                    "challenge_values_recorded": False,
                },
            )
    return result
