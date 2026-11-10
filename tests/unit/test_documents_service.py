import pytest

from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.domain.errors import NotFoundError, ValidationError
from sourcebook.services.documents import DocumentService


@pytest.fixture
def service(document_repo: SqliteDocumentRepository) -> DocumentService:
    return DocumentService(document_repo, max_bytes=2_000)


async def test_ingest_derives_title_and_chunks(service: DocumentService) -> None:
    doc = await service.ingest("# Leave policy\r\n\r\nEmployees get 25 days of paid leave.")
    assert doc.title == "Leave policy"
    assert doc.chunk_count == 1
    assert "\r" not in doc.content


async def test_explicit_title_wins(service: DocumentService) -> None:
    doc = await service.ingest("# Heading\nSome body text that is long enough.", "  Mine  ")
    assert doc.title == "Mine"


async def test_untitled_text_gets_a_placeholder(service: DocumentService) -> None:
    doc = await service.ingest("Plain text without any heading at all.")
    assert doc.title == "Untitled document"


@pytest.mark.parametrize(
    ("content", "message"),
    [("short", "too short"), ("x" * 2_001, "larger than 2 KB")],
)
async def test_ingest_validation(service: DocumentService, content: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        await service.ingest(content)


async def test_upload_uses_the_filename_as_fallback_title(service: DocumentService) -> None:
    doc = await service.ingest_upload(
        "dir/parental_leave-policy.md", b"Sixteen weeks of paid leave."
    )
    assert doc.title == "Parental Leave Policy"


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("report.pdf", b"whatever", "Only .md and .txt"),
        ("big.txt", b"x" * 2_001, "larger than"),
        ("bad.txt", b"\xff\xfe\xfa not utf8 at all", "not valid UTF-8"),
    ],
)
async def test_upload_validation(
    service: DocumentService, filename: str, data: bytes, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        await service.ingest_upload(filename, data)


async def test_get_list_and_delete(service: DocumentService) -> None:
    doc = await service.ingest("Some body text that is long enough.")
    assert (await service.get(doc.id)).id == doc.id
    assert [d.id for d in await service.list_all()] == [doc.id]
    await service.delete(doc.id)
    with pytest.raises(NotFoundError):
        await service.get(doc.id)
    with pytest.raises(NotFoundError):
        await service.delete(doc.id)
