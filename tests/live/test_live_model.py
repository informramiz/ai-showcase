"""Opt-in smoke test against a real model. Skipped unless ANTHROPIC_API_KEY is set.

Run: ANTHROPIC_API_KEY=... [ANTHROPIC_BASE_URL=https://openrouter.ai/api] \
     [SOURCEBOOK_MODEL=anthropic/claude-sonnet-5.5] uv run pytest -m live

It makes a handful of real (paid) model calls, so it is excluded from the default run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from sourcebook.config import ProviderChoice, Settings
from sourcebook.main import create_app
from tests.api.test_api import parse_sse

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


def _settings(tmp_path: Path, api_key: str) -> Settings:
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    return Settings(
        _env_file=None,
        database_path=tmp_path / "live.db",
        provider=ProviderChoice.ANTHROPIC,
        anthropic_api_key=SecretStr(api_key),
        anthropic_base_url=AnyHttpUrl(base_url) if base_url else None,
        model=os.environ.get("SOURCEBOOK_MODEL", "claude-sonnet-5-5"),
        effort="low",
        max_output_tokens=4_000,
    )


@pytest.fixture
def live(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path, os.environ["ANTHROPIC_API_KEY"]))) as client:
        yield client


def ask(client: TestClient, question: str) -> list[tuple[str, dict[str, object]]]:
    created = client.post("/api/questions", json={"question": question})
    assert created.status_code == 201
    stream = client.get(created.json()["stream_url"])
    assert stream.status_code == 200
    return parse_sse(stream.text)


def names(events: list[tuple[str, dict[str, object]]]) -> list[str]:
    return [name for name, _ in events]


def test_search_and_citation(live: TestClient) -> None:
    events = ask(live, "How many days a week can I work remotely?")
    assert names(events)[-1] == "done", events[-1]
    assert any(n == "tool_call" and d["name"] == "search_documents" for n, d in events)
    citations = [d for n, d in events if n == "citation"]
    assert citations
    assert any("four days" in str(c["cited_text"]).lower() for c in citations)


def test_calculation_uses_the_calculator_tool(live: TestClient) -> None:
    events = ask(live, "What is the daily meal limit when travelling, and what would 3 days cost?")
    assert names(events)[-1] == "done", events[-1]
    assert any(n == "tool_call" and d["name"] == "calculate" for n, d in events)
    done = events[-1][1]
    assert "225" in str(done["text"])


def test_finished_answer_replays_identically(live: TestClient) -> None:
    created = live.post(
        "/api/questions", json={"question": "Who do I contact if my laptop is stolen?"}
    )
    url = created.json()["stream_url"]
    first = parse_sse(live.get(url).text)
    assert names(first)[-1] == "done", first[-1]
    replay = parse_sse(live.get(url).text)
    assert replay[-1][1]["text"] == first[-1][1]["text"]
    assert [d["index"] for n, d in replay if n == "citation"] == [
        d["index"] for n, d in first if n == "citation"
    ]


def test_uploaded_document_can_be_cited(live: TestClient) -> None:
    note = "# Pet policy\n\nEach employee may bring one dog to the Lisbon hub on Fridays."
    upload = live.post(
        "/api/documents/upload", files={"file": ("pets.md", note.encode(), "text/markdown")}
    )
    assert upload.status_code == 201
    events = ask(live, "Can I bring my dog to the office?")
    assert names(events)[-1] == "done", events[-1]
    assert any("dog" in str(d["cited_text"]).lower() for n, d in events if n == "citation")


def test_bad_key_gives_a_clean_error(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, "sk-invalid-key-for-test"))) as client:
        events = ask(client, "How many days a week can I work remotely?")
    assert names(events)[-1] == "error"
    message = str(events[-1][1]["message"])
    assert "rejected" in message.lower()
    assert "sk-invalid" not in message
