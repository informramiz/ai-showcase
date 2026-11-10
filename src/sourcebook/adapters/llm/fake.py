"""A deterministic stand-in for the model.

It follows the same protocol a real model would (think, call search_documents, maybe call
calculate, then answer with citations), so the whole app, its tests and CI run without an
API key. Answers are extractive: the best-matching sentences from the search results.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from sourcebook.domain.models import Citation, SearchHit
from sourcebook.ports.llm import (
    AssistantMessage,
    CitationChunk,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolResultsMessage,
    TurnCompleted,
    TurnEvent,
    TurnRequest,
    UserMessage,
)

_WORD = re.compile(r"[a-z0-9']+")
_EXPRESSION = re.compile(r"\d[\d,.]*(?:\s*[-+*/x\u00d7]\s*\(?\s*\d[\d,.]*\)?)+")
_STOP = frozenset(
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
        "much",
        "many",
    ]
)
MAX_SENTENCES = 3
# A sentence must score at least this share of the best one to be quoted.
RELEVANCE_FLOOR = 0.75


class FakeProvider:
    def __init__(self, token_delay: float = 0.0) -> None:
        self._delay = token_delay

    @property
    def name(self) -> str:
        return "fake"

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[TurnEvent]:
        question = next((m.text for m in request.messages if isinstance(m, UserMessage)), "")
        results = [m for m in request.messages if isinstance(m, ToolResultsMessage)]
        if not results:
            async for event in self._plan(question):
                yield event
        else:
            async for event in self._answer(question, results):
                yield event

    async def _plan(self, question: str) -> AsyncIterator[TurnEvent]:
        terms = _terms(question)
        query = " ".join(terms) or question
        yield ThinkingChunk(f"The user is asking about: {query}. ")
        await self._pause()
        yield ThinkingChunk("I'll search their documents for the relevant passages first.")
        calls = [ToolCall("fake_search_1", "search_documents", {"query": query, "top_k": 4})]
        expression = _expression(question)
        if expression:
            calls.append(ToolCall("fake_calc_1", "calculate", {"expression": expression}))
        message = AssistantMessage("", tuple(calls))
        yield TurnCompleted(message, "tool_use")

    async def _answer(
        self, question: str, results: list[ToolResultsMessage]
    ) -> AsyncIterator[TurnEvent]:
        hits = [hit for msg in results for r in msg.results for hit in r.hits]
        calc = next(
            (r.text for msg in results for r in msg.results if r.text and "=" in r.text), None
        )
        picked = _best_sentences(question, hits)

        text_parts: list[str] = []
        if not picked:
            reply = "I couldn't find anything about that in your documents. "
            reply += "Try rephrasing, or upload a document that covers it."
            async for event in self._words(reply, text_parts):
                yield event
        else:
            async for event in self._words("Here's what your documents say. ", text_parts):
                yield event
            for hit, sentence in picked:
                async for event in self._words(sentence, text_parts):
                    yield event
                yield CitationChunk(Citation(hit.source, hit.title, sentence))
                async for event in self._words(" ", text_parts):
                    yield event
        if calc:
            async for event in self._words(f"\n\nWorked out exactly: {calc}.", text_parts):
                yield event
        yield TurnCompleted(AssistantMessage("".join(text_parts), ()), "end_turn")

    async def _words(self, text: str, sink: list[str]) -> AsyncIterator[TurnEvent]:
        for token in re.findall(r"\S+\s*|\s+", text):
            sink.append(token)
            yield TextChunk(token)
            await self._pause()

    async def _pause(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)


def _terms(text: str) -> list[str]:
    seen: list[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) > 2 and word not in _STOP and word not in seen:
            seen.append(word)
    return seen


def _expression(question: str) -> str | None:
    match = _EXPRESSION.search(question)
    if not match:
        return None
    return match.group(0).replace("\u00d7", "*").replace("x", "*").replace(",", "")


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _best_sentences(question: str, hits: list[SearchHit]) -> list[tuple[SearchHit, str]]:
    wanted = {_stem(t) for t in _terms(question)}
    scored: list[tuple[float, int, SearchHit, str]] = []
    order = 0
    for rank, hit in enumerate(hits):
        for sentence in hit.sentences:
            words = {_stem(w) for w in _WORD.findall(sentence.lower())}
            overlap = len(wanted & words)
            if overlap:
                # Prefer overlap, then better-ranked hits, then document order.
                scored.append((overlap - rank * 0.1, -order, hit, sentence))
            order += 1
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    picked: list[tuple[SearchHit, str]] = []
    floor = scored[0][0] * RELEVANCE_FLOOR if scored else 0.0
    for score, _order, hit, sentence in scored:
        if score < floor:
            break
        if sentence not in (s for _, s in picked):
            picked.append((hit, sentence))
        if len(picked) == MAX_SENTENCES:
            break
    return picked
