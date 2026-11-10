"""The Claude adapter against a stubbed SDK client: event mapping, citations, errors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2 as httpx
import pytest

from sourcebook.adapters.llm.claude import (
    FALLBACK_BETA,
    AnthropicProvider,
    build_client,
    to_api_message,
    to_api_tool,
    to_citation,
    to_tool_result,
)
from sourcebook.domain.errors import ProviderError
from sourcebook.domain.models import Citation, SearchHit, Usage
from sourcebook.ports.llm import (
    AssistantMessage,
    CitationChunk,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    TurnCompleted,
    TurnEvent,
    TurnRequest,
    UserMessage,
)

NS = SimpleNamespace
CITATION = NS(
    type="search_result_location",
    source="doc://1#chunk-0",
    title="Travel",
    cited_text=" Meals are 75 USD. ",
)


class FakeStream:
    def __init__(self, events: list[Any], final: Any, error: Exception | None) -> None:
        self._events = events
        self._final = final
        self._error = error

    async def __aenter__(self) -> FakeStream:
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        for event in self._events:
            yield event

    async def get_final_message(self) -> Any:
        return self._final


class FakeMessages:
    def __init__(self, stream: FakeStream) -> None:
        self._stream = stream
        self.kwargs: dict[str, Any] = {}

    def stream(self, **kwargs: Any) -> FakeStream:
        self.kwargs = kwargs
        return self._stream


def make_provider(
    events: list[Any] | None = None,
    final: Any = None,
    error: Exception | None = None,
    *,
    server_fallback: bool = True,
) -> tuple[AnthropicProvider, FakeMessages]:
    messages = FakeMessages(FakeStream(events or [], final, error))
    client = NS(beta=NS(messages=messages))
    provider = AnthropicProvider(
        cast("anthropic.AsyncAnthropic", client),
        model="claude-opus-5-5",
        effort="low",
        server_fallback=server_fallback,
    )
    return provider, messages


def final_message(*content: Any, stop: str | None = "end_turn", category: str | None = None) -> Any:
    return NS(
        content=list(content),
        stop_reason=stop,
        stop_details=NS(category=category) if category else None,
        usage=NS(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=None,
        ),
    )


REQUEST = TurnRequest(
    "system prompt",
    (UserMessage("What is the meal allowance?"),),
    (ToolSpec("search_documents", "Search.", {"type": "object"}),),
)


async def collect(provider: AnthropicProvider) -> list[TurnEvent]:
    return [event async for event in provider.stream_turn(REQUEST)]


async def test_maps_stream_events_and_final_message() -> None:
    text_block = NS(type="text", text="Meals are 75 USD.", citations=[CITATION, NS(type="other")])
    tool_block = NS(type="tool_use", id="tu_1", name="search_documents", input={"query": "x"})
    events = [
        NS(type="thinking", thinking="Weighing it."),
        NS(type="text", text="Meals are 75 USD."),
        NS(type="content_block_stop", content_block=text_block),
        NS(type="content_block_stop", content_block=NS(type="thinking")),
        NS(type="message_delta"),
    ]
    provider, messages = make_provider(
        events, final_message(text_block, tool_block, stop="tool_use")
    )

    out = await collect(provider)

    assert out[:3] == [
        ThinkingChunk("Weighing it."),
        TextChunk("Meals are 75 USD."),
        CitationChunk(Citation("doc://1#chunk-0", "Travel", "Meals are 75 USD.")),
    ]
    done = out[-1]
    assert isinstance(done, TurnCompleted)
    assert done.stop_reason == "tool_use"
    assert done.message.text == "Meals are 75 USD."
    assert done.message.tool_calls == (ToolCall("tu_1", "search_documents", {"query": "x"}),)
    assert done.message.native == [text_block, tool_block]
    assert done.usage == Usage(100, 20, 80, 0)
    assert done.refusal_category is None
    assert provider.name == "claude-opus-5-5"

    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5-5"
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["output_config"] == {"effort": "low"}
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["fallbacks"] == "default"
    assert sent["betas"] == [FALLBACK_BETA]
    assert sent["tools"][0]["eager_input_streaming"] is True
    assert sent["messages"] == [{"role": "user", "content": "What is the meal allowance?"}]


async def test_gateway_mode_omits_the_anthropic_only_fallback_options() -> None:
    provider, messages = make_provider([], final_message(), server_fallback=False)
    await collect(provider)
    assert "fallbacks" not in messages.kwargs
    assert "betas" not in messages.kwargs
    assert messages.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}


async def test_refusal_and_unknown_stop_reasons() -> None:
    provider, _ = make_provider([], final_message(stop="refusal", category="bio"))
    done = (await collect(provider))[-1]
    assert isinstance(done, TurnCompleted)
    assert done.stop_reason == "refusal"
    assert done.refusal_category == "bio"

    provider, _ = make_provider([], final_message(stop="pause_turn"))
    done = (await collect(provider))[-1]
    assert isinstance(done, TurnCompleted)
    assert done.stop_reason == "other"


def _response(code: int) -> httpx.Response:
    return httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.com"))


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    return cls("error", response=_response(code), body=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (_status_error(anthropic.AuthenticationError, 401), "key was rejected"),
        (_status_error(anthropic.PermissionDeniedError, 403), "cannot use the configured model"),
        (_status_error(anthropic.NotFoundError, 404), "was not found"),
        (_status_error(anthropic.RateLimitError, 429), "Rate limited"),
        (_status_error(anthropic.BadRequestError, 400), "rejected the request"),
        (_status_error(anthropic.InternalServerError, 529), r"returned an error \(529\)"),
        (
            anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
            "Could not reach",
        ),
        (ValueError("bad json"), "malformed tool input"),
    ],
)
async def test_sdk_errors_become_provider_errors(error: Exception, message: str) -> None:
    provider, _ = make_provider(error=error)
    with pytest.raises(ProviderError, match=message):
        await collect(provider)


def test_tool_spec_mapping() -> None:
    spec = ToolSpec("calculate", "Math.", {"type": "object"})
    assert to_api_tool(spec) == {
        "name": "calculate",
        "description": "Math.",
        "input_schema": {"type": "object"},
        "eager_input_streaming": True,
    }


def test_assistant_messages_replay_native_blocks_verbatim() -> None:
    native = [NS(type="thinking", signature="sig")]
    message = AssistantMessage("", (), native=native)
    assert to_api_message(message) == {"role": "assistant", "content": native}


def test_assistant_messages_without_native_blocks_are_rebuilt() -> None:
    message = AssistantMessage("Looking.", (ToolCall("t1", "calculate", {"expression": "1"}),))
    assert to_api_message(message) == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "t1", "name": "calculate", "input": {"expression": "1"}},
        ],
    }


def test_tool_results_become_search_result_blocks_with_citations() -> None:
    hit = SearchHit(3, "Security", 2, "Lost devices", ("Call the desk.", "Then email."), 1.5)
    message = ToolResultsMessage(
        (
            ToolResult("t1", hits=(hit,)),
            ToolResult("t2", text="Division by zero.", is_error=True),
            ToolResult("t3"),
        )
    )
    api = to_api_message(message)
    assert api["role"] == "user"
    first, second, third = cast("list[dict[str, Any]]", api["content"])
    assert first == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": [
            {
                "type": "search_result",
                "source": "doc://3#chunk-2",
                "title": "Security - Lost devices",
                "content": [
                    {"type": "text", "text": "Call the desk."},
                    {"type": "text", "text": "Then email."},
                ],
                "citations": {"enabled": True},
            }
        ],
    }
    assert second["is_error"] is True
    assert second["content"] == [{"type": "text", "text": "Division by zero."}]
    assert third["content"] == [{"type": "text", "text": "(no output)"}]
    assert to_tool_result(ToolResult("t3")) == third


def test_only_search_result_citations_are_mapped() -> None:
    assert to_citation(NS(type="char_location", cited_text="x")) is None
    untitled = to_citation(
        NS(type="search_result_location", source="s", title=None, cited_text="q")
    )
    assert untitled == Citation("s", "", "q")


def test_build_client_uses_the_given_key() -> None:
    client = build_client("test-key-not-real")
    assert client.api_key == "test-key-not-real"
    assert client.max_retries == 2


def test_build_client_accepts_a_gateway_base_url() -> None:
    client = build_client("test-key-not-real", "https://openrouter.ai/api")
    assert str(client.base_url).startswith("https://openrouter.ai/api")
