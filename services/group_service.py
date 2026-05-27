import asyncpg
import uuid
import json
from typing import List, Optional, Dict
from datetime import datetime, timezone
from fastapi import UploadFile
from schemas.group import (
    GroupCreateRequest,
    GroupGenericRequest,
    GroupMembersRequest,
    GroupAdminRequest,
    GroupRemoveMemberRequest,
    GroupAnnouncementRequest,
    GroupInviteRequest,
    GroupInviteReviewRequest,
    GroupUpdateNameRequest,
    GroupBatchInviteRequest,
    GroupPortraitResponse
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
    db_get_pending_group_invites,
    db_remove_admin_invite_states,
    db_assert_can_quit_group,
    db_assert_can_disband_group,
    db_clean_group_invites,
    db_assert_can_remove_member,
    db_get_group_announcements,
    db_get_group_list,
    db_update_group_name,
    db_update_group_profile,
)
from schemas.message import SendMessageRequest, MessageType
from core.s3_client import upload_image_to_s3
from services.message_service import send_message_service
from db.repositories.friend_repo import db_check_is_friend, db_filter_valid_friends
from core.s3_client import s3_client
from core.config import settings

QUERY_FIND_SYSTEM_PRIVATE_CONV = """
    SELECT c.conversation_id
    FROM conversation c
    JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
    JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
    WHERE c.type = 'private'
      AND cm1.member_user_id = $1
      AND cm2.member_user_id = -2
"""
QUERY_GET_USERNAME_BY_ID = "SELECT username FROM user_account WHERE user_id = $1"


async def create_group_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupCreateRequest) -> dict:
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

    # ========== 新增：发送通知 ==========
    # 1. 通知所有被邀请成员（通过私聊助手）
    await notify_members_added_to_group(
        conn=db_session,
        conversation_id=conv_id,
        creator_id=current_user_id,
        member_ids=req.user_ids,
    )

    return {"conversation_id": conv_id, "name": req.name, "avatar": req.avatar}


async def get_group_info_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupGenericRequest) -> dict:
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
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupMembersRequest) -> dict:
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
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupAdminRequest) -> None:
    if current_user_id == req.user_id:
        raise GroupException(GroupErrors.PermissionDenied, "不能操作自己")

    old_role = await db_session.fetchval(
        "SELECT role FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2",
        req.conversation_id, req.user_id
    )
    if not old_role:
        raise GroupException(GroupErrors.NotInGroup, message="该成员已不在群聊中")

    await db_manage_group_role(
        conn=db_session,
        operator_id=current_user_id,
        conversation_id=req.conversation_id,
        target_user_id=req.user_id,
        new_role=req.role,
    )
    # 发送通知（仅当角色确实发生变化时）
    if old_role != req.role:
        # 2. 被操作者私聊通知
        await send_role_change_private_notification(
            conn=db_session,
            target_user_id=req.user_id,
            operator_id=current_user_id,
            conversation_id=req.conversation_id,
            old_role=old_role,
            new_role=req.role,
        )

        # 3.若被操作者失去管理权限，清除其待处理审核记录
        if old_role in ("owner", "admin") and req.role == "member":
            await db_remove_admin_invite_states(
                db_session, req.conversation_id, req.user_id
            )


async def remove_group_member_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupRemoveMemberRequest) -> None:
    # 1. 提前鉴权（通知需要在删除前发送）
    await db_assert_can_remove_member(
        db_session,
        current_user_id,
        req.conversation_id,
        req.user_id,
    )

    # 2. 向被踢用户发送私聊通知
    await send_leave_group_notification(
        db_session,
        user_id=req.user_id,
        conversation_id=req.conversation_id,
        is_kicked=True,
        operator_id=current_user_id,
    )

    # 3. 清理被踢者的待处理入群审核记录
    await db_remove_admin_invite_states(
        db_session, req.conversation_id, req.user_id
    )

    # 4. 执行踢人（内部会再次鉴权，但此时角色不变，不会失败）
    await db_remove_group_member(
        conn=db_session,
        operator_id=current_user_id,
        conversation_id=req.conversation_id,
        target_user_id=req.user_id,
    )


