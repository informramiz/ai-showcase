"""Claude adapter for the `LLMProvider` port, built on the official `anthropic` SDK.

Choices, each from the Claude API docs:
- Adaptive thinking with summarized display, so the UI can show what the model is weighing.
- Search results go back to the model as `search_result` blocks inside the tool result, which
  turns on native citations (`search_result_location`) that point at exact sentences.
- `eager_input_streaming` on client tools; parsed inputs are validated by each tool (Pydantic).
- Top-level `cache_control` caches the stable prefix (tools + system prompt) across turns.
- `fallbacks="default"` lets the API retry on a fallback model when the primary is overloaded.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import anthropic
from anthropic import AsyncAnthropic

from sourcebook.domain.errors import ProviderError
from sourcebook.domain.models import Citation, Usage
from sourcebook.ports.llm import (
    AssistantMessage,
    CitationChunk,
    Message,
    StopReason,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnCompleted,
    TurnEvent,
    TurnRequest,
    UserMessage,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class AnthropicProvider:
    def __init__(
        self,
        client: AsyncAnthropic,
        *,
        model: str,
        effort: str = "medium",
        max_tokens: int = 16_000,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return self._model

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=request.system,
                messages=cast("Any", [to_api_message(m) for m in request.messages]),
                tools=cast("Any", [to_api_tool(t) for t in request.tools]),
                thinking={"type": "adaptive", "display": "summarized"},
                output_config=cast("Any", {"effort": self._effort}),
                cache_control={"type": "ephemeral"},
                fallbacks="default",
                betas=[FALLBACK_BETA],
            ) as stream:
                async for event in stream:
                    if event.type == "thinking":
                        yield ThinkingChunk(event.thinking)
                    elif event.type == "text":
                        yield TextChunk(event.text)
                    elif event.type == "content_block_stop":
                        # Citations attach to a finished text block; emitting them here puts
                        # the marker right after the sentence it supports.
                        block = event.content_block
                        if block.type == "text":
                            for citation in block.citations or ():
                                mapped = to_citation(citation)
                                if mapped is not None:
                                    yield CitationChunk(mapped)
                final = await stream.get_final_message()
        except ValueError as exc:  # malformed streamed tool JSON (eager input streaming)
            logger.warning("Model produced malformed tool input: %s", exc)
            raise ProviderError("The model produced malformed tool input. Please retry.") from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderError("The Anthropic API key was rejected.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("This API key cannot use the configured model.") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Model {self._model!r} was not found.") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Rate limited by the Anthropic API. Try again shortly.") from exc
        except anthropic.BadRequestError as exc:
            logger.warning("Anthropic rejected the request: %s", exc.message)
            raise ProviderError("The Anthropic API rejected the request.") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"The Anthropic API returned an error ({exc.status_code})."
            ) from exc
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise ProviderError("Could not reach the Anthropic API.") from exc

        tool_calls = tuple(
            ToolCall(block.id, block.name, block.input)
            for block in final.content
            if block.type == "tool_use"
        )
        text = "".join(block.text for block in final.content if block.type == "text")
        usage = final.usage
        yield TurnCompleted(
            message=AssistantMessage(text, tool_calls, native=list(final.content)),
            stop_reason=_STOP_REASONS.get(final.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
            refusal_category=final.stop_details.category if final.stop_details else None,
        )


def to_api_tool(spec: ToolSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "eager_input_streaming": True,
    }


def to_api_message(message: Message) -> dict[str, object]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.text}
    if isinstance(message, AssistantMessage):
        # Replay the model's own blocks verbatim (thinking signatures included).
        if message.native is not None:
            return {"role": "assistant", "content": message.native}
        blocks: list[dict[str, object]] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in message.tool_calls
        ]
        return {"role": "assistant", "content": blocks}
    return {"role": "user", "content": [to_tool_result(r) for r in message.results]}


def to_tool_result(result: ToolResult) -> dict[str, object]:
    content: list[dict[str, object]]
    if result.hits:
        content = [
            {
                "type": "search_result",
                "source": hit.source,
                "title": hit.title,
                "content": [{"type": "text", "text": s} for s in hit.sentences],
                "citations": {"enabled": True},
            }
            for hit in result.hits
        ]
    else:
        content = [{"type": "text", "text": result.text or "(no output)"}]
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": result.call_id,
        "content": content,
    }
    if result.is_error:
        block["is_error"] = True
    return block


def to_citation(citation: object) -> Citation | None:
    if getattr(citation, "type", None) != "search_result_location":
        return None
    return Citation(
        source=str(getattr(citation, "source", "")),
        title=str(getattr(citation, "title", None) or ""),
        cited_text=str(getattr(citation, "cited_text", "")).strip(),
    )


def build_client(api_key: str) -> AsyncAnthropic:
    return AsyncAnthropic(api_key=api_key, max_retries=2)
