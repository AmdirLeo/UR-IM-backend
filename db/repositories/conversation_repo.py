import asyncpg
from typing import Optional


async def db_get_direct_conversation(
    conn: asyncpg.Connection, user_id: int, friend_user_id: int
) -> Optional[int]:
    """
    查询两个用户之间的私聊会话 ID
    """
    find_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = $2;
    """
    # fetchval 会直接返回第一行第一列的值，如果没有找到则返回 None
    return await conn.fetchval(find_conv_query, user_id, friend_user_id)
