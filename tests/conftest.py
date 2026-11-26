import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracemind.checkpoint import to_psycopg_dsn
from tracemind.config import settings
from tracemind.db import Base


@pytest.fixture
async def schema():
    name = f"t_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(settings.database_url)
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{name}"'))
    except Exception as e:
        await admin.dispose()
        pytest.skip(f"Postgres not available: {type(e).__name__}")
    yield name
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
    await admin.dispose()


@pytest.fixture
async def db(schema):
    engine = create_async_engine(
        settings.database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def schema_dsn(schema):
    return f"{to_psycopg_dsn(settings.database_url)}?options=-csearch_path%3D{schema}"