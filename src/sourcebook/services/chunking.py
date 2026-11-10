"""Split a markdown or plain-text document into heading-scoped chunks and sentences."""

from __future__ import annotations

import re

from sourcebook.domain.models import NewChunk

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
MAX_CHUNK_CHARS = 1_200


def split_sentences(text: str) -> tuple[str, ...]:
    """Break a passage into sentences. Each list item counts as one sentence."""
    sentences: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _BULLET.match(stripped):
            sentences.append(_BULLET.sub("", stripped))
            continue
        sentences.extend(s.strip() for s in _SENTENCE_END.split(stripped) if s.strip())
    return tuple(sentences)


def chunk_document(content: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[NewChunk]:
    """Group text under its nearest heading, splitting long sections on sentence boundaries."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in content.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(2).strip(), []))
        else:
            sections[-1][1].append(line)

    chunks: list[NewChunk] = []
    for heading, lines in sections:
        sentences = split_sentences("\n".join(lines))
        buffer: list[str] = []
        size = 0
        for sentence in sentences:
            if buffer and size + len(sentence) > max_chars:
                chunks.append(NewChunk(len(chunks), heading, "\n".join(buffer)))
                buffer, size = [], 0
            buffer.append(sentence)
            size += len(sentence) + 1
        if buffer:
            chunks.append(NewChunk(len(chunks), heading, "\n".join(buffer)))
    return chunks


def derive_title(content: str, fallback: str) -> str:
    """Use the first heading as the title when the user did not give one."""
    for line in content.splitlines():
        match = _HEADING.match(line)
        if match:
            return match.group(2).strip()[:120]
    return fallback[:120]
