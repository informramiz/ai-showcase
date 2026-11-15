"""Shared fixtures: an in-memory database with the real schema and repositories on top."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sourcebook.adapters.sqlite.database import Database, connect
from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.adapters.sqlite.questions import SqliteQuestionRepository
from sourcebook.services.documents import DocumentService

HANDBOOK = """\
# Travel policy

## Meals
The daily meal allowance is 75 USD when travelling. Alcohol is not reimbursed.

## Flights
Book economy class for flights under six hours. Business class needs VP approval.
"""

SECURITY = """\
# Security

## Lost devices
If your laptop is stolen, contact the security desk within one hour.
Report it at security@northwindlabs.example as well.
"""


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    connection = await connect(":memory:")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def document_repo(db: Database) -> SqliteDocumentRepository:
    return SqliteDocumentRepository(db)


@pytest.fixture
def question_repo(db: Database) -> SqliteQuestionRepository:
    return SqliteQuestionRepository(db)


@pytest.fixture
async def documents(document_repo: SqliteDocumentRepository) -> DocumentService:
    service = DocumentService(document_repo, max_bytes=10_000)
    await service.ingest(HANDBOOK)
    await service.ingest(SECURITY)
    return service
