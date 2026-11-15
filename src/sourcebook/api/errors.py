"""Translate domain errors into HTTP responses in one place."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sourcebook.domain.errors import (
    NotFoundError,
    SourcebookError,
    TooLargeError,
    ValidationError,
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SourcebookError)
    async def _domain_error(_request: Request, exc: SourcebookError) -> JSONResponse:
        code = status.HTTP_400_BAD_REQUEST
        if isinstance(exc, NotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, TooLargeError):
            code = status.HTTP_413_CONTENT_TOO_LARGE
        elif isinstance(exc, ValidationError):
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.exception_handler(RequestValidationError)
    async def _request_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Same `{"detail": "<message>"}` shape as the domain errors, as the OpenAPI schema says."""
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return JSONResponse({"detail": problems}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
