from pathlib import Path

import aiosqlite

from sourcebook.adapters.sqlite.database import MIGRATIONS, connect, migrate
from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository, fts_query
from sourcebook.adapters.sqlite.questions import SqliteQuestionRepository
from sourcebook.domain.models import Answer, Citation, NewChunk, QuestionStatus, ToolStep, Usage
from sourcebook.services.documents import DocumentService


def test_fts_query_quotes_terms_and_drops_noise() -> None:
    assert fts_query("What is the meal-allowance? 'Meal'") == '"meal" OR "allowance"'
    assert fts_query('the " OR NEAR(') == '"near"'
    assert fts_query("the a of") is None


async def test_add_get_list_and_count(document_repo: SqliteDocumentRepository) -> None:
    doc = await document_repo.add("T", "Alpha beta.", [NewChunk(0, "", "Alpha beta.")])
    again = await document_repo.get(doc.id)
    assert again is not None
    assert (again.title, again.content, again.chunk_count) == ("T", "Alpha beta.", 1)
    assert again.created_at == doc.created_at
    assert [d.id for d in await document_repo.list_all()] == [doc.id]
    assert await document_repo.count() == 1
    assert await document_repo.get(999) is None


async def test_search_ranks_by_relevance_and_splits_sentences(
    documents: DocumentService, document_repo: SqliteDocumentRepository
) -> None:
    hits = await document_repo.search("laptop stolen", 3)
    assert hits[0].document_title == "Security"
    assert hits[0].heading == "Lost devices"
    assert hits[0].sentences[0].startswith("If your laptop is stolen")
    assert hits[0].score > 0
    assert await document_repo.search("the of", 3) == []
    # Porter stemming: "travelling" matches "travel".
    assert (await document_repo.search("travel", 3))[0].document_title == "Travel policy"


async def test_delete_cascades_to_the_search_index(
    documents: DocumentService, document_repo: SqliteDocumentRepository
) -> None:
    (security,) = [d for d in await document_repo.list_all() if d.title == "Security"]
    assert await document_repo.delete(security.id)
    assert not await document_repo.delete(security.id)
    assert await document_repo.search("laptop", 3) == []


async def test_questions_roundtrip_answers_and_failures(
    question_repo: SqliteQuestionRepository,
) -> None:
    first = await question_repo.create("First?")
    second = await question_repo.create("Second?")
    assert first.status is QuestionStatus.PENDING

    answer = Answer(
        text="Yes[^1]",
        citations=(Citation("doc://1#chunk-0", "T", "Quote."),),
        steps=(ToolStep("search_documents", {"query": "q", "top_k": 4}, "1 passage from T"),),
        provider="fake",
        usage=Usage(1, 2, 3, 4),
    )
    await question_repo.save_answer(first.id, answer)
    await question_repo.save_failure(second.id, "Nope.")

    stored = await question_repo.get(first.id)
    assert stored is not None
    assert stored.status is QuestionStatus.ANSWERED
    assert stored.answer == answer
    failed = await question_repo.get(second.id)
    assert failed is not None
    assert (failed.status, failed.error, failed.answer) == (QuestionStatus.FAILED, "Nope.", None)
    assert [q.id for q in await question_repo.list_recent(10)] == [second.id, first.id]
    assert len(await question_repo.list_recent(1)) == 1
    assert await question_repo.get(42) is None


async def test_file_database_is_created_and_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "app.db"
    db = await connect(path)
    try:
        assert path.exists()
        assert await migrate(db) == len(MIGRATIONS)
        async with db.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == len(MIGRATIONS)
    finally:
        await db.close()


async def test_foreign_keys_are_enforced(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1
