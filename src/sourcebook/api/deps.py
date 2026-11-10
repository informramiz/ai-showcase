"""Dependency wiring. Route handlers ask for services; the container owns their lifetimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import aiosqlite
from fastapi import Depends, Request

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
    container: Container = request.app.state.container
    return container


def get_documents(container: Annotated[Container, Depends(get_container)]) -> DocumentService:
    return container.documents


def get_answers(container: Annotated[Container, Depends(get_container)]) -> AnswerService:
    return container.answers


async def get_question(
    question_id: int, answers: Annotated[AnswerService, Depends(get_answers)]
) -> Question:
    return await answers.get(question_id)


ContainerDep = Annotated[Container, Depends(get_container)]
DocumentsDep = Annotated[DocumentService, Depends(get_documents)]
AnswersDep = Annotated[AnswerService, Depends(get_answers)]
QuestionDep = Annotated[Question, Depends(get_question)]