async def quit_group_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupGenericRequest) -> None:
    # 1. 提前鉴权（通知需要在删除前发送）
    await db_assert_can_quit_group(db_session, current_user_id, req.conversation_id)
    # 2. 发送主动退群私聊通知
    await send_leave_group_notification(
        db_session,
        user_id=current_user_id,
        conversation_id=req.conversation_id,
        is_kicked=False,
    )
    # 3. 清理待处理入群审核记录
    await db_remove_admin_invite_states(
        db_session, req.conversation_id, current_user_id
    )
    await db_quit_group(
        conn=db_session, user_id=current_user_id, conversation_id=req.conversation_id
    )


async def disband_group_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupGenericRequest) -> None:
    # 1. 提前鉴权（通知必须在删除前发送）
    await db_assert_can_disband_group(db_session, current_user_id, req.conversation_id)
    # 3. 清理群相关邀请数据（可选）
    await db_clean_group_invites(db_session, req.conversation_id)
    # 4. 执行解散
    await db_disband_group(
        conn=db_session, user_id=current_user_id, conversation_id=req.conversation_id
    )


async def post_group_announcement_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupAnnouncementRequest) -> dict:
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
        msg_type="notify",
        quote_message_id=None,
        extra_data={
            "action": "group_announcement",
            "announcement_id": announcement_id,
            "content": req.msg  # 方便前端直接拿纯净的公告内容去渲染特殊 UI
        }
    )
    # 调用 message 服务
    send_res = await send_message_service(db_session, current_user_id, msg_req)
    server_time = send_res.get("server_time") or datetime.now(timezone.utc)
    return {
        "time": server_time.isoformat(),
        "announcement_id": announcement_id,
    }


async def invite_to_group_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupInviteRequest) -> dict:

    # ========== 新增：好友关系鉴权 ==========
    is_friend = await db_check_is_friend(db_session, current_user_id, req.user_id)
    if not is_friend:
        # 使用统一的 GroupException 格式
        raise GroupException(
            GroupErrors.PermissionDenied,
            "权限不足：只能邀请自己的好友加入群聊")
    # ========================================

    # 1. 创建入群申请记录（邀请制）
    invite_id = await db_invite_to_group(
        conn=db_session,
        inviter_id=current_user_id,
        conversation_id=req.conversation_id,
        invitee_id=req.user_id,
    )

    # 2. 获取邀请人姓名（用于消息展示）
    inviter_name = await db_session.fetchval(
        QUERY_GET_USERNAME_BY_ID, current_user_id
    )

    # 3. 获取该群所有管理员和群主的 user_id（即有审核权限的人）
    admin_ids = await db_get_group_admins(db_session, req.conversation_id)

    # 4. 为每个管理员发送私聊通知
    for admin_id in admin_ids:

        # 4.1 查找该管理员与 -2 号助手的现有私聊会话
        conv_id = await db_session.fetchval(
            QUERY_FIND_SYSTEM_PRIVATE_CONV,
            admin_id
        )

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


