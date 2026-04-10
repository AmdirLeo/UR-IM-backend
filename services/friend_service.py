from typing import Optional, List, Dict
from db.repositories.friend_repo import (
    db_create_friend_request,
    db_handle_friend_request,
    db_remove_friend,
    db_get_friend_list,
    db_create_friend_tag,
    db_delete_friend_tag,
    db_add_friends_to_tag,
    db_get_friends_by_tag,
    db_remove_friend_from_tag,
)
from core.exceptions import BusinessException
from schemas.message import SendMessageRequest
# 引入发消息服务（请根据你的实际项目结构调整导入路径）
from services.message_service import send_message_service
import asyncpg
import time
import uuid
from core.ws_manager import manager  # 1. 引入同事写的邮局


async def apply_friend(
    db_session: asyncpg.Connection,
    from_user_id: int,
    target_user_id: int,
    message: Optional[str] = None,
) -> None:
    """
    处理好友申请的业务逻辑。
    """
    # 1. 不能添加自己为好友
    if from_user_id == target_user_id:
        raise BusinessException(status_code=400, detail="不能添加自己为好友")

    # 2. 创建申请记录（调用 repo 层提供的函数）
    success = await db_create_friend_request(
        db_session,
        sender_id=from_user_id,
        receiver_id=target_user_id,
        message=message or "",  # 确保 message 为字符串，防止 None 导致类型问题
    )
    if not success:
        # 理论上外层已做所有检查，此处为兜底
        raise BusinessException(status_code=500, detail="好友申请发送失败")

    # 3. 可选：通过 WebSocket 实时通知目标用户（暂未实现）
    notification = {
        "type": "FRIEND_REQUEST_RECEIVED",  # 这是一个独特的类型标识
        "data": {
            "from_user_id": from_user_id,
            "message": message or "请求添加你为好友",
            "timestamp": int(time.time())  # 传个时间戳，方便前端排序或显示
        }
    }

    # 调用同事写的 manager，把通知发给 target_user_id
    # 注意：如果对方不在线，manager.send_personal_message 内部会自动处理（不会报错）
    await manager.send_personal_message(notification, target_user_id)


async def handle_friend_request(
    db_session: asyncpg.Connection, current_user_id: int, request_id: int, action: str
) -> None:
    """
    处理好友申请的业务逻辑，并附带发送系统通知。
    """

    # 调用 repo 层的事务函数执行更新（同意或拒绝）
    db_result = await db_handle_friend_request(
        conn=db_session,
        request_id=request_id,
        current_user_id=current_user_id,
        action=action,
    )
    if not db_result:
        # 如果失败（例如申请状态已变更或不存在），抛出异常
        raise BusinessException(status_code=400, detail="处理失败，请稍后重试")

    # 提取最初发起好友申请的人的 ID
    sender_id = db_result["friend_id"]

    # ==========================================
    # 2. 查找对方与系统助手(10000号)的专属会话
    # ==========================================
    find_system_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = 10000;
    """
    system_conv_id = await db_session.fetchval(find_system_conv_query, sender_id)

    # ==========================================
    # 3. 如果找到了系统会话，立刻推送 WebSocket 通知！
    # ==========================================
    if system_conv_id:
        # 根据用户的操作，定制不同的系统提示语
        if action == "accepted":
            msg_content = f"好消息！用户ID: {current_user_id} 已同意你的好友申请，快去打个招呼吧！"
        else:
            msg_content = f"很遗憾，用户ID: {current_user_id} 拒绝了你的好友申请。"

        system_req = SendMessageRequest(
            conversation_id=system_conv_id,
            message_content=msg_content,
            msg_type="text",
            local_id=str(uuid.uuid4())  # 后端随机生成一个临时包 ID 即可
        )

        # 调起我们写好的发消息接口，身份为上帝账号 (10000)
        await send_message_service(
            db_session=db_session,
            user_id=10000,
            req=system_req
        )


async def remove_friend(db_session: asyncpg.Connection, current_user_id: int, friend_user_id: int) -> None:
    """
    删除好友的业务逻辑。
    - 不能删除自己
    - 检查是否为好友关系（可选，repo 层会返回删除行数，可据此判断）
    """
    # 1. 不能删除自己
    if current_user_id == friend_user_id:
        raise BusinessException(status_code=400, detail="不能删除自己")

    # 2. 调用 repo 层删除好友（同时删除双向记录）
    await db_remove_friend(db_session, current_user_id, friend_user_id)


async def get_friend_list(db_session: asyncpg.Connection, current_user_id: int) -> List[Dict]:
    """
    获取当前用户的好友列表。
    """
    # 调用 repo 层已实现的好友列表查询
    friends = await db_get_friend_list(db_session, current_user_id)
    return friends


async def create_friend_tag(db_session: asyncpg.Connection, user_id: int, tag_name: str) -> None:
    """
    新建好友分组
    """
    try:
        await db_create_friend_tag(db_session, user_id, tag_name)
    except Exception as e:
        # Assuming the database exception string contains some clue, or we rely on the specific exception class
        # According to the prompt: 409-该分组已存在
        raise BusinessException(status_code=409, detail="该分组已存在") from e


async def delete_friend_tag(db_session: asyncpg.Connection, user_id: int, tag_name: str) -> None:
    """
    删除好友分组
    """
    try:
        await db_delete_friend_tag(db_session, user_id, tag_name)
    except Exception as e:
        # According to the prompt: 404-分组不存在
        raise BusinessException(status_code=404, detail="分组不存在") from e


async def add_friends_to_tag(
    db_session: asyncpg.Connection, user_id: int, tag_name: str, friend_ids: list[int]
) -> None:
    """
    将好友移入分组
    """
    try:
        await db_add_friends_to_tag(db_session, user_id, tag_name, friend_ids)
    except Exception as e:
        raise BusinessException(status_code=404, detail="分组不存在") from e


async def get_friends_by_tag(db_session: asyncpg.Connection, user_id: int, tag_name: str) -> list[dict]:
    """
    获取某分组下的所有好友
    """
    return await db_get_friends_by_tag(db_session, user_id, tag_name)


async def remove_friend_from_tag(
    db_session: asyncpg.Connection, user_id: int, friend_user_id: int, tag_name: str
) -> None:
    """
    将特定好友移出分组
    """
    try:
        await db_remove_friend_from_tag(db_session, user_id, friend_user_id, tag_name)
    except Exception as e:
        raise BusinessException(status_code=404, detail="该好友不在当前分组中") from e
