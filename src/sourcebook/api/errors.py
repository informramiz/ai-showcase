"""Translate domain errors into HTTP responses in one place."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from sourcebook.domain.errors import NotFoundError, SourcebookError, ValidationError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SourcebookError)
    async def _domain_error(_request: Request, exc: SourcebookError) -> JSONResponse:
        code = status.HTTP_400_BAD_REQUEST
        if isinstance(exc, NotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, ValidationError):
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return JSONResponse({"detail": str(exc)}, status_code=code)
