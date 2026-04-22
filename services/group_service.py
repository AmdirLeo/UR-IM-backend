import asyncpg
import uuid
from datetime import datetime, timezone
from schemas.group import (
    GroupCreateRequest,
    GroupGenericRequest,
    GroupMembersRequest,
    GroupAdminRequest,
    GroupRemoveMemberRequest,
    GroupAnnouncementRequest,
    GroupInviteRequest,
    GroupInviteReviewRequest,
)
from schemas.message import MessageType
from core.exceptions import GroupException, GroupErrors
from db.repositories.group_repo import (
    db_create_group,
    db_get_group_info,
    db_manage_group_role,
    db_remove_group_member,
    db_quit_group,
    db_disband_group,
    db_post_group_announcement,
    db_invite_to_group,
    db_review_group_invite,
    db_get_group_admins,
)
from schemas.message import SendMessageRequest
from services.message_service import send_message_service


async def create_group_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupCreateRequest
) -> dict:
    if not req.user_ids:
        raise GroupException(GroupErrors.InvalidRequest, "好友列表不能为空")
    # 假设如果被邀请的人不存在或者其他问题，在底层的 db 层（没有提及详细错误，但通常由外键抛出或忽略）处理
    conv_id = await db_create_group(
        conn=db_session,
        creator_id=current_user_id,
        member_ids=req.user_ids,
        avatar_url=req.avatar,
        group_name=req.name,
    )
    return {"conversation_id": conv_id, "name": req.name, "avatar": req.avatar}


async def get_group_info_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupGenericRequest
) -> dict:
    # 调用底层接口，如果不在群里抛出 GroupErrors.NotInGroup
    info = await db_get_group_info(db_session, current_user_id, req.conversation_id)
    # 从 info["members"] 中提取 top_members, member_count, owner_id 和 my_role
    members = info.get("members", [])
    member_count = len(members)
    owner_id = 0
    my_role = ""
    top_members = []

    for i, m in enumerate(members):
        if m.get("role") == "owner":
            owner_id = m.get("member_user_id")
        if m.get("member_user_id") == current_user_id:
            my_role = m.get("role")
        if i < 9:
            top_members.append(
                {
                    "user_id": m.get("member_user_id"),
                    "user_name": m.get("username"),
                    "avatar_url": m.get("avatar_url"),
                    "role": m.get("role"),
                }
            )

    # 从 announcements 中提取最新的一条
    announcements = info.get("announcements", [])
    latest_announcement = None
    if announcements:
        first_ann = announcements[0]

        create_time_dt = first_ann.get("create_time")
        create_time_int = int(create_time_dt.timestamp()
                              ) if create_time_dt else 0

        latest_announcement = {
            "announcement_id": first_ann.get("announcement_id"),
            "content": first_ann.get("content"),
            "create_time": create_time_int,  # <--- 填入转换后的 int 变量
            "sender_name": first_ann.get("sender_name"),
        }

    return {
        "conversation_id": info.get("conversation_id"),
        "conversation_name": info.get("conversation_name"),
        "conversation_avatar": None,  # db目前没有存储头像，所以默认为None返回
        "member_count": member_count,
        "owner_id": owner_id,
        "my_role": my_role,
        "latest_announcement": latest_announcement,
        "top_members": top_members,
    }


async def get_group_members_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupMembersRequest
) -> dict:
    # 复用 get_group_info 来获取成员列表
    info = await db_get_group_info(db_session, current_user_id, req.conversation_id)
    members = info.get("members", [])
    total = len(members)
    # 分页切片
    start_idx = (req.page - 1) * req.page_size
    end_idx = req.page * req.page_size
    paged_members = members[start_idx:end_idx]
    result_list = []

    for m in paged_members:
        result_list.append(
            {
                "user_id": m.get("member_user_id"),
                "user_name": m.get("username"),
                "avatar_url": m.get("avatar_url"),
                "role": m.get("role"),
            }
        )

    return {
        "total": total,
        "page": req.page,
        "page_size": req.page_size,
        "list": result_list,
    }


async def manage_group_admin_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupAdminRequest
) -> None:
    if current_user_id == req.user_id:
        raise GroupException(GroupErrors.PermissionDenied, "不能操作自己")
    await db_manage_group_role(
        conn=db_session,
        operator_id=current_user_id,
        conversation_id=req.conversation_id,
        target_user_id=req.user_id,
        new_role=req.role,
    )


async def remove_group_member_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupRemoveMemberRequest
) -> None:
    await db_remove_group_member(
        conn=db_session,
        operator_id=current_user_id,
        conversation_id=req.conversation_id,
        target_user_id=req.user_id,
    )


async def quit_group_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupGenericRequest
) -> None:
    await db_quit_group(
        conn=db_session, user_id=current_user_id, conversation_id=req.conversation_id
    )


async def disband_group_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupGenericRequest
) -> None:
    await db_disband_group(
        conn=db_session, user_id=current_user_id, conversation_id=req.conversation_id
    )


