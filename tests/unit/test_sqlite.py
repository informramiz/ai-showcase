import asyncio
from pathlib import Path

from sourcebook.adapters.sqlite.database import MIGRATIONS, Database, connect, migrate
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
        async with db.read() as conn:
            assert await migrate(conn) == len(MIGRATIONS)
            async with conn.execute("PRAGMA user_version") as cur:
                row = await cur.fetchone()
        assert row is not None
        assert row[0] == len(MIGRATIONS)
    finally:
        await db.close()


async def test_foreign_keys_are_enforced(db: Database) -> None:
    async with db.read() as conn, conn.execute("PRAGMA foreign_keys") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_document_ids_are_not_reused_after_delete(
    document_repo: SqliteDocumentRepository,
) -> None:
    first = await document_repo.add("A", "text", [])
    await document_repo.delete(first.id)
    second = await document_repo.add("B", "text", [])
    assert second.id > first.id


async def test_failed_write_does_not_roll_back_a_concurrent_one(
    db: Database, document_repo: SqliteDocumentRepository
) -> None:
    async def failing() -> None:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO documents (title, content, created_at) VALUES ('x', 'x', 'x')"
            )
            await asyncio.sleep(0.01)
            raise RuntimeError("boom")

    async def adding() -> None:
        await document_repo.add("Kept", "text", [])

    results = await asyncio.gather(failing(), adding(), return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    assert [d.title for d in await document_repo.list_all()] == ["Kept"]


async def test_upgrading_a_version_1_database_keeps_data_and_stops_id_reuse(tmp_path: Path) -> None:
    import aiosqlite

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as old:
        await old.executescript(MIGRATIONS[0] + "PRAGMA user_version = 1;")
        await old.execute(
            "INSERT INTO documents VALUES (1, 'Old', 'Old text here.', '2026-01-01T00:00:00+00:00')"
        )
        await old.execute("INSERT INTO chunks VALUES (1, 1, 0, 'H', 'Old text here.')")
        await old.commit()

    db = await connect(path)
    try:
        repo = SqliteDocumentRepository(db)
        kept = await repo.get(1)
        assert kept is not None
        assert kept.chunk_count == 1
        assert [hit.document_id for hit in await repo.search("text", 5)] == [1]
        assert await repo.delete(1)
        assert (await repo.add("New", "text", [])).id == 2
    finally:
        await db.close()
