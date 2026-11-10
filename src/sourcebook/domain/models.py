"""Domain entities and value objects. No framework or provider imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Document:
    id: int
    title: str
    content: str
    created_at: datetime
    chunk_count: int = 0

    @property
    def word_count(self) -> int:
        return len(self.content.split())


@dataclass(frozen=True, slots=True)
class NewChunk:
    ordinal: int
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved passage. `sentences` are the citable units shown to the model."""

    document_id: int
    document_title: str
    chunk_ordinal: int
    heading: str
    sentences: tuple[str, ...]
    score: float

    @property
    def source(self) -> str:
        return f"doc://{self.document_id}#chunk-{self.chunk_ordinal}"

    @property
    def title(self) -> str:
        return f"{self.document_title} - {self.heading}" if self.heading else self.document_title

    @property
    def text(self) -> str:
        return " ".join(self.sentences)


@dataclass(frozen=True, slots=True)
class Citation:
    """A claim in the answer backed by an exact passage from a document."""

    source: str
    title: str
    cited_text: str

    @property
    def document_id(self) -> int | None:
        prefix = "doc://"
        if not self.source.startswith(prefix):
            return None
        head = self.source[len(prefix) :].split("#", 1)[0]
        return int(head) if head.isdigit() else None


class QuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolStep:
    name: str
    input: dict[str, object]
    summary: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    citations: tuple[Citation, ...]
    steps: tuple[ToolStep, ...]
    provider: str
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True, slots=True)
class Question:
    id: int
    text: str
    status: QuestionStatus
    created_at: datetime
    answer: Answer | None = None
    error: str | None = None
