# checkpoints live in postgres so a long run resumes after a worker restart.
# resuming = invoking the graph again with the same thread_id:
#   with graph_checkpointer() as saver:
#       graph = build_screening_graph(saver)
#       graph.invoke(state, config=thread_config(tid))

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


def _psycopg_connection_string() -> str:
    # PostgresSaver talks to psycopg directly, so no "+psycopg" suffix here
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


@contextmanager
def graph_checkpointer() -> Iterator[PostgresSaver]:
    # setup() is idempotent. these tables belong to langgraph, not alembic
    with PostgresSaver.from_conn_string(_psycopg_connection_string()) as saver:
        saver.setup()
        yield saver


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


@contextmanager
def pooled_checkpointer(max_size: int = 4) -> Iterator[PostgresSaver]:
    # thread-safe saver for graphs run in parallel; each step borrows a connection
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    with ConnectionPool(
        _psycopg_connection_string(),
        max_size=max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        yield saver
