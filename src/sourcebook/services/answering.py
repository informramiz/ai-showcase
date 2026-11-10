"""The agent loop: ask the model, run the tools it calls, stream everything as it happens."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from sourcebook.domain.errors import NotFoundError, ProviderError, RefusalError, ValidationError
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
from sourcebook.domain.models import Answer, Citation, Question, QuestionStatus, ToolStep, Usage
from sourcebook.ports.llm import (
    CitationChunk,
    LLMProvider,
    Message,
    TextChunk,
    ThinkingChunk,
    ToolResult,
    ToolResultsMessage,
    TurnCompleted,
    TurnRequest,
    UserMessage,
)
from sourcebook.ports.repositories import QuestionRepository
from sourcebook.services.tools import ToolRegistry

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1_000
MARKER = re.compile(r"\[\^(\d+)\]")

SYSTEM_PROMPT = """\
You are Sourcebook, an assistant that answers questions using only the user's own documents.

How to work:
- Always call search_documents before answering. If the results do not cover the question, \
search again with different keywords (at most twice more).
- Base every factual statement on the search results and let the citations point at the exact \
passages. Do not add facts from general knowledge.
- When the answer needs arithmetic on figures from the documents (totals, differences, \
percentages), call calculate instead of doing it in your head.
- If the documents do not contain the answer, say so plainly and suggest what document would.

Style: lead with the direct answer in one or two sentences, then any detail as short paragraphs \
or a brief list. Plain, friendly, no headings, no preamble such as "Based on the documents".\
"""


class AnswerService:
    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        questions: QuestionRepository,
        *,
        max_turns: int = 4,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._questions = questions
        self._max_turns = max_turns
        self._in_flight: set[int] = set()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def ask(self, text: str) -> Question:
        question = text.strip()
        if not question:
            raise ValidationError("Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValidationError(f"Question is longer than {MAX_QUESTION_CHARS} characters.")
        return await self._questions.create(question)

    async def get(self, question_id: int) -> Question:
        question = await self._questions.get(question_id)
        if question is None:
            raise NotFoundError("Question", question_id)
        return question

    async def recent(self, limit: int = 20) -> list[Question]:
        return await self._questions.list_recent(limit)

    async def stream(self, question_id: int) -> AsyncIterator[AnswerEvent]:
        """Answer a pending question live, or replay a finished one from storage."""
        question = await self.get(question_id)
        if question.status is QuestionStatus.ANSWERED and question.answer is not None:
            for event in replay(question.id, question.answer):
                yield event
            return
        if question.status is QuestionStatus.FAILED:
            yield AnswerFailed(question.id, question.error or "This question failed.")
            return
        if question_id in self._in_flight:
            yield AnswerFailed(question.id, "This question is already being answered.")
            return

        self._in_flight.add(question_id)
        try:
            async for event in self._run(question):
                yield event
        finally:
            self._in_flight.discard(question_id)

    async def _run(self, question: Question) -> AsyncIterator[AnswerEvent]:
        run = _RunState()
        messages: list[Message] = [UserMessage(question.text)]
        try:
            for _turn in range(self._max_turns):
                request = TurnRequest(SYSTEM_PROMPT, tuple(messages), self._tools.specs)
                completed: TurnCompleted | None = None
                async for chunk in self._provider.stream_turn(request):
                    if isinstance(chunk, ThinkingChunk):
                        yield ThinkingDelta(chunk.text)
                    elif isinstance(chunk, TextChunk):
                        yield TokenDelta(run.add_text(chunk.text))
                    elif isinstance(chunk, CitationChunk):
                        index = run.add_citation(chunk.citation)
                        yield CitationAdded(index, run.citations[index - 1])
                    else:
                        completed = chunk
                if completed is None:
                    raise ProviderError("The model stream ended unexpectedly.")
                run.usage += completed.usage
                run.end_turn()

                if completed.stop_reason == "refusal":
                    raise RefusalError(completed.refusal_category)
                calls = completed.message.tool_calls
                if completed.stop_reason != "tool_use" or not calls:
                    break  # end_turn, or max_tokens on a text-only turn: keep what we have

                messages.append(completed.message)
                results: list[ToolResult] = []
                for call in calls:
                    outcome = await self._tools.run(call)
                    yield ToolCalled(call.id, call.name, outcome.input)
                    yield ToolFinished(
                        call.id,
                        call.name,
                        outcome.summary,
                        outcome.result.hits,
                        outcome.result.is_error,
                    )
                    run.steps.append(
                        ToolStep(call.name, outcome.input, outcome.summary, outcome.result.is_error)
                    )
                    results.append(outcome.result)
                messages.append(ToolResultsMessage(tuple(results)))
            else:
                if not run.text.strip():
                    run.add_text("I ran out of search attempts before finding a confident answer.")

            answer = Answer(
                text=run.text.strip(),
                citations=tuple(run.citations),
                steps=tuple(run.steps),
                provider=self._provider.name,
                usage=run.usage,
            )
            await self._questions.save_answer(question.id, answer)
            yield AnswerCompleted(question.id, answer)
        except ProviderError as exc:
            await self._questions.save_failure(question.id, str(exc))
            yield AnswerFailed(question.id, str(exc))
        except Exception:
            logger.exception("Answering question %s failed", question.id)
            message = "Something went wrong while answering. Please try again."
            await self._questions.save_failure(question.id, message)
            yield AnswerFailed(question.id, message)


class _RunState:
    """Accumulates the answer text (with [^n] citation markers), citations and tool steps."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.citations: list[Citation] = []
        self.steps: list[ToolStep] = []
        self.usage = Usage()
        self._turn_has_text = False

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def add_text(self, text: str) -> str:
        if not self._turn_has_text and self.parts:
            text = "\n\n" + text.lstrip()
        self._turn_has_text = True
        self.parts.append(text)
        return text

    def add_citation(self, citation: Citation) -> int:
        """Register a citation (deduplicated) and place its marker in the text."""
        key = (citation.source, citation.cited_text)
        index = next(
            (i for i, c in enumerate(self.citations, 1) if (c.source, c.cited_text) == key), 0
        )
        if not index:
            self.citations.append(citation)
            index = len(self.citations)
        self.parts.append(f"[^{index}]")
        return index

    def end_turn(self) -> None:
        self._turn_has_text = False


def replay(question_id: int, answer: Answer) -> list[AnswerEvent]:
    """Rebuild the event sequence of a stored answer so the UI renders it the same way."""
    events: list[AnswerEvent] = []
    for i, step in enumerate(answer.steps):
        call_id = f"replay-{i}"
        events.append(ToolCalled(call_id, step.name, step.input))
        events.append(ToolFinished(call_id, step.name, step.summary, (), step.is_error))
    position = 0
    for match in MARKER.finditer(answer.text):
        if match.start() > position:
            events.append(TokenDelta(answer.text[position : match.start()]))
        index = int(match.group(1))
        if 1 <= index <= len(answer.citations):
            events.append(CitationAdded(index, answer.citations[index - 1]))
        else:
            events.append(TokenDelta(match.group(0)))
        position = match.end()
    if position < len(answer.text):
        events.append(TokenDelta(answer.text[position:]))
    events.append(AnswerCompleted(question_id, answer))
    return events
