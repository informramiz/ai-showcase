"""Baseline browser-hardening headers (OWASP Secure Headers recommendations)."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

# The UI ships its own script, styles and vendored htmx: nothing is loaded from elsewhere.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
SELF_HOSTED_DOCS = ("/docs", "/redoc")  # Swagger UI / ReDoc load assets from a CDN


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def _headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not request.url.path.startswith(SELF_HOSTED_DOCS):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response
