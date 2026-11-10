"""SQLite + FTS5 implementation of `DocumentRepository`."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import aiosqlite

from sourcebook.domain.models import Document, NewChunk, SearchHit

_WORD = re.compile(r"[\w']+", re.UNICODE)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "so",
        "that",
        "the",
        "their",
        "there",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)
MAX_QUERY_TERMS = 12


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: quoted terms joined with OR (BM25 ranks them)."""
    terms: list[str] = []
    for raw in _WORD.findall(text.lower()):
        word = raw.strip("'")
        if len(word) > 1 and word not in _STOPWORDS and word not in terms:
            terms.append(word)
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms[:MAX_QUERY_TERMS])


class SqliteDocumentRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def add(self, title: str, content: str, chunks: Sequence[NewChunk]) -> Document:
        created = datetime.now(UTC)
        cursor = await self._db.execute(
            "INSERT INTO documents (title, content, created_at) VALUES (?, ?, ?)",
            (title, content, created.isoformat()),
        )
        document_id = cursor.lastrowid
        assert document_id is not None
        await self._db.executemany(
            "INSERT INTO chunks (document_id, ordinal, heading, text) VALUES (?, ?, ?, ?)",
            [(document_id, c.ordinal, c.heading, c.text) for c in chunks],
        )
        await self._db.commit()
        return Document(document_id, title, content, created, len(chunks))

    async def get(self, document_id: int) -> Document | None:
        async with self._db.execute(
            _SELECT + " WHERE d.id = ? GROUP BY d.id", (document_id,)
        ) as cur:
            row = await cur.fetchone()
        return _document(row) if row else None

    async def list_all(self) -> list[Document]:
        async with self._db.execute(_SELECT + " GROUP BY d.id ORDER BY d.created_at, d.id") as cur:
            rows = await cur.fetchall()
        return [_document(row) for row in rows]

    async def delete(self, document_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM documents") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        match = fts_query(query)
        if match is None:
            return []
        sql = """
            SELECT c.document_id, d.title, c.ordinal, c.heading, c.text,
                   bm25(chunks_fts, 2.0, 1.0) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        async with self._db.execute(sql, (match, limit)) as cur:
            rows = await cur.fetchall()
        return [
            SearchHit(
                document_id=row["document_id"],
                document_title=row["title"],
                chunk_ordinal=row["ordinal"],
                heading=row["heading"],
                sentences=tuple(s for s in row["text"].split("\n") if s),  # one sentence per line
                score=round(-float(row["rank"]), 4),
            )
            for row in rows
        ]


_SELECT = """
    SELECT d.id, d.title, d.content, d.created_at, COUNT(c.id) AS chunk_count
    FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
"""


def _document(row: aiosqlite.Row) -> Document:
    return Document(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
        chunk_count=row["chunk_count"],
    )
