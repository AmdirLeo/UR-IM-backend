import asyncpg
from datetime import datetime, timezone
from schemas.message import (
    SendMessageRequest,
    MessageSearchRequest,
    DeleteMessageRequest,
)
from core.exceptions import MessageException, MessageErrors
from db.repositories.message_repo import (
    db_send_message,
    db_quote_message,
    db_get_message_history,
    db_filter_messages,
    db_delete_local_messages,
)


async def send_message_service(db_session: asyncpg.Connection, user_id: int, req: SendMessageRequest) -> dict:
    """发送消息逻辑处理"""

    if req.quote_message_id is not None:
        # 把返回值存在一个中间变量 db_result 里
        db_result = await db_quote_message(
            db_session, user_id, req.conversation_id, req.message_content, req.msg_type, req.quote_message_id
        )
    else:
        db_result = await db_send_message(db_session, user_id, req.conversation_id, req.message_content, req.msg_type)

    # 从字典中提取出真正的 msg_id
    real_msg_id = db_result["msg_id"]

    # 3. 构造返回结构
    return {
        "msg_id": real_msg_id,  # 这里填入提取出来的整数
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
    history = await db_get_message_history(db_session, user_id, conversation_id, start_msg_id, limit)

    return history


async def search_message_service(
    db_session: asyncpg.Connection,
    current_user_id: int,
    req: MessageSearchRequest,
) -> list[dict]:
    """筛选历史消息记录"""

    if req.start_time is None and req.end_time is None and req.keyword is None:
        raise MessageException(MessageErrors.InvalidRequest, "start_time, end_time, keyword 不能全为空")

    if req.conversation_id is None:
        raise MessageException(MessageErrors.InvalidRequest, "conversation_id 不能为空")

    history = await db_filter_messages(
        conn=db_session,
        user_id=current_user_id,
        conversation_id=req.conversation_id,
        keyword=req.keyword,
        sender_id=req.user_id,
        start_time=req.start_time,
        end_time=req.end_time,
        cursor_msg_id=req.offset,
        limit=req.limit,
    )

    result = []
    for msg in history:
        result.append(
            {
                "user_id": msg.get("sender_id", 0),
                "conversation_id": req.conversation_id,
                "msg_id": msg.get("msg_id", 0),
                "msg": msg.get("msg_content", ""),
                "time": msg.get("create_time", datetime.now(timezone.utc)),
            }
        )

    return result


async def delete_message_service(
    db_session: asyncpg.Connection,
    current_user_id: int,
    req: DeleteMessageRequest,
) -> None:
    """删除消息记录"""
    await db_delete_local_messages(
        db_session,
        user_id=current_user_id,
        conversation_id=req.conversation_id,
        msg_ids=[req.message_id],
    )