async def post_group_announcement_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupAnnouncementRequest
) -> dict:
    # 插入公告到数据库
    announcement_id = await db_post_group_announcement(
        conn=db_session,
        operator_id=current_user_id,
        conversation_id=req.conversation_id,
        content=req.msg,
    )
    # 构造发消息请求，将公告发到群里
    msg_req = SendMessageRequest(
        conversation_id=req.conversation_id,
        local_id=str(uuid.uuid4()),
        message_content=f"[群公告] {req.msg}",
        msg_type="text",
        quote_message_id=None,
    )
    # 调用 message 服务
    send_res = await send_message_service(db_session, current_user_id, msg_req)
    server_time = send_res.get("server_time") or datetime.now(timezone.utc)
    return {
        "time": server_time.isoformat(),
        "announcement_id": announcement_id,
    }


async def invite_to_group_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupInviteRequest
) -> dict:
    # 1. 创建入群申请记录（邀请制）
    invite_id = await db_invite_to_group(
        conn=db_session,
        inviter_id=current_user_id,
        conversation_id=req.conversation_id,
        invitee_id=req.user_id,
    )

    # 2. 获取邀请人姓名（用于消息展示）
    inviter_name = await db_session.fetchval(
        "SELECT username FROM user_account WHERE user_id = $1", current_user_id
    )

    # 3. 获取该群所有管理员和群主的 user_id（即有审核权限的人）
    admin_ids = await db_get_group_admins(db_session, req.conversation_id)

    # 4. 为每个管理员发送私聊通知
    for admin_id in admin_ids:

        # 4.1 查找该管理员与 -2 号助手的现有私聊会话
        conv_id = await db_session.fetchval("""
            SELECT c.conversation_id
            FROM conversation c
            JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
            JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
            WHERE c.type = 'private'
              AND cm1.member_user_id = $1
              AND cm2.member_user_id = -2
        """, admin_id)

        if conv_id is None:
            raise RuntimeError(
                f"数据完整性错误：管理员 {admin_id} 缺少与群聊助手(-2)的私聊会话，"
                "请检查注册流程是否正确创建了该会话。"
            )

        # 4.2 构造卡片消息
        msg_req = SendMessageRequest(
            conversation_id=conv_id,
            local_id=str(uuid.uuid4()),
            message_content="[收到一条入群申请]",
            msg_type=MessageType.CARD,
            extra_data={
                "card_type": "group_apply",
                "apply_id": invite_id,
                "applicant_id": req.user_id,
                "inviter_id": current_user_id,
                "inviter_name": inviter_name,
                "conversation_id": req.conversation_id,
                "status": "pending"
            }
        )

        # 4.3 以 -2 身份发送消息
        await send_message_service(
            db_session=db_session,
            user_id=-2,
            req=msg_req
        )
    return {"apply_id": invite_id}


