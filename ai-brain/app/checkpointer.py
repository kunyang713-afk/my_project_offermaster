from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, Connection

from .config import settings

_checkpointer = None
_async_checkpointer = None


def get_checkpointer() -> PostgresSaver:
    """同步 checkpointer（阻塞路径 start/answer 使用）。"""
    global _checkpointer
    if _checkpointer is None:
        conn = Connection.connect(settings.database_url, autocommit=True)
        _checkpointer = PostgresSaver(conn)
        _checkpointer.setup()
    return _checkpointer


async def get_async_checkpointer() -> AsyncPostgresSaver:
    """异步 checkpointer（SSE 流式路径使用）。

    同步 PostgresSaver 不实现 async 接口（aget_tuple 抛 NotImplementedError），
    流式所需的 astream_events / aget_state 必须搭配 AsyncPostgresSaver。
    与同步 checkpointer 共享同一组 Postgres checkpoint 表，thread_id 状态互通。
    """
    global _async_checkpointer
    if _async_checkpointer is None:
        aconn = await AsyncConnection.connect(settings.database_url, autocommit=True)
        _async_checkpointer = AsyncPostgresSaver(aconn)
        await _async_checkpointer.setup()
    return _async_checkpointer
