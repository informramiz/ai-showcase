# syntax=docker/dockerfile:1
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PATH="/app/.venv/bin:$PATH" \
    SOURCEBOOK_HOST=0.0.0.0 \
    SOURCEBOOK_PORT=8000 \
    SOURCEBOOK_DATABASE_PATH=/data/sourcebook.db

WORKDIR /app

# Dependencies first so code changes do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

CMD ["sourcebook"]
