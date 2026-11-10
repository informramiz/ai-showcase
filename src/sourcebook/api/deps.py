"""Dependency wiring. Route handlers ask for services; the container owns their lifetimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

import aiosqlite
from fastapi import Depends, HTTPException, Request, status

from sourcebook.config import Settings
from sourcebook.domain.models import Question
from sourcebook.ports.repositories import DocumentRepository
from sourcebook.services.answering import AnswerService
from sourcebook.services.documents import DocumentService


@dataclass(slots=True)
class Container:
    settings: Settings
    db: aiosqlite.Connection
    document_repo: DocumentRepository
    documents: DocumentService
    answers: AnswerService


def get_container(request: Request) -> Container:
    container: Container = request.state.container  # set by the lifespan
    return container


def get_documents(container: Annotated[Container, Depends(get_container)]) -> DocumentService:
    return container.documents


def get_answers(container: Annotated[Container, Depends(get_container)]) -> AnswerService:
    return container.answers


def require_same_origin_htmx(request: Request) -> None:
    """CSRF guard for the cookie-less but browser-facing UI routes.

    htmx sends ``HX-Request: true`` on every request it makes. A cross-site form post
    cannot set that header, and a cross-site fetch that does triggers a CORS preflight
    this app never approves. When the browser sends ``Origin`` it must also match ``Host``.
    """
    if request.headers.get("hx-request") != "true":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing HX-Request header.")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request refused.")


async def get_question(
    question_id: int, answers: Annotated[AnswerService, Depends(get_answers)]
) -> Question:
    return await answers.get(question_id)


ContainerDep = Annotated[Container, Depends(get_container)]
DocumentsDep = Annotated[DocumentService, Depends(get_documents)]
AnswersDep = Annotated[AnswerService, Depends(get_answers)]
QuestionDep = Annotated[Question, Depends(get_question)]
