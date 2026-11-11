from sourcebook.api.events import to_sse
from sourcebook.domain.events import AnswerFailed


def test_error_event_carries_the_question_id_for_the_sidebar() -> None:
    sse = to_sse(AnswerFailed(7, "Rate limited."), 3)
    assert sse.event == "error"
    assert sse.data == {"question_id": 7, "message": "Rate limited."}
