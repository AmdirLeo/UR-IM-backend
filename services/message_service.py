import asyncpg
from datetime import datetime, timezone
from schemas.message import SendMessageRequest
from core.exceptions import MessageException, MessageErrors
from db.repositories.message_repo import db_send_message
from db.repositories.conversation_repo import db_check_user_in_conversation

async def send_message_service(
    db_session: asyncpg.Connection,
    user_id: int,
    req: SendMessageRequest
) -> dict:
    """
    发送消息逻辑处理
    """
    # 1. 验证用户是否在会话中
    is_in_conv = await db_check_user_in_conversation(db_session, user_id, req.conversation_id)
    if not is_in_conv:
        raise MessageException(MessageErrors.NotInConversation)

    # 2. 执行数据库事务
    msg_id = await db_send_message(
        db_session,
        user_id,
        req.conversation_id,
        req.message_content,
        req.msg_type
    )

    # 3. 构造返回结构
    return {
        "msg_id": msg_id,
        "server_time": datetime.now(timezone.utc),
        "local_id": req.local_id
    }