async def invite_to_group_batch_service(
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupBatchInviteRequest) -> dict:

    # ========== 新增：批量好友过滤 ==========
    valid_friend_ids = await db_filter_valid_friends(
        db_session, current_user_id, req.user_ids
    )

    if not valid_friend_ids:
        # 如果传过来的所有 ID 都不是当前用户的好友，直接按照统一格式阻断
        raise GroupException(
            GroupErrors.PermissionDenied,
            "权限不足：只能邀请自己的好友加入群聊")
    # ========================================

    # 1. 批量创建入群申请记录（使用事务保证数据一致性）
    apply_records = []
    async with db_session.transaction():
        for invitee_id in valid_friend_ids:
            # 过滤掉自己邀请自己的情况（容错）
            if invitee_id == current_user_id:
                continue

            # 复用你现有的 DB 插入逻辑，这样不会破坏已有的 group_invite_admin_state 等关联表逻辑
            invite_id = await db_invite_to_group(
                conn=db_session,
                inviter_id=current_user_id,
                conversation_id=req.conversation_id,
                invitee_id=invitee_id,
            )
            apply_records.append(
                {"user_id": invitee_id, "apply_id": invite_id})

    # 如果所有有效邀请都被过滤了，直接返回
    if not apply_records:
        return {"applies": []}

    # 2. 获取邀请人姓名
    inviter_name = await db_session.fetchval(
        QUERY_GET_USERNAME_BY_ID, current_user_id
    )

    # 3. 获取管理员和群主
    admin_ids = await db_get_group_admins(db_session, req.conversation_id)

    # 4. 为每个管理员发送私聊卡片通知
    for admin_id in admin_ids:
        conv_id = await db_session.fetchval(
            QUERY_FIND_SYSTEM_PRIVATE_CONV,
            admin_id
        )

        if conv_id is None:
            continue  # 宽容处理：如果有管理员缺失系统会话，跳过他，不要阻塞其他人的通知

        # 4.2 为当前管理员批量发送卡片（每个人一张卡片，方便前端逐个点击同意/拒绝）
        for apply_info in apply_records:
            msg_req = SendMessageRequest(
                conversation_id=conv_id,
                local_id=str(uuid.uuid4()),
                message_content="[收到一条入群申请]",
                msg_type=MessageType.CARD,
                extra_data={
                    "card_type": "group_apply",
                    "apply_id": apply_info["apply_id"],
                    "applicant_id": apply_info["user_id"],
                    "inviter_id": current_user_id,
                    "inviter_name": inviter_name,
                    "conversation_id": req.conversation_id,
                    "status": "pending"
                }
            )

            # 4.3 发送消息
            await send_message_service(
                db_session=db_session,
                user_id=-2,
                req=msg_req
            )

    return {"applies": apply_records}


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

    tip_text = f"群聊 {conversation_id} 的入群申请已被其他管理员批准，请刷新待审批列表。"
    extra_action = "group_invite_approved_by_other"

    # 4. 循环发送系统消息
    for admin_record in other_admins:
        admin_id = admin_record["member_user_id"]
        # 获取该管理员与系统助手的私聊会话ID
        system_conv_id = await conn.fetchval(QUERY_FIND_SYSTEM_PRIVATE_CONV, admin_id)
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
    system_conv_id = await conn.fetchval(QUERY_FIND_SYSTEM_PRIVATE_CONV, invitee_id)
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
        db_session: asyncpg.Connection,
        current_user_id: int,
        req: GroupInviteReviewRequest) -> None:
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

        # 通知被邀请人：申请已通过
        await notify_invitee_approved(
            conn=db_session,
            invite_id=req.apply_id,
        )


async def get_pending_group_invites_as_cards(
    db_session: asyncpg.Connection,
    current_user_id: int
) -> list[dict]:
    """
    获取当前用户待审批的入群申请，并转换为卡片格式。
    """
    raw_invites = await db_get_pending_group_invites(db_session, current_user_id)

    cards = []
    for inv in raw_invites:
        cards.append({
            "card_type": "group_apply",
            "apply_id": inv["invite_id"],
            "conversation_id": inv["conversation_id"],
            "conversation_name": inv["conversation_name"],
            "group_avatar": inv["group_avatar"],
            "applicant_id": inv["applicant_id"],
            "applicant_name": inv["applicant_name"],
            "applicant_avatar": inv["applicant_avatar"],
            "inviter_id": inv["inviter_id"],
            "inviter_name": inv["inviter_name"],
            "inviter_avatar": inv["inviter_avatar"],
            "status": "pending",
            "create_time": int(inv["create_time"].timestamp())  # 转为 Unix 时间戳
        })
    return cards


