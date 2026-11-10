from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.ports.llm import ToolCall
from sourcebook.services.documents import DocumentService
from sourcebook.services.tools import CalculateTool, SearchDocumentsTool, ToolRegistry


async def test_search_returns_hits_and_summary(
    documents: DocumentService, document_repo: SqliteDocumentRepository
) -> None:
    tool = SearchDocumentsTool(document_repo, default_top_k=2)
    outcome = await tool.run(ToolCall("t1", "search_documents", {"query": "meal allowance"}))

    assert not outcome.result.is_error
    assert outcome.result.hits[0].heading == "Meals"
    assert outcome.input == {"query": "meal allowance", "top_k": 2}
    assert outcome.summary.endswith("from Travel policy")


async def test_search_with_no_matches_says_so(
    documents: DocumentService, document_repo: SqliteDocumentRepository
) -> None:
    outcome = await SearchDocumentsTool(document_repo).run(
        ToolCall("t1", "search_documents", {"query": "submarine"})
    )
    assert outcome.result.hits == ()
    assert "No passages matched" in outcome.result.text
    assert outcome.summary == "No matching passages"


async def test_search_rejects_invalid_input(document_repo: SqliteDocumentRepository) -> None:
    tool = SearchDocumentsTool(document_repo)
    for bad in ({"query": "x"}, {"query": "ok query", "extra": 1}, "not a dict"):
        outcome = await tool.run(ToolCall("t1", "search_documents", bad))
        assert outcome.result.is_error
        assert outcome.summary.startswith("Error: Invalid input")


async def test_calculate_formats_result() -> None:
    outcome = await CalculateTool().run(ToolCall("c1", "calculate", {"expression": "75 * 3"}))
    assert outcome.result.text == "75 * 3 = 225"
    assert outcome.summary == "= 225"
    assert not outcome.result.is_error


async def test_calculate_reports_domain_errors_to_the_model() -> None:
    outcome = await CalculateTool().run(ToolCall("c1", "calculate", {"expression": "1/0"}))
    assert outcome.result.is_error
    assert outcome.result.text == "Division by zero."


async def test_calculate_rejects_bad_schema() -> None:
    outcome = await CalculateTool().run(ToolCall("c1", "calculate", {"expr": "1"}))
    assert outcome.result.is_error


async def test_registry_lists_specs_in_order_and_rejects_unknown_tools(
    document_repo: SqliteDocumentRepository,
) -> None:
    registry = ToolRegistry([SearchDocumentsTool(document_repo), CalculateTool()])
    assert [s.name for s in registry.specs] == ["search_documents", "calculate"]
    assert "title" not in registry.specs[0].input_schema
    assert registry.specs[0].input_schema["required"] == ["query"]

    outcome = await registry.run(ToolCall("x", "delete_everything", {}))
    assert outcome.result.is_error
    assert "Unknown tool" in outcome.result.text

    ok = await registry.run(ToolCall("c", "calculate", {"expression": "2+2"}))
    assert ok.result.text == "2+2 = 4"
