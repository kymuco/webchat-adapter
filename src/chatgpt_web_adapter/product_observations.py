from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, TypeAlias
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ACTIVITY_STARTED = "activity_started"
ACTIVITY_TEXT_SNAPSHOT = "activity_text_snapshot"
ACTIVITY_TEXT_DELTA = "activity_text_delta"
ACTIVITY_TEXT_REVISION = "activity_text_revision"
ACTIVITY_COMPLETED = "activity_completed"

PRODUCT_SOURCE_OBSERVED = "product_source_observed"
PRODUCT_CITATION_OBSERVED = "product_citation_observed"
PRODUCT_REQUIRED_ACTION_OBSERVED = "product_required_action_observed"

_ACTIVITY_EVENT_TYPES = frozenset(
    {
        ACTIVITY_STARTED,
        ACTIVITY_TEXT_SNAPSHOT,
        ACTIVITY_TEXT_DELTA,
        ACTIVITY_TEXT_REVISION,
        ACTIVITY_COMPLETED,
    }
)

_SEARCH_OPERATIONS = frozenset(
    {
        "search_query",
        "image_query",
        "product_query",
        "businesses_query",
        "availability_query",
        "open",
        "click",
        "find",
        "screenshot",
    }
)
_SEARCH_ACTIVITY_KINDS = frozenset(
    {
        "web",
        "file_search",
        "research",
        "image",
        "product_search",
        "local_search",
        "browsing_display",
    }
)
_TOOL_ACTIVITY_KINDS = frozenset({"tool", "code"})
_PRIVATE_ACTIVITY_CONTENT_TYPES = frozenset({"thoughts"})
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "auth",
        "authorization",
        "api_key",
        "apikey",
        "key",
        "signature",
        "sig",
        "credential",
        "credentials",
        "secret",
        "client_secret",
        "client_assertion",
        "code_verifier",
        "password",
        "passwd",
        "session",
        "session_id",
        "sessionid",
        "code",
    }
)


class ProductObservationKind(str, Enum):
    SEARCH = "SEARCH"
    TOOL = "TOOL"
    SOURCE = "SOURCE"
    CITATION = "CITATION"
    REQUIRED_ACTION = "REQUIRED_ACTION"
    CONNECTOR = "CONNECTOR"
    ACTIVITY = "ACTIVITY"


class ProductObservationPhase(str, Enum):
    STARTED = "STARTED"
    UPDATED = "UPDATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    OBSERVED = "OBSERVED"


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _non_negative_int(value: Any) -> int | None:
    value = _optional_int(value)
    if value is None or value < 0:
        return None
    return value


def _sensitive_query_key(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(".", "_")
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or normalized.startswith("x_amz_")
        or normalized.startswith("x_goog_")
        or normalized.startswith("oauth_")
    )


