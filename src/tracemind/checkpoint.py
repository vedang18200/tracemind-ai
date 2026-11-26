from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from tracemind.config import settings


def to_psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def open_checkpointer(dsn: str | None = None):
    async with AsyncPostgresSaver.from_conn_string(dsn or to_psycopg_dsn(settings.database_url)) as saver:
        await saver.setup()          # creates LangGraph's checkpoint tables if missing
        yield saver