"""SQLite implementation of `QuestionRepository`. Answers are stored as JSON."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from sourcebook.domain.models import Answer, Citation, Question, QuestionStatus, ToolStep, Usage


class SqliteQuestionRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def create(self, text: str) -> Question:
        created = datetime.now(UTC)
        cursor = await self._db.execute(
            "INSERT INTO questions (text, status, created_at) VALUES (?, ?, ?)",
            (text, QuestionStatus.PENDING.value, created.isoformat()),
        )
        await self._db.commit()
        assert cursor.lastrowid is not None
        return Question(cursor.lastrowid, text, QuestionStatus.PENDING, created)

    async def get(self, question_id: int) -> Question | None:
        async with self._db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)) as cur:
            row = await cur.fetchone()
        return _question(row) if row else None

    async def list_recent(self, limit: int) -> list[Question]:
        async with self._db.execute(
            "SELECT * FROM questions ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [_question(row) for row in rows]

    async def save_answer(self, question_id: int, answer: Answer) -> None:
        await self._db.execute(
            "UPDATE questions SET status = ?, answer_json = ?, error = NULL WHERE id = ?",
            (QuestionStatus.ANSWERED.value, json.dumps(answer_to_json(answer)), question_id),
        )
        await self._db.commit()

    async def save_failure(self, question_id: int, message: str) -> None:
        await self._db.execute(
            "UPDATE questions SET status = ?, error = ? WHERE id = ?",
            (QuestionStatus.FAILED.value, message, question_id),
        )
        await self._db.commit()


def answer_to_json(answer: Answer) -> dict[str, Any]:
    return {
        "text": answer.text,
        "provider": answer.provider,
        "citations": [
            {"source": c.source, "title": c.title, "cited_text": c.cited_text}
            for c in answer.citations
        ],
        "steps": [
            {"name": s.name, "input": s.input, "summary": s.summary, "is_error": s.is_error}
            for s in answer.steps
        ],
        "usage": {
            "input_tokens": answer.usage.input_tokens,
            "output_tokens": answer.usage.output_tokens,
            "cache_read_input_tokens": answer.usage.cache_read_input_tokens,
            "cache_creation_input_tokens": answer.usage.cache_creation_input_tokens,
        },
    }


def answer_from_json(data: dict[str, Any]) -> Answer:
    return Answer(
        text=data["text"],
        provider=data["provider"],
        citations=tuple(Citation(**c) for c in data["citations"]),
        steps=tuple(ToolStep(**s) for s in data["steps"]),
        usage=Usage(**data.get("usage", {})),
    )


def _question(row: aiosqlite.Row) -> Question:
    raw = row["answer_json"]
    return Question(
        id=row["id"],
        text=row["text"],
        status=QuestionStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        answer=answer_from_json(json.loads(raw)) if raw else None,
        error=row["error"],
    )
