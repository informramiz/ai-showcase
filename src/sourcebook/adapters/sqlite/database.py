"""SQLite connection and schema migrations, tracked with PRAGMA user_version."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

MIGRATIONS: tuple[str, ...] = (
    # 1: documents, heading-scoped chunks with an FTS5 (BM25) index, questions
    """
    CREATE TABLE documents (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE chunks (
        id          INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ordinal     INTEGER NOT NULL,
        heading     TEXT NOT NULL,
        text        TEXT NOT NULL,
        UNIQUE (document_id, ordinal)
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        heading, text, content='chunks', content_rowid='id', tokenize='porter unicode61'
    );
    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, heading, text) VALUES (new.id, new.heading, new.text);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, heading, text)
        VALUES ('delete', old.id, old.heading, old.text);
    END;
    CREATE TABLE questions (
        id          INTEGER PRIMARY KEY,
        text        TEXT NOT NULL,
        status      TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        answer_json TEXT,
        error       TEXT
    );
    CREATE INDEX questions_created ON questions(created_at DESC);
    """,
)


class Database:
    """One SQLite connection plus a lock, so each unit of work is atomic across requests.

    Every repository call runs inside `read()` or `transaction()`. The lock keeps another
    request from committing (or rolling back) a half-finished operation on the shared connection.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            yield self._connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Commit when the block succeeds, roll back when it raises (or is cancelled)."""
        async with self._lock:
            try:
                yield self._connection
            except BaseException:
                await self._connection.rollback()
                raise
            await self._connection.commit()

    async def close(self) -> None:
        await self._connection.close()


async def connect(path: Path | str) -> Database:
    """Open the database, apply pending migrations and return the connection."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    try:
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            await connection.execute("PRAGMA journal_mode = WAL")
        await migrate(connection)
    except BaseException:
        await connection.close()
        raise
    return Database(connection)


async def migrate(db: aiosqlite.Connection) -> int:
    """Apply each migration newer than the stored user_version. Returns the new version."""
    async with db.execute("PRAGMA user_version") as cursor:
        row = await cursor.fetchone()
    version = int(row[0]) if row else 0
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        await db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    return len(MIGRATIONS)
