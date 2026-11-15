from collections.abc import AsyncIterator, Sequence

import pytest

from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.adapters.sqlite.questions import SqliteQuestionRepository
from sourcebook.domain.errors import NotFoundError, ProviderError, ValidationError
from sourcebook.domain.events import (
    AnswerCompleted,
    AnswerEvent,
    AnswerFailed,
    CitationAdded,
    ThinkingDelta,
    TokenDelta,
    ToolCalled,
    ToolFinished,
)
from sourcebook.domain.models import Answer, Citation, QuestionStatus, ToolStep, Usage
from sourcebook.ports.llm import (
    AssistantMessage,
    CitationChunk,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolResultsMessage,
    TurnCompleted,
    TurnEvent,
    TurnRequest,
)
from sourcebook.services.answering import AnswerService, replay
from sourcebook.services.documents import DocumentService
from sourcebook.services.tools import CalculateTool, SearchDocumentsTool, ToolRegistry

CITE = Citation("doc://1#chunk-1", "Travel policy - Meals", "The allowance is 75 USD.")


class ScriptedProvider:
    """Plays back one scripted list of events per turn and records each request."""

    def __init__(self, turns: Sequence[Sequence[TurnEvent] | Exception]) -> None:
        self._turns = list(turns)
        self.requests: list[TurnRequest] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        self.requests.append(request)
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        for event in turn:
            yield event


def tool_turn(*calls: ToolCall) -> list[TurnEvent]:
    return [
        ThinkingChunk("Let me search."),
        TurnCompleted(AssistantMessage("", calls), "tool_use", Usage(10, 5, 2, 1)),
    ]


def answer_turn(*events: TurnEvent, stop: str = "end_turn") -> list[TurnEvent]:
    return [*events, TurnCompleted(AssistantMessage("", ()), stop, Usage(20, 8))]  # type: ignore[arg-type]


def service(
    provider: ScriptedProvider,
    document_repo: SqliteDocumentRepository,
    question_repo: SqliteQuestionRepository,
    max_turns: int = 4,
) -> AnswerService:
    tools = ToolRegistry([SearchDocumentsTool(document_repo), CalculateTool()])
    return AnswerService(provider, tools, question_repo, max_turns=max_turns)


async def run(svc: AnswerService, text: str) -> tuple[int, list[AnswerEvent]]:
    question = await svc.ask(text)
    return question.id, [event async for event in svc.stream(question.id)]


async def test_full_loop_streams_tools_tokens_and_citations(
    documents: DocumentService,
    document_repo: SqliteDocumentRepository,
    question_repo: SqliteQuestionRepository,
) -> None:
    provider = ScriptedProvider(
        [
            tool_turn(
                ToolCall("s1", "search_documents", {"query": "meal allowance"}),
                ToolCall("c1", "calculate", {"expression": "75 * 3"}),
            ),
            answer_turn(
                TextChunk("It is 75 USD a day."),
                CitationChunk(CITE),
                TextChunk(" Again."),
                CitationChunk(CITE),
            ),
        ]
    )
    svc = service(provider, document_repo, question_repo)
    qid, events = await run(svc, "  What is the meal allowance?  ")

    kinds = [type(e) for e in events]
    assert kinds == [
        ThinkingDelta,
        ToolCalled,
        ToolFinished,
        ToolCalled,
        ToolFinished,
        TokenDelta,
        CitationAdded,
        TokenDelta,
        CitationAdded,
        AnswerCompleted,
    ]
    done = events[-1]
    assert isinstance(done, AnswerCompleted)
    assert done.answer.text == "It is 75 USD a day.[^1] Again.[^1]"
    assert done.answer.citations == (CITE,)  # deduplicated
    assert [s.name for s in done.answer.steps] == ["search_documents", "calculate"]
    assert done.answer.usage == Usage(30, 13, 2, 1)
    assert done.answer.provider == "scripted"

    # The second request replays the assistant turn and the tool results.
    second = provider.requests[1]
    assert isinstance(second.messages[1], AssistantMessage)
    results = second.messages[2]
    assert isinstance(results, ToolResultsMessage)
    assert results.results[0].hits
    assert results.results[1].text == "75 * 3 = 225"

    stored = await svc.get(qid)
    assert stored.status is QuestionStatus.ANSWERED
    assert stored.answer == done.answer
    assert [q.id for q in await svc.recent()] == [qid]


