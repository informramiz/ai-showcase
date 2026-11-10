"""Server-rendered UI: the page shell plus htmx partials for the document library."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from sourcebook.api.deps import AnswersDep, DocumentsDep
from sourcebook.api.schemas import QuestionOut
from sourcebook.domain.errors import SourcebookError
from sourcebook.domain.models import Document

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "web" / "templates")
EXAMPLES = (
    "How many days a week can I work remotely?",
    "What is the daily meal limit when travelling, and what would 3 days cost?",
    "Who do I contact if my laptop is stolen?",
    "How does on-call compensation work?",
)

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, documents: DocumentsDep, answers: AnswersDep) -> HTMLResponse:
    history = [QuestionOut.of(q).model_dump(mode="json") for q in await answers.recent(12)]
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "documents": await documents.list_all(),
            "history": history,
            "provider": answers.provider_name,
            "examples": EXAMPLES,
        },
    )


@router.get("/ui/documents", response_class=HTMLResponse)
async def library(request: Request, documents: DocumentsDep) -> HTMLResponse:
    return _library(request, await documents.list_all())


@router.post("/ui/documents", response_class=HTMLResponse)
async def add_document(
    request: Request,
    documents: DocumentsDep,
    file: UploadFile | None = None,
    content: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
) -> HTMLResponse:
    error: str | None = None
    added: int | None = None
    try:
        if file is not None and file.filename:
            doc = await documents.ingest_upload(file.filename, await file.read(), title or None)
        else:
            doc = await documents.ingest(content, title or None)
        added = doc.id
    except SourcebookError as exc:
        error = str(exc)
    return _library(request, await documents.list_all(), error=error, added=added)


@router.delete("/ui/documents/{document_id}", response_class=HTMLResponse)
async def remove_document(
    request: Request, document_id: int, documents: DocumentsDep
) -> HTMLResponse:
    error: str | None = None
    try:
        await documents.delete(document_id)
    except SourcebookError as exc:
        error = str(exc)
    return _library(request, await documents.list_all(), error=error)


def _library(
    request: Request,
    docs: Sequence[Document],
    *,
    error: str | None = None,
    added: int | None = None,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "partials/library.html",
        {"documents": docs, "error": error, "added": added},
    )
