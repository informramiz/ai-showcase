"""Events the answering service streams to clients while it works."""

from __future__ import annotations

from dataclasses import dataclass

from sourcebook.domain.models import Answer, Citation, SearchHit


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True, slots=True)
class TokenDelta:
    text: str


@dataclass(frozen=True, slots=True)
class CitationAdded:
    index: int
    citation: Citation


@dataclass(frozen=True, slots=True)
class ToolCalled:
    call_id: str
    name: str
    input: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolFinished:
    call_id: str
    name: str
    summary: str
    hits: tuple[SearchHit, ...] = ()
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class AnswerCompleted:
    question_id: int
    answer: Answer


@dataclass(frozen=True, slots=True)
class AnswerFailed:
    question_id: int
    message: str


AnswerEvent = (
    ThinkingDelta
    | TokenDelta
    | CitationAdded
    | ToolCalled
    | ToolFinished
    | AnswerCompleted
    | AnswerFailed
)
