import asyncpg
import json
from datetime import datetime, timezone
from core.ws_manager import manager  # 引入 WebSocket 邮局
from schemas.message import (
    SendMessageRequest,
    MessageSearchRequest,
    DeleteMessageRequest,
    MessageType,
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
    """发送消息逻辑处理(多态 JSONB 版)"""
    # ==========================================
    # 1. 新增：组装统一的 JSONB 载荷
    # ==========================================
    msg_body_dict = {
        "type": req.msg_type.value,
        "content": req.message_content,
        "extra": req.extra_data or {}
    }
    # 💡 核心修复：转成字符串，这样底层的 text 或 varchar 字段就能存下了
    packed_content = json.dumps(msg_body_dict, ensure_ascii=False)

    if req.quote_message_id is not None:
        db_result = await db_quote_message(
            db_session, user_id, req.conversation_id, packed_content, req.msg_type.value, req.quote_message_id
        )
    else:
        db_result = await db_send_message(
            db_session, user_id, req.conversation_id, packed_content, req.msg_type.value
        )

    # 从字典中提取出真正的 msg_id
    real_msg_id = db_result["msg_id"] if isinstance(db_result, dict) else db_result
    # 提前获取一下服务器时间，因为推送和返回都要用到
    server_time = datetime.now(timezone.utc)

    # 去数据库查一下，这个 conversation_id 里面到底有哪几个人的 ID
    members_query = "SELECT member_user_id FROM conversation_member WHERE conversation_id = $1"
    members = await db_session.fetch(members_query, req.conversation_id)

    # 4. 构造 WebSocket 通知载荷 (把 extra 也带上)
    ws_notification = {
        "type": "NEW_CHAT_MESSAGE",
        "data": {
            "conversation_id": req.conversation_id,
            "msg_id": real_msg_id,
            "sender_id": user_id,
            "msg_type": req.msg_type.value,
            "content": req.message_content,
            "extra": req.extra_data or {},  # 👈 前端靠这个字段渲染卡片或执行指令
            "create_time": server_time.isoformat(),
            "quote_message_id": req.quote_message_id
        }
    }

    for record in members:
        target_user_id = record["member_user_id"]
        # manager 会自动判断这个人当前在不在线，在线就秒推，离线就静默丢弃
        await manager.send_personal_message(ws_notification, target_user_id)

    # 3. 构造返回结构
    return {
        "msg_id": real_msg_id,  # 这里填入提取出来的整数
        "server_time": server_time,
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
        raise MessageException(MessageErrors.InvalidRequest,
                               "start_time, end_time, keyword 不能全为空")

    if req.conversation_id is None:
        raise MessageException(
            MessageErrors.InvalidRequest, "conversation_id 不能为空")

    history = await db_filter_messages(
        conn=db_session,
        user_id=current_user_id,
        conversation_id=req.conversation_id,
        filters=req,
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
