import asyncpg
from db.repositories.message_repo import (
    db_sync_conversations,
    db_mark_conversation_as_read,
    db_set_conversation_mute,
    db_set_conversation_pin,
)
from db.repositories.conversation_repo import db_get_direct_conversation
from core.exceptions import BusinessException


async def sync_conversations(db_session: asyncpg.Connection, user_id: int) -> list[dict]:
    """同步会话列表及未读信息"""
    conversations = await db_sync_conversations(db_session, user_id)
    return conversations


async def read_ack(db_session: asyncpg.Connection, user_id: int, conversation_id: int):
    """处理已读回执"""
    # 也可以在这里加上用户是否在会话中的校验，如果是正常流程则不一定需要
    await db_mark_conversation_as_read(db_session, user_id, conversation_id)


async def set_conversation_mute(db_session: asyncpg.Connection, user_id: int, conversation_id: int, is_muted: bool):
    """设置消息免打扰"""
    await db_set_conversation_mute(db_session, user_id, conversation_id, is_muted)


async def set_conversation_pin(db_session: asyncpg.Connection, user_id: int, conversation_id: int, is_pinned: bool):
    """设置会话置顶"""
    await db_set_conversation_pin(db_session, user_id, conversation_id, is_pinned)


async def get_direct_conversation_id(
    db_session: asyncpg.Connection, current_user_id: int, friend_user_id: int
) -> int:

    # 防止自己查自己
    if current_user_id == friend_user_id:
        raise BusinessException(status_code=400, detail="无法与自己建立私聊会话")

    conv_id = await db_get_direct_conversation(db_session, current_user_id, friend_user_id)

    if not conv_id:
        # 如果找不到，说明他们俩根本不是好友，或者会话被意外破坏了
        raise BusinessException(status_code=404, detail="私聊会话不存在")

    return conv_id