async def notify_other_admins_invite_approved(
    conn: asyncpg.Connection,
    invite_id: int,
    handled_by_user_id: int,
) -> None:
    """
    当某个管理员通过了群邀请后，向群内其他在线管理员发送系统通知，
    提示前端刷新待审批数量。
    消息通过系统助手(-2)的私聊会话发送。
    """
    # 1. 查询邀请对应的群聊ID和邀请人ID
    invite_info = await conn.fetchrow(
        """
        SELECT conversation_id, inviter_id
        FROM group_invite
        WHERE invite_id = $1
        """,
        invite_id,
    )
    if not invite_info:
        return  # 邀请不存在，直接忽略

    conversation_id = invite_info["conversation_id"]

    # 2. 查询群内所有其他管理员的 user_id（排除当前处理者）
    other_admins = await conn.fetch(
        """
        SELECT member_user_id
        FROM conversation_member
        WHERE conversation_id = $1
          AND role IN ('owner', 'admin')
          AND member_user_id != $2
          AND is_active = true
        """,
        conversation_id,
        handled_by_user_id,
    )
    if not other_admins:
        return

    # 3. 为每个管理员找到与系统助手(-2)的私聊会话
    find_system_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = -2
    """

    tip_text = f"群聊 {conversation_id} 的入群申请已被其他管理员批准，请刷新待审批列表。"
    extra_action = "group_invite_approved_by_other"

    # 4. 循环发送系统消息
    for admin_record in other_admins:
        admin_id = admin_record["member_user_id"]
        # 获取该管理员与系统助手的私聊会话ID
        system_conv_id = await conn.fetchval(find_system_conv_query, admin_id)
        if not system_conv_id:
            continue   # 跳过没有会话的管理员

        # 构造系统消息请求
        send_req = SendMessageRequest(
            conversation_id=system_conv_id,
            local_id=str(uuid.uuid4()),
            message_content="[入群申请被其他管理员批准]",
            msg_type=MessageType.NOTIFY,
            extra_data={
                "action": extra_action,
                "tips": tip_text,
                "invite_id": invite_id,
                "conversation_id": conversation_id,
                "handled_by": handled_by_user_id,
            }
        )
        # 发送消息（假设 send_message_service 已存在）
        try:
            await send_message_service(conn, -2, send_req)
        except Exception as e:
            # 记录日志，不中断主流程
            print(f"发送通知给管理员 {admin_id} 失败: {e}")


async def send_group_invite_approved_notification(
    conn: asyncpg.Connection,
    invite_id: int,
) -> None:
    """
    在群聊中发送系统通知：“邀请人 把 被邀请人 拉入了群聊”
    使用系统助手 -2 发送 NOTIFY 类型消息。
    """
    # 1. 查询邀请详情（群ID、邀请人、被邀请人）
    invite_info = await conn.fetchrow(
        """
        SELECT conversation_id, inviter_id, invitee_id
        FROM group_invite
        WHERE invite_id = $1
        """,
        invite_id,
    )
    if not invite_info:
        # 邀请不存在，静默返回
        return

    conversation_id = invite_info["conversation_id"]
    inviter_id = invite_info["inviter_id"]
    invitee_id = invite_info["invitee_id"]

    # 2. 获取邀请人和被邀请人的显示名称（优先昵称，否则用 user_id）
    async def get_user_display_name(user_id: int) -> str:
        row = await conn.fetchrow(
            "SELECT username FROM user_account WHERE user_id = $1",
            user_id,
        )
        if row:
            # 假设有 nickname 字段，否则用 username，最后用 user_id
            return row.get("username") or str(user_id)
        return str(user_id)

    inviter_name = await get_user_display_name(inviter_id)
    invitee_name = await get_user_display_name(invitee_id)

    # 3. 构造消息文本
    message_text = f"{inviter_name} 把 {invitee_name} 拉入了群聊"

    # 4. 构造 NOTIFY 消息请求
    send_req = SendMessageRequest(
        conversation_id=conversation_id,          # 在群聊中发送
        local_id=str(uuid.uuid4()),
        message_content=message_text,
        msg_type=MessageType.NOTIFY,
        extra_data={
            "action": "group_member_invited",
            "inviter_id": inviter_id,
            "invitee_id": invitee_id,
            "invite_id": invite_id,
        }
    )

    # 5. 调用已有的消息发送服务（发送者固定为系统助手 -2）
    try:
        await send_message_service(conn, -2, send_req)
    except Exception as e:
        # 记录日志但不影响主流程
        print(f"发送群邀请通知失败: {e}")


async def notify_invitee_approved(
    conn: asyncpg.Connection,
    invite_id: int,
) -> None:
    """
    向被邀请人发送系统通知：入群申请已被批准。
    消息通过系统助手(-2)的私聊会话发送，extra_data 中携带群聊ID，
    前端收到后可准备打开该群聊的会话窗口。
    """
    # 1. 查询邀请详情（被邀请人ID、群聊ID）
    invite_info = await conn.fetchrow(
        """
        SELECT conversation_id, invitee_id
        FROM group_invite
        WHERE invite_id = $1
        """,
        invite_id,
    )
    if not invite_info:
        return

    conversation_id = invite_info["conversation_id"]
    invitee_id = invite_info["invitee_id"]

    # 2. 查询被邀请人与系统助手(-2)的私聊会话ID
    find_system_conv_query = """
        SELECT c.conversation_id
        FROM conversation c
        JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
        JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
        WHERE c.type = 'private'
          AND cm1.member_user_id = $1
          AND cm2.member_user_id = -2
    """
    system_conv_id = await conn.fetchval(find_system_conv_query, invitee_id)
    if not system_conv_id:
        # 如果没有与系统助手的私聊会话，可以跳过（或尝试创建）
        return

    # 3. 构造通知消息
    tip_text = f"你已被批准加入群聊 {conversation_id}，请刷新会话列表。"
    send_req = SendMessageRequest(
        conversation_id=system_conv_id,
        local_id=str(uuid.uuid4()),
        message_content="[入群申请已通过]",
        msg_type=MessageType.NOTIFY,
        extra_data={
            "action": "group_invite_approved_for_invitee",
            "tips": tip_text,
            "conversation_id": conversation_id,   # 前端可用此ID打开群聊窗口
            "invite_id": invite_id,
        }
    )

    try:
        await send_message_service(conn, -2, send_req)
    except Exception as e:
        print(f"发送通知给被邀请人 {invitee_id} 失败: {e}")


async def review_group_invite_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupInviteReviewRequest
) -> None:
    action = "approved" if req.status == "APPROVED" else "ignored"
    # 执行审核（若通过，会将被邀请人加入群成员）
    await db_review_group_invite(
        conn=db_session,
        reviewer_id=current_user_id,
        invite_id=req.apply_id,
        action=action,
    )
    if action == "approved":
        await notify_other_admins_invite_approved(
            conn=db_session,
            invite_id=req.apply_id,
            handled_by_user_id=current_user_id,
        )

        await send_group_invite_approved_notification(
            conn=db_session,
            invite_id=req.apply_id,
        )
        # 通知被邀请人：申请已通过
        await notify_invitee_approved(
            conn=db_session,
            invite_id=req.apply_id,
        )
