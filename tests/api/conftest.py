from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sourcebook.adapters.llm.fake import FakeProvider
from sourcebook.config import Settings
from sourcebook.main import create_app


def make_client(tmp_path: Path, *, seed: bool) -> TestClient:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "test.db",
        seed_samples=seed,
        provider="fake",
    )
    return TestClient(create_app(settings, provider=FakeProvider()))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path, seed=True) as c:
        yield c


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path, seed=False) as c:
        yield c
