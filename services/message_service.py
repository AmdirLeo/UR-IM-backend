import asyncpg
from datetime import datetime, timezone
from schemas.message import SendMessageRequest
from core.exceptions import MessageException, MessageErrors
from db.repositories.message_repo import db_send_message, db_get_message_history


async def send_message_service(
    db_session: asyncpg.Connection, user_id: int, req: SendMessageRequest
) -> dict:
    """发送消息逻辑处理"""
    
    msg_id = await db_send_message(
        db_session, user_id, req.conversation_id, req.message_content, req.msg_type
    )

    # 3. 构造返回结构
    return {
        "msg_id": msg_id,
        "server_time": datetime.now(timezone.utc),
        "local_id": req.local_id,
    }


async def get_message_history_service(
    db_session: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
    start_msg_id: int | None,
    limit: int,
) -> list[dict]:
    """获取历史消息记录"""

    # 获取历史记录
    history = await db_get_message_history(
        db_session, user_id, conversation_id, start_msg_id, limit
    )

    return history
