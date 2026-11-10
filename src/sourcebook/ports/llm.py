"""Provider-neutral port for a streaming, tool-using language model.

The answering service owns the agent loop and talks only to this interface.
Adapters (Anthropic, the deterministic fake) translate to and from it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sourcebook.domain.models import Citation, SearchHit, Usage

# ---- conversation ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: object  # untrusted model output; validated by the tool before use


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """One completed model turn.

    `native` holds the provider's own content blocks (thinking signatures and
    all) so the adapter can replay the turn unmodified on the next request.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    native: object = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    hits: tuple[SearchHit, ...] = ()
    text: str = ""
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ToolResultsMessage:
    results: tuple[ToolResult, ...]


Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class TurnRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = field(default=())


# ---- streamed turn events -------------------------------------------------

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass(frozen=True, slots=True)
class ThinkingChunk:
    text: str


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str


@dataclass(frozen=True, slots=True)
class CitationChunk:
    citation: Citation


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    message: AssistantMessage
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    refusal_category: str | None = None


TurnEvent = ThinkingChunk | TextChunk | CitationChunk | TurnCompleted


class LLMProvider(Protocol):
    """A model that streams one turn at a time. The caller runs the tool loop."""

    @property
    def name(self) -> str: ...

    def stream_turn(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        """Stream one assistant turn. The last event is always `TurnCompleted`.

        Raises `ProviderError` for failures the user should see.
        """
        ...