def _safe_source_url(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        query_items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return None
    if any(_sensitive_query_key(key) for key, _ in query_items):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _citation_range(event: dict[str, Any]) -> tuple[int, int] | None:
    start = _non_negative_int(event.get("start_index"))
    end = _non_negative_int(event.get("end_index"))
    if start is None or end is None or end < start:
        return None
    return start, end


def _activity_observation_kind(
    *,
    activity_kind: str | None,
    operation: str | None,
) -> ProductObservationKind:
    """Prefer explicit normalized operations over coarse activity kinds."""

    if operation is not None:
        if operation in _SEARCH_OPERATIONS:
            return ProductObservationKind.SEARCH
        return ProductObservationKind.TOOL
    if activity_kind in _SEARCH_ACTIVITY_KINDS:
        return ProductObservationKind.SEARCH
    if activity_kind in _TOOL_ACTIVITY_KINDS:
        return ProductObservationKind.TOOL
    return ProductObservationKind.ACTIVITY


def _uncorrelated_tool_event(
    *,
    activity_id: str,
    tool_name: str | None,
) -> bool:
    """Return whether PR8.12 exposes only a standalone operation observation.

    PR8.12 starts an assistant tool request under ``tool-...:<assistant-id>`` and
    reports the corresponding tool result under ``tool-result-...:<tool-id>``.
    It also emits typed operation-only starts under ``typed-...`` without a
    corresponding completion event. None of those identifiers is a proven
    lifecycle correlation key, so PR9.3 represents them as truthful point
    observations until stronger product evidence exists.
    """

    return tool_name is not None or activity_id.startswith(("tool-", "typed-"))


@dataclass(frozen=True)
class ProductActivityObservation:
    observation_id: str
    kind: ProductObservationKind
    phase: ProductObservationPhase
    activity_kind: str | None = None
    operation: str | None = None
    tool_name: str | None = None
    label: str | None = None
    text: str | None = None
    source_content_type: str | None = None
    sequence: int | None = None
    observed_at_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["phase"] = self.phase.value
        return payload


@dataclass(frozen=True)
class ProductSourceObservation:
    observation_id: str
    source_id: str
    url: str
    title: str | None = None
    domain: str | None = None
    attribution: str | None = None
    source_origin: str | None = None
    sequence: int | None = None
    observed_at_ms: int | None = None
    kind: ProductObservationKind = ProductObservationKind.SOURCE
    phase: ProductObservationPhase = ProductObservationPhase.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["phase"] = self.phase.value
        return payload


@dataclass(frozen=True)
class ProductCitationObservation:
    observation_id: str
    citation_id: str
    source_id: str
    citation_index: int | None = None
    start_index: int | None = None
    end_index: int | None = None
    reference_type: str | None = None
    display_text: str | None = None
    sequence: int | None = None
    observed_at_ms: int | None = None
    kind: ProductObservationKind = ProductObservationKind.CITATION
    phase: ProductObservationPhase = ProductObservationPhase.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["phase"] = self.phase.value
        return payload


@dataclass(frozen=True)
class ProductRequiredActionObservation:
    observation_id: str
    action_type: str
    label: str | None = None
    sequence: int | None = None
    observed_at_ms: int | None = None
    kind: ProductObservationKind = ProductObservationKind.REQUIRED_ACTION
    phase: ProductObservationPhase = ProductObservationPhase.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["phase"] = self.phase.value
        return payload


@dataclass(frozen=True)
class StructuredProductObservation:
    observation_id: str
    kind: ProductObservationKind
    phase: ProductObservationPhase
    label: str | None = None
    text: str | None = None
    sequence: int | None = None
    observed_at_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["phase"] = self.phase.value
        return payload


ProductObservation: TypeAlias = (
    ProductActivityObservation
    | ProductSourceObservation
    | ProductCitationObservation
    | ProductRequiredActionObservation
    | StructuredProductObservation
)


class ProductObservationCollector:
    """Convert sanitized browser activity/source events into typed observations."""

    def __init__(self) -> None:
        self._observations: list[ProductObservation] = []
        self._activity_index: dict[str, int] = {}
        self.dropped_event_count = 0

    @property
    def observations(self) -> tuple[ProductObservation, ...]:
        return tuple(self._observations)

    def _record_activity(self, event: dict[str, Any]) -> bool:
        event_type = _optional_text(event.get("type"))
        if event_type not in _ACTIVITY_EVENT_TYPES:
            return False
        activity_id = _optional_text(event.get("activity_id"))
        if activity_id is None:
            self.dropped_event_count += 1
            return True

        content_type = _optional_text(event.get("content_type"))
        if content_type in _PRIVATE_ACTIVITY_CONTENT_TYPES:
            return True

        activity_kind = _optional_text(event.get("activity_kind"))
        operation = _optional_text(event.get("operation"))
        tool_name = _optional_text(event.get("tool_name"))
        kind = _activity_observation_kind(
            activity_kind=activity_kind,
            operation=operation,
        )
        label = _optional_text(event.get("label"))
        text = _optional_text(event.get("text"))
        sequence = _non_negative_int(event.get("sequence"))
        observed_at_ms = _non_negative_int(event.get("observed_at_ms"))

        if _uncorrelated_tool_event(activity_id=activity_id, tool_name=tool_name):
            phase = (
                ProductObservationPhase.STARTED
                if event_type == ACTIVITY_STARTED
                else ProductObservationPhase.OBSERVED
            )
            self._observations.append(
                ProductActivityObservation(
                    observation_id=activity_id,
                    kind=kind,
                    phase=phase,
                    activity_kind=activity_kind,
                    operation=operation,
                    tool_name=tool_name,
                    label=label,
                    text=text,
                    source_content_type=content_type,
                    sequence=sequence,
                    observed_at_ms=observed_at_ms,
                )
            )
            return True

        if event_type == ACTIVITY_STARTED:
            observation = ProductActivityObservation(
                observation_id=activity_id,
                kind=kind,
                phase=ProductObservationPhase.STARTED,
                activity_kind=activity_kind,
                operation=operation,
                tool_name=tool_name,
                label=label,
                text=text,
                source_content_type=content_type,
                sequence=sequence,
                observed_at_ms=observed_at_ms,
            )
            self._activity_index[activity_id] = len(self._observations)
            self._observations.append(observation)
            return True

        index = self._activity_index.get(activity_id)
        if index is None:
            self.dropped_event_count += 1
            return True
        current = self._observations[index]
        if not isinstance(current, ProductActivityObservation):
            self.dropped_event_count += 1
            return True

        phase = current.phase
        if event_type == ACTIVITY_COMPLETED:
            phase = ProductObservationPhase.COMPLETED
        elif event_type in {
            ACTIVITY_TEXT_SNAPSHOT,
            ACTIVITY_TEXT_DELTA,
            ACTIVITY_TEXT_REVISION,
        }:
            phase = ProductObservationPhase.UPDATED

        self._observations[index] = ProductActivityObservation(
            observation_id=current.observation_id,
            kind=kind,
            phase=phase,
            activity_kind=activity_kind or current.activity_kind,
            operation=operation or current.operation,
            tool_name=tool_name or current.tool_name,
            label=label or current.label,
            text=text if text is not None else current.text,
            source_content_type=content_type or current.source_content_type,
            sequence=sequence if sequence is not None else current.sequence,
            observed_at_ms=(
                observed_at_ms
                if observed_at_ms is not None
                else current.observed_at_ms
            ),
        )
        return True

    def _record_source(self, event: dict[str, Any]) -> bool:
        if event.get("type") != PRODUCT_SOURCE_OBSERVED:
            return False
        source_id = _optional_text(event.get("source_id"))
        url = _safe_source_url(event.get("url"))
        if source_id is None or url is None:
            self.dropped_event_count += 1
            return True
        self._observations.append(
            ProductSourceObservation(
                observation_id=f"source:{source_id}",
                source_id=source_id,
                url=url,
                title=_optional_text(event.get("title")),
                domain=_optional_text(event.get("domain")),
                attribution=_optional_text(event.get("attribution")),
                source_origin=_optional_text(event.get("source_origin")),
                sequence=_non_negative_int(event.get("sequence")),
                observed_at_ms=_non_negative_int(event.get("observed_at_ms")),
            )
        )
        return True

    def _record_citation(self, event: dict[str, Any]) -> bool:
        if event.get("type") != PRODUCT_CITATION_OBSERVED:
            return False
        citation_id = _optional_text(event.get("citation_id"))
        source_id = _optional_text(event.get("source_id"))
        if citation_id is None or source_id is None:
            self.dropped_event_count += 1
            return True
        citation_range = _citation_range(event)
        start_index = citation_range[0] if citation_range is not None else None
        end_index = citation_range[1] if citation_range is not None else None
        self._observations.append(
            ProductCitationObservation(
                observation_id=f"citation:{citation_id}",
                citation_id=citation_id,
                source_id=source_id,
                citation_index=_non_negative_int(event.get("citation_index")),
                start_index=start_index,
                end_index=end_index,
                reference_type=_optional_text(event.get("reference_type")),
                display_text=_optional_text(event.get("display_text")),
                sequence=_non_negative_int(event.get("sequence")),
                observed_at_ms=_non_negative_int(event.get("observed_at_ms")),
            )
        )
        return True

    def _record_required_action(self, event: dict[str, Any]) -> bool:
        if event.get("type") != PRODUCT_REQUIRED_ACTION_OBSERVED:
            return False
        action_type = _optional_text(event.get("action_type"))
        if action_type is None:
            self.dropped_event_count += 1
            return True
        action_id = _optional_text(event.get("action_id")) or action_type
        self._observations.append(
            ProductRequiredActionObservation(
                observation_id=f"required-action:{action_id}",
                action_type=action_type,
                label=_optional_text(event.get("label")),
                sequence=_non_negative_int(event.get("sequence")),
                observed_at_ms=_non_negative_int(event.get("observed_at_ms")),
            )
        )
        return True

    def consume(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            self.dropped_event_count += 1
            return
        if self._record_activity(event):
            return
        if self._record_source(event):
            return
        if self._record_citation(event):
            return
        if self._record_required_action(event):
            return
