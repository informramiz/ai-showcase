import pytest
from pydantic import SecretStr

from sourcebook.adapters.llm.claude import AnthropicProvider
from sourcebook.adapters.llm.fake import FakeProvider
from sourcebook.adapters.sqlite.documents import SqliteDocumentRepository
from sourcebook.config import ProviderChoice, Settings
from sourcebook.main import build_provider, seed_samples
from sourcebook.services.documents import DocumentService


def _settings(provider: ProviderChoice, key: str | None) -> Settings:
    return Settings(
        _env_file=None,
        provider=provider,
        anthropic_api_key=SecretStr(key) if key else None,
    )


def test_build_provider_defaults_to_the_fake() -> None:
    assert isinstance(build_provider(_settings(ProviderChoice.AUTO, None)), FakeProvider)


def test_build_provider_uses_claude_when_a_key_is_set() -> None:
    provider = build_provider(_settings(ProviderChoice.AUTO, "sk-placeholder"))
    assert isinstance(provider, AnthropicProvider)
    assert provider.name.startswith("claude")


def test_forcing_claude_without_a_key_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_provider(_settings(ProviderChoice.ANTHROPIC, None))


async def test_seed_samples_loads_the_bundled_handbook(
    document_repo: SqliteDocumentRepository,
) -> None:
    assert await seed_samples(DocumentService(document_repo, 500_000)) == 4
    titles = {d.title for d in await document_repo.list_all()}
    assert len(titles) == 4
