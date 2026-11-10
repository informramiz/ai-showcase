"""Use cases for the document library: ingest, list, read, delete."""

from __future__ import annotations

from sourcebook.domain.errors import NotFoundError, ValidationError
from sourcebook.domain.models import Document
from sourcebook.ports.repositories import DocumentRepository
from sourcebook.services.chunking import chunk_document, derive_title

MIN_CONTENT_CHARS = 20


class DocumentService:
    def __init__(self, documents: DocumentRepository, max_bytes: int) -> None:
        self._documents = documents
        self._max_bytes = max_bytes

    async def ingest(
        self, content: str, title: str | None = None, *, fallback: str = ""
    ) -> Document:
        text = content.replace("\r\n", "\n").strip()
        if len(text.encode()) > self._max_bytes:
            raise ValidationError(f"Document is larger than {self._max_bytes // 1000} KB.")
        if len(text) < MIN_CONTENT_CHARS:
            raise ValidationError("Document is empty or too short to index.")
        chunks = chunk_document(text)
        name = (title or "").strip() or derive_title(text, fallback or "Untitled document")
        return await self._documents.add(name, text, chunks)

    async def ingest_upload(self, filename: str, data: bytes, title: str | None = None) -> Document:
        if not filename.lower().endswith((".md", ".markdown", ".txt")):
            raise ValidationError("Only .md and .txt files are supported.")
        if len(data) > self._max_bytes:
            raise ValidationError(f"Document is larger than {self._max_bytes // 1000} KB.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("File is not valid UTF-8 text.") from exc
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
        return await self.ingest(text, title, fallback=stem.title())

    async def list_all(self) -> list[Document]:
        return await self._documents.list_all()

    async def get(self, document_id: int) -> Document:
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError("Document", document_id)
        return document

    async def delete(self, document_id: int) -> None:
        if not await self._documents.delete(document_id):
            raise NotFoundError("Document", document_id)
