"""JSON API for the document library."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from sourcebook.api.deps import DocumentsDep
from sourcebook.api.schemas import DocumentIn, DocumentOut, DocumentSummary, ErrorOut

router = APIRouter(prefix="/api/documents", tags=["documents"])
_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorOut},
    413: {"model": ErrorOut},
    422: {"model": ErrorOut},
}


@router.get("")
async def list_documents(documents: DocumentsDep) -> list[DocumentSummary]:
    return [DocumentSummary.of(d) for d in await documents.list_all()]


@router.post("", status_code=status.HTTP_201_CREATED, responses=_ERRORS)
async def create_document(body: DocumentIn, documents: DocumentsDep) -> DocumentOut:
    return DocumentOut.of(await documents.ingest(body.content, body.title))


@router.post("/upload", status_code=status.HTTP_201_CREATED, responses=_ERRORS)
async def upload_document(
    documents: DocumentsDep,
    file: Annotated[UploadFile, File(description="A .md or .txt file")],
    title: Annotated[str | None, Form(max_length=120)] = None,
) -> DocumentOut:
    data = await documents.read_upload(file)
    return DocumentOut.of(await documents.ingest_upload(file.filename or "upload.txt", data, title))


@router.get("/{document_id}", responses=_ERRORS)
async def get_document(document_id: int, documents: DocumentsDep) -> DocumentOut:
    return DocumentOut.of(await documents.get(document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
async def delete_document(document_id: int, documents: DocumentsDep) -> Response:
    await documents.delete(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
