"""Groups: the whole core on a live Postgres, stubs only at the boundary.

The ``<name>_test`` database on the YORATH_DATABASE_URL server is created on the first run; the
running stack's data is not touched. The schema goes through migrations once per session (down and
up), tables are cleared before each test. No database means a failure, not a skip (R3).
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text

from tests.fixtures.stubs import Backends
from yorath.app import create_app
from yorath.control.journal import Journal
from yorath.db import make_engine, make_sessions
from yorath.enforcement import gateway
from yorath.enforcement.secrets import DbSecretStore
from yorath.runtime.rag.embeddings import OllamaEmbeddings
from yorath.runtime.rag.indexer import Indexer
from yorath.settings import Settings

CORE_DIR = Path(__file__).resolve().parents[2]
REFERENCE_TABLES = {"alembic_version", "clusters", "runtime_types"}  # filled by migrations


def pytest_collection_modifyitems(items) -> None:
    """A group module without the marker silently drops out of ``-m group``: a collection error (R3, R6)."""
    unmarked = sorted(
        {
            str(item.path.name)
            for item in items
            if "groups" in item.path.parts and item.get_closest_marker("group") is None
        }
    )
    if unmarked:
        raise pytest.UsageError(f"group modules without the group marker: {', '.join(unmarked)}")


def _ensure_test_database(url: str) -> str:
    """URL of the ``<db>_test`` test database; creates it if missing."""
    base = make_url(url)
    test_name = f"{base.database}_test"
    admin_url = base.set(database="postgres")

    async def create() -> None:
        admin = admin_url.render_as_string(hide_password=False)
        engine = make_engine(admin).execution_options(isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                exists = await conn.scalar(
                    text("select 1 from pg_database where datname = :n"), {"n": test_name}
                )
                if not exists:
                    await conn.execute(text(f'create database "{test_name}"'))
        finally:
            await engine.dispose()

    asyncio.run(create())
    return base.set(database=test_name).render_as_string(hide_password=False)


def _alembic(url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=CORE_DIR,
        check=True,
        capture_output=True,
        env={**os.environ, "YORATH_DATABASE_URL": url},
    )


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("YORATH_DATABASE_URL")
    if not url:
        pytest.fail(
            "YORATH_DATABASE_URL is not set: groups need Postgres (make test)", pytrace=False
        )
    try:
        test_url = _ensure_test_database(url)
    except OSError as exc:
        pytest.fail(f"the YORATH_DATABASE_URL database is unavailable: {exc}", pytrace=False)
    _alembic(test_url, "downgrade", "base")
    _alembic(test_url, "upgrade", "head")
    return test_url


@pytest.fixture(autouse=True)
async def clean_tables(database_url):
    """Test independence: all tables except reference ones. TRUNCATE bypasses the audit trigger."""
    engine = make_engine(database_url)
    try:
        async with engine.begin() as conn:
            rows = await conn.execute(
                text("select tablename from pg_tables where schemaname = 'public'")
            )
            tables = sorted({r[0] for r in rows} - REFERENCE_TABLES)
            await conn.execute(text(f"truncate {', '.join(tables)} cascade"))
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(database_url):
    engine = make_engine(database_url)
    try:
        yield make_sessions(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(sessions):
    async with sessions() as s:
        yield s


@pytest.fixture
def backends():
    """Third-party stub; a request to an unknown route fails the test (R12)."""
    stub = Backends()
    yield stub
    assert stub.unexpected == [], f"requests that missed the stub: {stub.unexpected}"


@pytest.fixture
def make_app(sessions, backends):
    """App on a live database; lifespan does not run, state is set directly."""

    def build(**overrides):
        settings = Settings(build_sha="test", session_ttl_hours=1, **overrides)
        app = create_app(settings)
        # fake embeddings are a bag of words with low cosine, so the relevance threshold is lowered
        manifest = app.state.manifest
        app.state.manifest = manifest.model_copy(
            update={
                "rag": manifest.rag.model_copy(update={"min_similarity": 0.2}),
                "gateway": manifest.gateway.model_copy(update={"retry_delay_seconds": 0}),
            },
            deep=True,
        )
        app.state.sessions = sessions
        app.state.journal = Journal(sessions)
        app.state.secrets = DbSecretStore(sessions, settings.secret_key)
        app.state.http = backends.client()
        app.state.indexer = Indexer(
            sessions,
            lambda model: OllamaEmbeddings(app.state.http, settings.ollama_url, model),
            app.state.manifest.rag,
        )
        gateway.install(app)
        return app

    return build


@pytest.fixture
async def api(make_app):
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
