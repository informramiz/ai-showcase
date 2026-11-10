"""HTTP request and response models (Pydantic v2). The domain stays framework-free."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from sourcebook.domain.models import Answer, Citation, Document, Question, SearchHit, ToolStep


class DocumentIn(BaseModel):
    content: str = Field(min_length=1, description="Markdown or plain text.")
    title: str | None = Field(default=None, max_length=120)


class DocumentSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    chunk_count: int
    word_count: int

    @classmethod
    def of(cls, document: Document) -> DocumentSummary:
        return cls(
            id=document.id,
            title=document.title,
            created_at=document.created_at,
            chunk_count=document.chunk_count,
            word_count=document.word_count,
        )


class DocumentOut(DocumentSummary):
    content: str

    @classmethod
    def of(cls, document: Document) -> DocumentOut:
        return cls(**DocumentSummary.of(document).model_dump(), content=document.content)


class QuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=1_000)


class CitationOut(BaseModel):
    index: int
    source: str
    title: str
    cited_text: str
    document_id: int | None

    @classmethod
    def of(cls, index: int, citation: Citation) -> CitationOut:
        return cls(
            index=index,
            source=citation.source,
            title=citation.title,
            cited_text=citation.cited_text,
            document_id=citation.document_id,
        )


class StepOut(BaseModel):
    name: str
    input: dict[str, object]
    summary: str
    is_error: bool

    @classmethod
    def of(cls, step: ToolStep) -> StepOut:
        return cls(name=step.name, input=step.input, summary=step.summary, is_error=step.is_error)


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


class AnswerOut(BaseModel):
    text: str
    provider: str
    citations: list[CitationOut]
    steps: list[StepOut]
    usage: UsageOut

    @classmethod
    def of(cls, answer: Answer) -> AnswerOut:
        return cls(
            text=answer.text,
            provider=answer.provider,
            citations=[CitationOut.of(i, c) for i, c in enumerate(answer.citations, 1)],
            steps=[StepOut.of(s) for s in answer.steps],
            usage=UsageOut(
                input_tokens=answer.usage.input_tokens,
                output_tokens=answer.usage.output_tokens,
                cache_read_input_tokens=answer.usage.cache_read_input_tokens,
                cache_creation_input_tokens=answer.usage.cache_creation_input_tokens,
            ),
        )


class QuestionOut(BaseModel):
    id: int
    question: str
    status: str
    created_at: datetime
    stream_url: str
    answer: AnswerOut | None = None
    error: str | None = None

    @classmethod
    def of(cls, question: Question) -> QuestionOut:
        return cls(
            id=question.id,
            question=question.text,
            status=question.status.value,
            created_at=question.created_at,
            stream_url=f"/api/questions/{question.id}/stream",
            answer=AnswerOut.of(question.answer) if question.answer else None,
            error=question.error,
        )


class HitOut(BaseModel):
    source: str
    title: str
    document_id: int
    score: float
    snippet: str

    @classmethod
    def of(cls, hit: SearchHit) -> HitOut:
        snippet = hit.text if len(hit.text) <= 220 else hit.text[:217].rstrip() + "..."
        return cls(
            source=hit.source,
            title=hit.title,
            document_id=hit.document_id,
            score=hit.score,
            snippet=snippet,
        )


class HealthOut(BaseModel):
    status: str
    provider: str
    documents: int


class ErrorOut(BaseModel):
    detail: str