async def notify_members_added_to_group(
    conn: asyncpg.Connection,
    conversation_id: int,
    creator_id: int,
    member_ids: List[int],
) -> None:
    """
    向所有被邀请加入群聊的成员发送私聊系统通知（通过群聊助手 -2 发送）。
    """
    # 1. 获取创建者名称
    creator_name = await conn.fetchval(
        QUERY_GET_USERNAME_BY_ID,
        creator_id,
    )
    if not creator_name:
        creator_name = str(creator_id)

    # 2. 为每个被邀请成员查找与系统助手(-2)的私聊会话并发送通知

    tip_text = f"{creator_name} 将你加入了群聊"
    extra_data = {
        "action": "added_to_group",
        "conversation_id": conversation_id,
        "creator_id": creator_id,
        "tips": tip_text,
    }

    for member_id in member_ids:
        if member_id == creator_id:
            continue  # 创建者不需要自己通知自己

        system_conv_id = await conn.fetchval(QUERY_FIND_SYSTEM_PRIVATE_CONV, member_id)
        if not system_conv_id:
            # 理论上每个用户都应该有与助手的私聊会话，没有则跳过
            continue

        send_req = SendMessageRequest(
            conversation_id=system_conv_id,
            local_id=str(uuid.uuid4()),
            message_content="[你被邀请加入群聊]",
            msg_type=MessageType.NOTIFY,
            extra_data=extra_data,
        )
        try:
            await send_message_service(conn, -2, send_req)
        except Exception as e:
            # 记录日志，不影响主流程
            print(f"发送群创建通知给成员 {member_id} 失败: {e}")


async def send_role_change_private_notification(
    conn: asyncpg.Connection,
    target_user_id: int,
    operator_id: int,
    conversation_id: int,
    old_role: str,
    new_role: str,
) -> None:
    """
    向被操作者发送私聊系统通知（角色变更提醒）
    """
    # 获取操作者和群名称
    operator_name = await conn.fetchval(
        QUERY_GET_USERNAME_BY_ID, operator_id
    )
    group_name = await conn.fetchval(
        "SELECT conversation_name FROM conversation WHERE conversation_id = $1", conversation_id
    )
    if not operator_name:
        operator_name = str(operator_id)
    group_display = group_name or f"群聊{conversation_id}"

    # 查询目标用户与系统助手的私聊会话
    system_conv = await conn.fetchval(
        QUERY_FIND_SYSTEM_PRIVATE_CONV,
        target_user_id
    )
    if not system_conv:
        return

    # 构造消息文案
    if new_role == "owner":
        tips = f"{operator_name} 已将群聊 {group_display} 的群主转让给你"
        action = "group_owner_transferred_to_you"
    elif new_role == "admin" and old_role == "member":
        tips = f"你已被 {operator_name} 设置为群聊 {group_display} 的管理员"
        action = "group_admin_set_to_you"
    elif new_role == "member" and old_role == "admin":
        tips = f"你已被 {operator_name} 撤销群聊 {group_display} 的管理员"
        action = "group_admin_unset_from_you"
    else:
        return

    send_req = SendMessageRequest(
        conversation_id=system_conv,
        local_id=str(uuid.uuid4()),
        message_content="[群角色变更通知]",
        msg_type=MessageType.NOTIFY,
        extra_data={
            "action": action,
            "tips": tips,
            "conversation_id": conversation_id,
            "operator_id": operator_id,
            "new_role": new_role,
        }
    )
    await send_message_service(conn, -2, send_req)


