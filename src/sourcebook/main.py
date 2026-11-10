"""Application factory: builds the container once per process and wires the routers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from sourcebook.adapters.llm.claude import AnthropicProvider, build_client
from sourcebook.adapters.llm.fake import FakeProvider
from sourcebook.adapters.sqlite.database import connect
from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.adapters.sqlite.questions import SqliteQuestionRepository
from sourcebook.api.deps import Container
from sourcebook.api.errors import register_error_handlers
from sourcebook.api.routes import documents, questions, ui
from sourcebook.api.schemas import HealthOut
from sourcebook.config import Settings, get_settings
from sourcebook.ports.llm import LLMProvider
from sourcebook.services.answering import AnswerService
from sourcebook.services.documents import DocumentService
from sourcebook.services.tools import CalculateTool, SearchDocumentsTool, ToolRegistry

logger = logging.getLogger("sourcebook")
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def build_provider(settings: Settings) -> LLMProvider:
    """Claude when a key is configured, the deterministic fake otherwise."""
    if not settings.use_anthropic:
        return FakeProvider(token_delay=0.012)
    if settings.anthropic_api_key is None:
        raise RuntimeError("SOURCEBOOK_PROVIDER=anthropic needs ANTHROPIC_API_KEY to be set.")
    base_url = str(settings.anthropic_base_url) if settings.anthropic_base_url else None
    client = build_client(settings.anthropic_api_key.get_secret_value(), base_url)
    return AnthropicProvider(
        client,
        server_fallback=base_url is None,
        model=settings.model,
        effort=settings.effort,
        max_tokens=settings.max_output_tokens,
    )


async def seed_samples(service: DocumentService) -> int:
    """Load the bundled sample handbook so a fresh install has something to ask about."""
    added = 0
    for entry in sorted(resources.files("sourcebook.seed").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            await service.ingest(entry.read_text(encoding="utf-8"))
            added += 1
    return added


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = await connect(settings.database_path)
        document_repo = SqliteDocumentRepository(db)
        document_service = DocumentService(document_repo, settings.max_upload_bytes)
        llm = provider or build_provider(settings)
        tools = ToolRegistry(
            [SearchDocumentsTool(document_repo, settings.search_top_k), CalculateTool()]
        )
        answers = AnswerService(
            llm, tools, SqliteQuestionRepository(db), max_turns=settings.max_agent_turns
        )
        if settings.seed_samples and await document_repo.count() == 0:
            logger.info("Seeded %d sample documents", await seed_samples(document_service))
        logger.info("Sourcebook ready, provider=%s", llm.name)
        app.state.container = Container(settings, db, document_repo, document_service, answers)
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(
        title="Sourcebook",
        summary="Ask questions about your documents. Answers stream in with exact citations.",
        version="1.0.0",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(documents.router)
    app.include_router(questions.router)
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/health", tags=["meta"])
    async def health() -> HealthOut:
        container: Container = app.state.container
        return HealthOut(
            status="ok",
            provider=container.answers.provider_name,
            documents=await container.document_repo.count(),
        )

    return app
