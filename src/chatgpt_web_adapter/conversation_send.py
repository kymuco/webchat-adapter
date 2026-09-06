from __future__ import annotations

from typing import Any, Callable, Sequence

from .model_registry import DEFAULT_MODEL
from .types import (
    AttachedConversation,
    ChatConversation,
    ChatResponse,
    ConversationRef,
    MediaItem,
)


def _resolve_send_model(
    *,
    attached: AttachedConversation,
    preserve_model: bool,
    model: str | None,
) -> str:
    if model is not None:
        return model
    if preserve_model and attached.detected_model:
        return attached.detected_model
    return DEFAULT_MODEL


def _resolve_reasoning_effort(
    *,
    attached: AttachedConversation,
    preserve_model: bool,
    model: str | None,
    reasoning_effort: str | None,
) -> str | None:
    if reasoning_effort is not None:
        return reasoning_effort
    if model is not None:
        return None
    if preserve_model and attached.detected_model and attached.detected_reasoning_effort:
        return attached.detected_reasoning_effort
    return None


def send_to_conversation(
    self: Any,
    url_or_id: ConversationRef | ChatConversation | dict[str, Any] | str,
    prompt: str,
    *,
    preserve_model: bool = True,
    model: str | None = None,
    system: str | None = None,
    web_search: bool = False,
    temporary: bool = False,
    reasoning_effort: str | None = None,
    media: Sequence[MediaItem] | None = None,
    on_token: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> ChatResponse:
    """Send a prompt to an existing chatgpt.com conversation by URL or id.

    Ordinary text turns use the live-observed prepare/conduit path. Multimodal
    turns deliberately remain on the legacy ``send()`` path until independently
    characterized.
    """

    attached = self.attach_conversation(url_or_id)
    resolved_model = _resolve_send_model(
        attached=attached,
        preserve_model=preserve_model,
        model=model,
    )
    resolved_reasoning_effort = _resolve_reasoning_effort(
        attached=attached,
        preserve_model=preserve_model,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    if media:
        return self.send(
            prompt,
            model=resolved_model,
            system=system,
            web_search=web_search,
            temporary=temporary,
            reasoning_effort=resolved_reasoning_effort,
            conversation=attached.conversation,
            media=media,
            on_token=on_token,
            on_event=on_event,
        )

    response = self._send_existing_text_prepared(
        prompt,
        model=resolved_model,
        system=system,
        web_search=web_search,
        temporary=temporary,
        reasoning_effort=resolved_reasoning_effort,
        conversation=attached.conversation,
        media=None,
        on_token=on_token,
        on_event=on_event,
    )
    if response.title is None and attached.title is not None:
        response.title = attached.title
    return response
