import asyncpg
from db.repositories.conversation_repo import (
    db_sync_conversations,
    db_mark_conversation_as_read,
)


async def sync_conversations(
    db_session: asyncpg.Connection, user_id: int
) -> list[dict]:
    """同步会话列表及未读信息"""
    conversations = await db_sync_conversations(db_session, user_id)
    return conversations


async def read_ack(
    db_session: asyncpg.Connection, user_id: int, conversation_id: int, msg_id: int
):
    """处理已读回执"""
    # 也可以在这里加上用户是否在会话中的校验，如果是正常流程则不一定需要
    await db_mark_conversation_as_read(db_session, user_id, conversation_id, msg_id)
