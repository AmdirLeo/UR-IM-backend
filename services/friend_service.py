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
from db.repositories.user_repo import (
    db_get_user_by_id,
)
from core.exceptions import BusinessException
from schemas.message import SendMessageRequest, MessageType
from schemas.user import UserInfoResponse
# 引入发消息服务（请根据你的实际项目结构调整导入路径）
from services.message_service import send_message_service
from services.user_service import get_user_info_service
import asyncpg
import time
import uuid
import json
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
    request_id = await db_create_friend_request(
        db_session,
        sender_id=from_user_id,
        receiver_id=target_user_id,
        message=message or "",  # 确保 message 为字符串，防止 None 导致类型问题
    )
    if not request_id:
        # 理论上外层已做所有检查，此处为兜底
        raise BusinessException(status_code=500, detail="好友申请发送失败")

    # 3. 查找目标用户与系统助手(10000号)的私聊会话 ID
    system_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = 10000
    """
    system_conv_id = await db_session.fetchval(system_conv_query, target_user_id)

    # 4. 如果目标用户有系统会话，则发送通知卡片
    if system_conv_id:

        # 4.1 构造消息请求体
        msg_req = SendMessageRequest(
            conversation_id=system_conv_id,
            local_id=str(uuid.uuid4()),        # 系统生成一个随机本地ID
            # message_content 用作手机弹窗或列表的简短预览
            message_content="[收到一条好友申请]",  # 字典转成 JSON 字符串塞进内容里
            msg_type=MessageType.CARD,           # 贴上我们新定义的包裹标签
            extra_data={                        # 👈 真正的结构化数据全放这里
                "card_type": "friend_apply",
                "request_id": request_id,
                "sender_id": from_user_id,
                "reason": message or "",
                "status": "pending"
            }
        )

        # 4.2 调用消息模块，以 10000 号的身份发信
        await send_message_service(
            db_session=db_session,
            user_id=10000,
            req=msg_req
        )


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
            msg_content = "[好友申请已通过]"
            extra_data = {
                "action": "friend_accept",
                "tips": f"用户 {current_user_id} 已同意你的好友申请，快去打个招呼吧！",
                # 💡 假设你的 db_result 里返回了新建的两人私聊会话ID
                # 如果底层还没写这块逻辑，前端拿到 None 就只给个提示框，不自动跳
                "new_conversation_id": db_result.get("new_conversation_id")
            }
        else:
            msg_content = "[好友申请被拒绝]"
            extra_data = {
                "action": "friend_reject",
                "tips": f"用户 {current_user_id} 拒绝了你的好友申请。"
            }

        system_req = SendMessageRequest(
            conversation_id=system_conv_id,
            local_id=str(uuid.uuid4()),
            message_content=msg_content,
            msg_type=MessageType.NOTIFY,  # 👈 使用系统通知指令类型
            extra_data=extra_data         # 👈 将指令参数丢进附件包
        )

        # 调起我们写好的发消息接口，身份为上帝账号 (10000)
        await send_message_service(
            db_session=db_session,
            user_id=10000,
            req=system_req
        )


async def remove_friend(
    db_session: asyncpg.Connection,
    current_user_id: int,
    friend_user_id: int,
    delete_history: bool = False
) -> None:
    """
    删除好友的业务逻辑。
    - 不能删除自己
    - 检查是否为好友关系（可选，repo 层会返回删除行数，可据此判断）
    """
    # 1. 不能删除自己
    if current_user_id == friend_user_id:
        raise BusinessException(status_code=400, detail="不能删除自己")

    # 2. 核心调度：全权委托给 Repo 层的底层事务处理数据变更
    await db_remove_friend(db_session, current_user_id, friend_user_id, delete_history)

    # ==========================================
    # 3. 找到被删除人的系统助手会话，发一条解绑指令
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
    friend_system_conv_id = await db_session.fetchval(find_system_conv_query, friend_user_id)

    if friend_system_conv_id:
        notify_req = SendMessageRequest(
            conversation_id=friend_system_conv_id,
            local_id=str(uuid.uuid4()),
            message_content="[好友关系解除]",
            msg_type=MessageType.NOTIFY,
            extra_data={
                "action": "friend_deleted",
                "trigger_user_id": current_user_id,  # 告诉前端是谁删了你
                "tips": "对方已解除与你的好友关系，你无法再发送新消息。"
            }
        )
        await send_message_service(db_session, 10000, notify_req)


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
    await db_create_friend_tag(db_session, user_id, tag_name)


async def delete_friend_tag(db_session: asyncpg.Connection, user_id: int, tag_name: str) -> None:
    """
    删除好友分组
    """
    await db_delete_friend_tag(db_session, user_id, tag_name)


async def add_friends_to_tag(
    db_session: asyncpg.Connection, user_id: int, tag_name: str, friend_ids: list[int]
) -> None:
    """
    将好友移入分组
    """
    await db_add_friends_to_tag(db_session, user_id, tag_name, friend_ids)


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
    await db_remove_friend_from_tag(db_session, user_id, friend_user_id, tag_name)


# services/user_service.py

async def get_other_user_info_service(
    conn,
    current_user_id: int,
    target_user_id: int
) -> UserInfoResponse:
    """
    获取其他用户个人信息的业务逻辑
    """
    # 0. 边缘情况处理：如果他查的是自己，可以直接复用之前的逻辑（可选）
    if current_user_id == target_user_id:
        return await get_user_info_service(conn, current_user_id)

    # 1. 去数据库查询目标用户信息
    user = await db_get_user_by_id(conn, target_user_id)

    # 2. 安全校验
    if not user:
        raise BusinessException(status_code=404, detail="目标用户不存在")

    # 3.  进阶权限校验 (未来可以在这里加逻辑)
    # 比如：判断 target_user_id 是否在 current_user_id 的黑名单里？
    # 比如：如果不是好友，是不是只能看基础信息，不能看详细资料？

    # 4. 封装返回类
    # 注意：真实项目中，给别人看的信息通常少于给自己看的信息。
    # 这里暂时复用 UserInfoResponse，后续建议新建一个 TargetUserInfoResponse
    return UserInfoResponse(
        code=200,
        id=user["user_id"],
        username=user["username"],
        avatar_url=user.get("avatar_url"),
        # 如果是隐私要求高的系统，非好友查询时，邮箱可能需要打码处理，如 a***@gmail.com
        email=user["email"]
    )
