import pytest
from httpx import ASGITransport, AsyncClient

from tests.registry import known_ids
from yorath.app import create_app
from yorath.settings import Settings


def pytest_collection_modifyitems(items) -> None:
    """A coverage claim with an unknown identifier is worse than no claim (R9)."""
    bad = sorted(
        {
            f"{item.nodeid}: {claim}"
            for item in items
            for mark in item.iter_markers("covers")
            for claim in mark.args
            if claim not in known_ids()
        }
    )
    if bad:
        raise pytest.UsageError("covers with an unknown identifier:\n" + "\n".join(bad))


@pytest.fixture
def app():
    return create_app(Settings(build_sha="test"))


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
