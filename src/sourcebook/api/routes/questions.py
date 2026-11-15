"""Questions: create one, then stream its answer over Server-Sent Events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from sourcebook.api.deps import AnswersDep, QuestionDep
from sourcebook.api.events import to_sse
from sourcebook.api.schemas import ErrorOut, QuestionIn, QuestionOut

router = APIRouter(
    prefix="/api/questions", tags=["questions"], responses={422: {"model": ErrorOut}}
)


@router.post("", status_code=status.HTTP_201_CREATED, responses={422: {"model": ErrorOut}})
async def ask(body: QuestionIn, answers: AnswersDep) -> QuestionOut:
    """Store the question. Open `stream_url` with EventSource to watch it being answered."""
    return QuestionOut.of(await answers.ask(body.question))


@router.get("")
async def recent_questions(
    answers: AnswersDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[QuestionOut]:
    return [QuestionOut.of(q) for q in await answers.recent(limit)]


@router.get("/{question_id}", responses={404: {"model": ErrorOut}})
async def get_question(question: QuestionDep) -> QuestionOut:
    return QuestionOut.of(question)


@router.get(
    "/{question_id}/stream",
    response_class=EventSourceResponse,
    responses={404: {"model": ErrorOut}},
)
async def stream_answer(
    question: QuestionDep, answers: AnswersDep
) -> AsyncIterator[ServerSentEvent]:
    """Events: thinking, token, citation, tool_call, tool_result, then done or error."""
    sequence = 0
    async for event in answers.stream(question.id):
        sequence += 1
        yield to_sse(event, sequence)
