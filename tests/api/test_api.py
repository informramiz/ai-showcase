import json
from typing import Any

from fastapi.testclient import TestClient

NOTE = "# Parental leave\n\nEmployees receive sixteen weeks of paid parental leave."


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        name, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data += line.removeprefix("data:").strip()
        if data:
            events.append((name, json.loads(data)))
    return events


def test_health_reports_provider_and_seeded_documents(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok", "provider": "fake", "documents": 4}


def test_document_crud(empty_client: TestClient) -> None:
    created = empty_client.post("/api/documents", json={"content": NOTE})
    assert created.status_code == 201
    doc = created.json()
    assert doc["title"] == "Parental leave"
    assert doc["chunk_count"] == 1

    listed = empty_client.get("/api/documents").json()
    assert [d["id"] for d in listed] == [doc["id"]]
    assert "content" not in listed[0]
    assert empty_client.get(f"/api/documents/{doc['id']}").json()["content"] == NOTE

    assert empty_client.delete(f"/api/documents/{doc['id']}").status_code == 204
    missing = empty_client.get(f"/api/documents/{doc['id']}")
    assert missing.status_code == 404
    assert "detail" in missing.json()
    assert empty_client.delete(f"/api/documents/{doc['id']}").status_code == 404


def test_document_validation_errors(empty_client: TestClient) -> None:
    assert empty_client.post("/api/documents", json={"content": ""}).status_code == 422
    too_short = empty_client.post("/api/documents", json={"content": "tiny"})
    assert too_short.status_code == 422
    assert "too short" in too_short.json()["detail"]


def test_upload(empty_client: TestClient) -> None:
    ok = empty_client.post(
        "/api/documents/upload",
        files={"file": ("leave.md", NOTE.encode(), "text/markdown")},
        data={"title": "Leave"},
    )
    assert ok.status_code == 201
    assert ok.json()["title"] == "Leave"
    bad = empty_client.post(
        "/api/documents/upload", files={"file": ("x.pdf", b"%PDF-1.7 binary", "application/pdf")}
    )
    assert bad.status_code == 422


def test_question_lifecycle_streams_a_cited_answer(client: TestClient) -> None:
    created = client.post(
        "/api/questions", json={"question": "Who do I contact if my laptop is stolen?"}
    )
    assert created.status_code == 201
    question = created.json()
    assert question["status"] == "pending"
    assert question["stream_url"] == f"/api/questions/{question['id']}/stream"

    response = client.get(question["stream_url"])
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [n for n, _ in events]
    assert names.index("tool_call") < names.index("token")
    assert "tool_result" in names
    assert "token" in names
    assert "citation" in names
    assert names[-1] == "done"
    done = events[-1][1]
    assert done["provider"] == "fake"
    assert done["citations"][0]["title"].startswith("Security")
    assert done["citations"][0]["document_id"] is not None
    assert "security team" in done["text"]

    stored = client.get(f"/api/questions/{question['id']}").json()
    assert stored["status"] == "answered"
    assert stored["answer"]["text"] == done["text"]

    replay = parse_sse(client.get(question["stream_url"]).text)
    assert replay[-1] == ("done", done)

    recent = client.get("/api/questions", params={"limit": 5}).json()
    assert [q["id"] for q in recent] == [question["id"]]


def test_calculator_tool_shows_up_in_the_stream(client: TestClient) -> None:
    question = client.post(
        "/api/questions",
        json={"question": "What is the daily meal limit, and what would 3 * 75 cost?"},
    ).json()
    events = parse_sse(client.get(question["stream_url"]).text)
    tools = [d["name"] for n, d in events if n == "tool_call"]
    assert tools == ["search_documents", "calculate"]
    assert "225" in events[-1][1]["text"]


def test_question_errors(client: TestClient) -> None:
    assert client.post("/api/questions", json={"question": ""}).status_code == 422
    assert client.post("/api/questions", json={"question": "   "}).status_code == 422
    assert client.get("/api/questions/999").status_code == 404
    assert client.get("/api/questions/999/stream").status_code == 404
    assert client.get("/api/questions", params={"limit": 0}).status_code == 422


def test_ui_page_and_library_partials(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Ask your documents" in page.text
    assert "Who do I contact if my laptop is stolen?" in page.text
    assert "Demo model (no API key)" in page.text
    assert client.get("/static/app.js").status_code == 200

    library = client.get("/ui/documents")
    assert library.text.count('class="doc-open"') == 4

    added = client.post("/ui/documents", data={"content": NOTE})
    assert "Parental leave" in added.text
    assert "is-new" in added.text

    uploaded = client.post(
        "/ui/documents", files={"file": ("benefits.txt", b"Dental cover is included for staff.")}
    )
    assert "Benefits" in uploaded.text

    rejected = client.post("/ui/documents", data={"content": "tiny"})
    assert 'class="alert"' in rejected.text

    doc_id = client.get("/api/documents").json()[0]["id"]
    removed = client.delete(f"/ui/documents/{doc_id}")
    assert removed.text.count('class="doc-open"') == 5
    assert 'class="alert"' in client.delete(f"/ui/documents/{doc_id}").text


def test_openapi_schema_is_served(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/questions/{question_id}/stream" in paths
    assert "/" not in paths