async def send_leave_group_notification(
    conn: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
    is_kicked: bool,
    operator_id: Optional[int] = None,
) -> None:
    """向退群/被踢用户发送私聊通知"""
    # 获取群名称
    group_name = await conn.fetchval(
        "SELECT conversation_name FROM conversation WHERE conversation_id = $1",
        conversation_id
    )
    group_display = group_name or f"群聊{conversation_id}"

    if is_kicked and operator_id:
        operator_name = await conn.fetchval(
            QUERY_GET_USERNAME_BY_ID, operator_id
        ) or str(operator_id)
        tips = f"你已被 {operator_name} 移出群聊 {group_display}"
        action = "kicked_from_group"
        content = "[你已被移出群聊]"
    else:
        tips = f"你已退出群聊 {group_display}"
        action = "left_group"
        content = "[你已退出群聊]"

    # 查找用户与系统助手的私聊
    system_conv = await conn.fetchval(
        QUERY_FIND_SYSTEM_PRIVATE_CONV, user_id  # 使用之前定义的常量
    )
    if not system_conv:
        return

    send_req = SendMessageRequest(
        conversation_id=system_conv,
        local_id=str(uuid.uuid4()),
        message_content=content,
        msg_type=MessageType.NOTIFY,
        extra_data={
            "action": action,
            "tips": tips,
            "conversation_id": conversation_id,
            "group_name": group_display,
        }
    )
    if operator_id:
        send_req.extra_data["operator_id"] = operator_id

    await send_message_service(conn, -2, send_req)


async def get_group_announcements_service(
    db_session: asyncpg.Connection,
    current_user_id: int,
    conversation_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    获取群公告列表服务。
    """
    return await db_get_group_announcements(
        conn=db_session,
        user_id=current_user_id,
        conversation_id=conversation_id,
        page=page,
        page_size=page_size,
    )


async def get_group_list(
        db_session: asyncpg.Connection,
        current_user_id: int) -> List[Dict]:
    """
    获取当前用户加入的群聊列表。
    """
    groups = await db_get_group_list(db_session, current_user_id)
    return groups


async def update_group_name_service(
    db_session: asyncpg.Connection,
    current_user_id: int,
    req: GroupUpdateNameRequest
) -> dict:
    """处理修改群聊名称的业务逻辑"""
    # 1. 校验权限：仅 owner 和 admin 可以修改
    query_role = """
        SELECT role FROM conversation_member
        WHERE conversation_id = $1 AND member_user_id = $2
    """
    role = await db_session.fetchval(query_role, req.conversation_id, current_user_id)

    if not role:
        # 请替换为你的业务异常类，返回 403 或 404
        raise GroupException(GroupErrors.NotInGroup)
    if role not in ("owner", "admin"):
        # 如果你有定义 GroupException 和状态码映射，请抛出对应 403 的异常
        raise GroupException(GroupErrors.PermissionDenied)

    # 2. 更新数据库中的名称
    await db_update_group_name(db_session, req.conversation_id, req.new_name)

    return {"conversation_id": req.conversation_id, "new_name": req.new_name}

MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 限制为 2MB (以字节为单位)


async def edit_group_portrait_service(
    db_session: asyncpg.Connection,
    current_user_id: int,
    group_id: int,
    file: UploadFile
):
    """
    修改群头像的业务逻辑服务
    """

    # 1. 校验权限：仅 owner 和 admin 可以修改
    query_role = """
        SELECT role FROM conversation_member
        WHERE conversation_id = $1 AND member_user_id = $2
    """
    role = await db_session.fetchval(query_role, group_id, current_user_id)

    if not role:
        # 请替换为你的业务异常类，返回 403 或 404
        raise GroupException(GroupErrors.NotInGroup)
    if role not in ("owner", "admin"):
        # 如果你有定义 GroupException 和状态码映射，请抛出对应 403 的异常
        raise GroupException(GroupErrors.PermissionDenied)

    # 🌟 复用同一个公共工具函数
    object_name, group_avatar_url = await upload_image_to_s3(
        file=file,
        bucket_name=settings.BUCKET_AVATAR,  # 也可以用 settings.BUCKET_GROUP
        max_size=MAX_AVATAR_SIZE,
        err_msg_prefix="群头像"
    )

    # 更新群组数据库表
    is_success = await db_update_group_profile(
        db_session,
        group_id=group_id,
        avatar_url=group_avatar_url
    )

    # 逆向擦除逻辑
    if not is_success:
        try:
            s3_client.remove_object(settings.BUCKET_AVATAR, object_name)
        except Exception:
            pass
        raise GroupException(status_code=500, detail="数据库更新群头像失败")

    return GroupPortraitResponse(
        code=200,
        filekey=group_avatar_url,
        width=256,
        height=256
    )
