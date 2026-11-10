"""Typed domain errors. The API layer maps each one to an HTTP status."""


class SourcebookError(Exception):
    """Base class for expected, user-facing failures."""


class NotFoundError(SourcebookError):
    def __init__(self, kind: str, id_: int) -> None:
        super().__init__(f"{kind} {id_} not found")
        self.kind = kind
        self.id = id_


class ValidationError(SourcebookError):
    """Input is well-formed but breaks a domain rule (empty document, too large, ...)."""


class ProviderError(SourcebookError):
    """The language model could not produce an answer. Message is safe to show users."""


class RefusalError(ProviderError):
    def __init__(self, category: str | None) -> None:
        label = f" ({category})" if category else ""
        super().__init__(f"The model declined to answer this question{label}.")
        self.category = category
