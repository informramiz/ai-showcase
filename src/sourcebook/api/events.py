"""Map domain answer events onto named Server-Sent Events."""

from __future__ import annotations

from fastapi.sse import ServerSentEvent

from sourcebook.api.schemas import AnswerOut, CitationOut, HitOut
from sourcebook.domain.events import (
    AnswerCompleted,
    AnswerEvent,
    AnswerFailed,
    CitationAdded,
    ThinkingDelta,
    TokenDelta,
    ToolCalled,
    ToolFinished,
)


def to_sse(event: AnswerEvent, sequence: int) -> ServerSentEvent:
    name: str
    data: object
    match event:
        case ThinkingDelta(text=text):
            name, data = "thinking", {"text": text}
        case TokenDelta(text=text):
            name, data = "token", {"text": text}
        case CitationAdded(index=index, citation=citation):
            name, data = "citation", CitationOut.of(index, citation).model_dump()
        case ToolCalled(call_id=call_id, name=tool, input=args):
            name, data = "tool_call", {"id": call_id, "name": tool, "input": args}
        case ToolFinished():
            name = "tool_result"
            data = {
                "id": event.call_id,
                "name": event.name,
                "summary": event.summary,
                "is_error": event.is_error,
                "hits": [HitOut.of(hit).model_dump() for hit in event.hits],
            }
        case AnswerCompleted(question_id=question_id, answer=answer):
            name, data = "done", {"question_id": question_id, **AnswerOut.of(answer).model_dump()}
        case AnswerFailed(question_id=question_id, message=message):
            name, data = "error", {"question_id": question_id, "message": message}
    return ServerSentEvent(data=data, event=name, id=str(sequence))
