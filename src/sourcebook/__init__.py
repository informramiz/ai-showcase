"""Sourcebook: document Q&A with cited, streamed answers."""

__all__ = ["main"]


def main() -> None:
    """Console entry point: run the API with uvicorn."""
    import uvicorn

    from sourcebook.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "sourcebook.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )
