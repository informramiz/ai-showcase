import pytest
from pydantic import SecretStr

from sourcebook.config import ProviderChoice, Settings
from sourcebook.domain.models import Citation


@pytest.mark.parametrize(
    ("provider", "key", "expected"),
    [
        (ProviderChoice.AUTO, None, False),
        (ProviderChoice.AUTO, "   ", False),
        (ProviderChoice.AUTO, "sk-placeholder", True),
        (ProviderChoice.FAKE, "sk-placeholder", False),
        (ProviderChoice.ANTHROPIC, None, True),
    ],
)
def test_use_anthropic(provider: ProviderChoice, key: str | None, expected: bool) -> None:
    settings = Settings(
        _env_file=None,
        provider=provider,
        anthropic_api_key=SecretStr(key) if key is not None else None,
    )
    assert settings.use_anthropic is expected


def test_key_is_read_from_the_standard_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-from-env"
    assert "sk-from-env" not in repr(settings)


def test_citation_document_id() -> None:
    assert Citation("doc://12#chunk-3", "t", "q").document_id == 12
    assert Citation("https://example.com", "t", "q").document_id is None
    assert Citation("doc://abc", "t", "q").document_id is None
