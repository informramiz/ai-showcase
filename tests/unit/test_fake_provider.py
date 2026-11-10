from sourcebook.adapters.llm.fake import FakeProvider, _expression, _follow_up_expression
from sourcebook.domain.models import SearchHit
from sourcebook.ports.llm import (
    CitationChunk,
    TextChunk,
    ThinkingChunk,
    ToolResult,
    ToolResultsMessage,
    TurnCompleted,
    TurnEvent,
    TurnRequest,
    UserMessage,
)

HIT = SearchHit(
    1,
    "Travel policy",
    1,
    "Meals",
    ("The daily meal allowance is 75 USD when travelling.", "Alcohol is not reimbursed."),
    3.2,
)
OTHER = SearchHit(2, "Remote work", 0, "", ("Remote work is allowed three days a week.",), 1.0)


async def collect(request: TurnRequest) -> list[TurnEvent]:
    return [event async for event in FakeProvider().stream_turn(request)]


async def test_first_turn_plans_a_search() -> None:
    events = await collect(TurnRequest("sys", (UserMessage("What is the meal allowance?"),)))
    assert isinstance(events[0], ThinkingChunk)
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.stop_reason == "tool_use"
    (call,) = done.message.tool_calls
    assert call.name == "search_documents"
    assert call.input == {"query": "meal allowance", "top_k": 4}


async def test_arithmetic_in_the_question_adds_a_calculate_call() -> None:
    events = await collect(TurnRequest("sys", (UserMessage("Meals cost 75 x 3 days?"),)))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert [c.name for c in done.message.tool_calls] == ["search_documents", "calculate"]
    assert done.message.tool_calls[1].input == {"expression": "75 * 3"}


def test_expression_extraction() -> None:
    assert _expression("what is 1,200 × 2") == "1200 * 2"  # noqa: RUF001
    assert _expression("no numbers here") is None


async def test_answer_turn_cites_the_relevant_sentences_only() -> None:
    results = ToolResultsMessage(
        (ToolResult("s", hits=(HIT, OTHER)), ToolResult("c", text="75 * 3 = 225"))
    )
    events = await collect(
        TurnRequest("sys", (UserMessage("What is the daily meal allowance?"), results))
    )
    citations = [e.citation for e in events if isinstance(e, CitationChunk)]
    assert [c.cited_text for c in citations] == [HIT.sentences[0]]
    assert citations[0].source == "doc://1#chunk-1"
    assert citations[0].title == "Travel policy - Meals"

    text = "".join(e.text for e in events if isinstance(e, TextChunk))
    assert text.startswith("Here's what your documents say.")
    assert "Worked out exactly: 75 * 3 = 225." in text
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.stop_reason == "end_turn"
    assert done.message.text == text


async def test_answer_turn_without_matches_says_it_found_nothing() -> None:
    results = ToolResultsMessage((ToolResult("s", text="No passages matched."),))
    events = await collect(TurnRequest("sys", (UserMessage("submarines?"), results)))
    text = "".join(e.text for e in events if isinstance(e, TextChunk))
    assert text.startswith("I couldn't find anything")
    assert not any(isinstance(e, CitationChunk) for e in events)


async def test_token_delay_is_honoured() -> None:
    provider = FakeProvider(token_delay=0.0001)
    events = [e async for e in provider.stream_turn(TurnRequest("s", (UserMessage("hi"),)))]
    assert provider.name == "fake"
    assert isinstance(events[-1], TurnCompleted)


async def test_rate_times_quantity_triggers_a_second_tool_round() -> None:
    hit = SearchHit(1, "T", 0, "Meals", ("The allowance is $1,075 per day.",), 1.0)
    assert _follow_up_expression("cost for 3 days?", [(hit, hit.sentences[0])]) == "1075 * 3"
    assert _follow_up_expression("cost per day?", [(hit, hit.sentences[0])]) is None
    assert _follow_up_expression("cost for 3 days?", []) is None
    plain = SearchHit(1, "T", 0, "Meals", ("No amount here.",), 1.0)
    assert _follow_up_expression("for 3 days", [(plain, plain.sentences[0])]) is None