async def test_text_from_separate_turns_is_split_into_paragraphs(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    first: list[TurnEvent] = [
        TextChunk("Searching first."),
        TurnCompleted(
            AssistantMessage(
                "Searching first.", (ToolCall("s", "calculate", {"expression": "1+1"}),)
            ),
            "tool_use",
        ),
    ]
    provider = ScriptedProvider([first, answer_turn(TextChunk("  Two."))])
    _, events = await run(service(provider, document_repo, question_repo), "q?")
    done = events[-1]
    assert isinstance(done, AnswerCompleted)
    assert done.answer.text == "Searching first.\n\nTwo."


async def test_refusal_fails_the_question(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    refusal = [TurnCompleted(AssistantMessage("", ()), "refusal", refusal_category="cyber")]
    svc = service(ScriptedProvider([refusal]), document_repo, question_repo)
    qid, events = await run(svc, "q?")
    failed = events[-1]
    assert isinstance(failed, AnswerFailed)
    assert "declined" in failed.message
    assert "(cyber)" in failed.message
    stored = await svc.get(qid)
    assert stored.status is QuestionStatus.FAILED

    # Streaming a failed question again replays the stored error.
    again = [e async for e in svc.stream(qid)]
    assert again == [AnswerFailed(qid, stored.error or "")]


async def test_provider_errors_are_shown_to_the_user(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    svc = service(ScriptedProvider([ProviderError("Rate limited.")]), document_repo, question_repo)
    _, events = await run(svc, "q?")
    assert events == [AnswerFailed(1, "Rate limited.")]


async def test_unexpected_errors_are_logged_and_hidden(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    svc = service(ScriptedProvider([RuntimeError("boom")]), document_repo, question_repo)
    _, events = await run(svc, "q?")
    failed = events[-1]
    assert isinstance(failed, AnswerFailed)
    assert "boom" not in failed.message
    assert "Something went wrong" in failed.message


async def test_stream_that_ends_without_completion_fails(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    svc = service(ScriptedProvider([[TextChunk("half")]]), document_repo, question_repo)
    _, events = await run(svc, "q?")
    failed = events[-1]
    assert isinstance(failed, AnswerFailed)
    assert "ended unexpectedly" in failed.message


async def test_running_out_of_turns_fails_instead_of_saving_a_preamble(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    call = ToolCall("s", "search_documents", {"query": "nothing here"})
    narrated: list[TurnEvent] = [
        TextChunk("Let me search."),
        TurnCompleted(AssistantMessage("", (call,)), "tool_use", Usage(10, 5)),
    ]
    provider = ScriptedProvider([narrated, narrated])
    qid, events = await run(service(provider, document_repo, question_repo, max_turns=2), "q?")
    failed = events[-1]
    assert isinstance(failed, AnswerFailed)
    assert "search attempts" in failed.message
    stored = await question_repo.get(qid)
    assert stored is not None
    assert stored.status is QuestionStatus.FAILED


async def test_truncated_answer_is_a_failure(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    provider = ScriptedProvider([answer_turn(TextChunk("Half an ans"), stop="max_tokens")])
    _, events = await run(service(provider, document_repo, question_repo), "q?")
    failed = events[-1]
    assert isinstance(failed, AnswerFailed)
    assert "output tokens" in failed.message


async def test_empty_answer_is_a_failure(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    provider = ScriptedProvider([answer_turn()])
    _, events = await run(service(provider, document_repo, question_repo), "q?")
    assert isinstance(events[-1], AnswerFailed)


async def test_answered_question_is_replayed_without_calling_the_model(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    provider = ScriptedProvider([answer_turn(TextChunk("Yes."), CitationChunk(CITE))])
    svc = service(provider, document_repo, question_repo)
    qid, first = await run(svc, "q?")
    second = [e async for e in svc.stream(qid)]
    assert len(provider.requests) == 1
    assert second[-1] == first[-1]
    assert TokenDelta("Yes.") in second
    assert CitationAdded(1, CITE) in second


async def test_a_question_cannot_be_answered_twice_at_once(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    provider = ScriptedProvider([answer_turn(TextChunk("Yes."))])
    svc = service(provider, document_repo, question_repo)
    question = await svc.ask("q?")
    live = svc.stream(question.id)
    await anext(live)  # starts the run and holds the in-flight slot
    duplicate = [e async for e in svc.stream(question.id)]
    assert duplicate == [AnswerFailed(question.id, "This question is already being answered.")]
    rest = [e async for e in live]
    assert isinstance(rest[-1], AnswerCompleted)


@pytest.mark.parametrize("text", ["   ", "x" * 1001])
async def test_ask_validates_the_question(
    text: str, document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    svc = service(ScriptedProvider([]), document_repo, question_repo)
    with pytest.raises(ValidationError):
        await svc.ask(text)


async def test_get_unknown_question_raises(
    document_repo: SqliteDocumentRepository, question_repo: SqliteQuestionRepository
) -> None:
    svc = service(ScriptedProvider([]), document_repo, question_repo)
    assert svc.provider_name == "scripted"
    with pytest.raises(NotFoundError, match="Question 99 not found"):
        await svc.get(99)


def test_replay_rebuilds_events_and_keeps_unknown_markers_as_text() -> None:
    answer = Answer(
        text="A[^1] b [^7] c",
        citations=(CITE,),
        steps=(ToolStep("calculate", {"expression": "1+1"}, "= 2"),),
        provider="fake",
    )
    events = replay(5, answer)
    assert events == [
        ToolCalled("replay-0", "calculate", {"expression": "1+1"}),
        ToolFinished("replay-0", "calculate", "= 2", (), False),
        TokenDelta("A"),
        CitationAdded(1, CITE),
        TokenDelta(" b "),
        TokenDelta("[^7]"),
        TokenDelta(" c"),
        AnswerCompleted(5, answer),
    ]
