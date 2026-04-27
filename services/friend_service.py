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
    db_get_friend_tags,
    db_get_pending_friend_requests,
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

    # 3. 查找目标用户与系统助手(-1号)的私聊会话 ID
    system_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = -1
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

        # 4.2 调用消息模块，以 -1 号的身份发信
        await send_message_service(
            db_session=db_session,
            user_id=-1,
            req=msg_req
        )


async def handle_friend_request(
        db_session: asyncpg.Connection,
        current_user_id: int,
        request_id: int,
        action: str) -> dict:
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

    # ==========================================
    # 2. 只有在同意申请时，才发送打招呼消息
    # ==========================================
    if action == "accepted":
        # 【核心改动 1】：同意申请后，通知直接下发到两人的【私聊会话】中！
        # 2. 查找对方与系统助手(-1号)的专属会话
        sender_id = db_result["friend_id"]
        find_system_conv_query = """
            SELECT c.conversation_id
            FROM conversation c
            JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
            JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
            WHERE c.type = 'private'
            AND cm1.member_user_id = $1
            AND cm2.member_user_id = -1;
        """
        system_conv_id = await db_session.fetchval(find_system_conv_query, sender_id)

        private_conv_id = db_result.get("conversation_id")

        # 1. 通知申请人 A
        if system_conv_id and private_conv_id:
            accept_req = SendMessageRequest(
                conversation_id=system_conv_id,
                local_id=str(uuid.uuid4()),
                message_content="[好友申请已通过]",  # 前端可以根据这个显示打招呼的灰色小字
                msg_type=MessageType.NOTIFY,
                extra_data={
                    "action": "friend_accept",
                    "tips": f"用户 {current_user_id} 已同意你的好友申请，快去打个招呼吧！",
                    "conversation_id": private_conv_id
                }
            )
            await send_message_service(db_session, -1, accept_req)

        # 2. B 在新会话中发送欢迎消息
        if private_conv_id:
            welcome_req = SendMessageRequest(
                conversation_id=private_conv_id,
                local_id=str(uuid.uuid4()),
                message_content="我们已经是好友啦，一起聊天吧！",
                msg_type=MessageType.TEXT,
                extra_data={}
            )
            await send_message_service(db_session, current_user_id, welcome_req)

    # 👈 修改点 3：把 Repo 层返回的字典，原封不动地返回给上一层的 API 路由
    return db_result


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
    direct_conv_id = await db_remove_friend(db_session, current_user_id, friend_user_id, delete_history)

    # ==========================================
    # 3. 发一条解绑指令
    # ==========================================
    if direct_conv_id:
        notify_req = SendMessageRequest(
            conversation_id=direct_conv_id,
            local_id=str(uuid.uuid4()),
            message_content="[好友关系解除]",
            msg_type=MessageType.NOTIFY,
            extra_data={
                "action": "friend_deleted",
                "trigger_user_id": current_user_id,  # 告诉前端是谁删了你
                "tips": "对方已解除与你的好友关系，你无法再发送新消息。"
            }
        )
        # 关键点：用系统账号 (-1) 的身份调用服务层发消息！
        # 因为此时 current_user_id 已经退群 (is_active=false)，
        # 如果用 current_user_id 去发，会被你自己将要写的鉴权拦截器无情拦截。
        await send_message_service(db_session, -1, notify_req)


async def get_friend_list(
        db_session: asyncpg.Connection,
        current_user_id: int) -> List[Dict]:
    """
    获取当前用户的好友列表。
    """
    # 调用 repo 层已实现的好友列表查询
    friends = await db_get_friend_list(db_session, current_user_id)
    return friends


async def create_friend_tag(
        db_session: asyncpg.Connection,
        user_id: int,
        tag_name: str) -> None:
    """
    新建好友分组
    """
    await db_create_friend_tag(db_session, user_id, tag_name)


async def delete_friend_tag(
        db_session: asyncpg.Connection,
        user_id: int,
        tag_name: str) -> None:
    """
    删除好友分组
    """
    await db_delete_friend_tag(db_session, user_id, tag_name)


async def add_friends_to_tag(
        db_session: asyncpg.Connection,
        user_id: int,
        tag_name: str,
        friend_ids: list[int]) -> None:
    """
    将好友移入分组
    """
    await db_add_friends_to_tag(db_session, user_id, tag_name, friend_ids)


async def get_friends_by_tag(
        db_session: asyncpg.Connection,
        user_id: int,
        tag_name: str) -> list[dict]:
    """
    获取某分组下的所有好友
    """
    return await db_get_friends_by_tag(db_session, user_id, tag_name)


async def remove_friend_from_tag(
        db_session: asyncpg.Connection,
        user_id: int,
        friend_user_id: int,
        tag_name: str) -> None:
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


async def get_friend_tag_list(
        db_session: asyncpg.Connection,
        current_user_id: int) -> list[str]:
    """
    获取当前用户的所有好友分组标签列表。
    """
    # 调用之前定义的数据库层函数
    tags = await db_get_friend_tags(db_session, current_user_id)
    return tags


async def get_pending_friend_requests_as_cards(
    db_session: asyncpg.Connection,
    current_user_id: int
) -> list[dict]:
    """
    获取待处理好友申请，并转换为卡片格式。
    """
    raw_requests = await db_get_pending_friend_requests(db_session, current_user_id)

    cards = []
    for req in raw_requests:
        cards.append({
            "card_type": "friend_apply",
            "request_id": req["request_id"],
            "sender_id": req["sender_id"],
            "sender_name": req["sender_name"],
            "sender_avatar": req["sender_avatar"],
            "reason": req["message"] or "",
            "status": "pending",
            "create_time": int(req["create_time"].timestamp())  # 转为 Unix 时间戳
        })
    return cards
