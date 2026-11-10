"""Tools the model may call. Each validates its own (untrusted) input with Pydantic."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from sourcebook.domain.errors import ValidationError
from sourcebook.domain.models import SearchHit
from sourcebook.ports.llm import ToolCall, ToolResult, ToolSpec
from sourcebook.ports.repositories import DocumentRepository
from sourcebook.services import calculator


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    result: ToolResult
    summary: str
    input: dict[str, object]


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def run(self, call: ToolCall) -> ToolOutcome: ...


def _schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def _error(call: ToolCall, message: str, raw: object) -> ToolOutcome:
    shown = raw if isinstance(raw, dict) else {"input": raw}
    return ToolOutcome(
        ToolResult(call.id, text=message, is_error=True), f"Error: {message}", dict(shown)
    )


# ---- search_documents ------------------------------------------------------


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=2,
        max_length=200,
        description="Keywords to search for. Use the terms the documents would use.",
    )
    top_k: int = Field(default=4, ge=1, le=8, description="How many passages to return.")


class SearchDocumentsTool:
    def __init__(self, documents: DocumentRepository, default_top_k: int = 4) -> None:
        self._documents = documents
        self._default_top_k = default_top_k

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_documents",
            description=(
                "Full-text search over the user's uploaded documents. Returns the best-matching "
                "passages as citable search results. Call it before answering any question about "
                "the documents; call it again with different keywords if the first results miss."
            ),
            input_schema=_schema(SearchInput),
        )

    async def run(self, call: ToolCall) -> ToolOutcome:
        raw = call.input if isinstance(call.input, dict) else {}
        if isinstance(raw, dict) and "top_k" not in raw:
            raw = {**raw, "top_k": self._default_top_k}
        try:
            args = SearchInput.model_validate(raw)
        except PydanticValidationError as exc:
            return _error(call, f"Invalid input: {exc.errors()[0]['msg']}", call.input)

        hits: tuple[SearchHit, ...] = tuple(await self._documents.search(args.query, args.top_k))
        shown: dict[str, object] = {"query": args.query, "top_k": args.top_k}
        if not hits:
            return ToolOutcome(
                ToolResult(call.id, text=f"No passages matched {args.query!r}."),
                "No matching passages",
                shown,
            )
        return ToolOutcome(ToolResult(call.id, hits=hits), _describe(hits), shown)


def _describe(hits: Iterable[SearchHit]) -> str:
    items = list(hits)
    titles = sorted({hit.document_title for hit in items})
    noun = "passage" if len(items) == 1 else "passages"
    return f"{len(items)} {noun} from {', '.join(titles)}"


# ---- calculate ---------------------------------------------------------------


class CalculateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(
        min_length=1,
        max_length=calculator.MAX_EXPRESSION_LENGTH,
        description="Arithmetic only, e.g. '(45 * 12) + 300'. Numbers and + - * / // % ** ( ).",
    )


class CalculateTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculate",
            description=(
                "Evaluate an arithmetic expression exactly. Use it for any sum, product, "
                "percentage or unit conversion based on figures found in the documents."
            ),
            input_schema=_schema(CalculateInput),
        )

    async def run(self, call: ToolCall) -> ToolOutcome:
        try:
            args = CalculateInput.model_validate(call.input)
        except PydanticValidationError as exc:
            return _error(call, f"Invalid input: {exc.errors()[0]['msg']}", call.input)
        shown: dict[str, object] = {"expression": args.expression}
        try:
            value = calculator.evaluate(args.expression)
        except ValidationError as exc:
            return ToolOutcome(ToolResult(call.id, text=str(exc), is_error=True), str(exc), shown)
        formatted = calculator.format_number(value)
        return ToolOutcome(
            ToolResult(call.id, text=f"{args.expression} = {formatted}"),
            f"= {formatted}",
            shown,
        )


# ---- registry ---------------------------------------------------------------


class ToolRegistry:
    """Fixed, ordered set of tools. Order is stable so the prompt prefix stays cacheable."""

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    async def run(self, call: ToolCall) -> ToolOutcome:
        tool = self._tools.get(call.name)
        if tool is None:
            return _error(call, f"Unknown tool {call.name!r}.", call.input)
        return await tool.run(call)
