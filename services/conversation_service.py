import asyncpg
from db.repositories.conversation_repo import db_sync_conversations


async def sync_conversations(
    db_session: asyncpg.Connection, user_id: int
) -> list[dict]:
    """同步会话列表及未读信息"""
    conversations = await db_sync_conversations(db_session, user_id)
    return conversations
