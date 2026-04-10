import asyncpg
import json
from core.exceptions import GroupException, GroupErrors

# Sonar
QUERY_GET_MEMBER_ROLE = "SELECT role FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;"
QUERY_GET_CONVERSATION_LIST = """
    SELECT
        c.conversation_id,
        c.conversation_name,
        c.last_msg_time,
        m.msg_body
    FROM conversation_member cm
    JOIN conversation c ON cm.conversation_id = c.conversation_id
    -- 左连接 message 表，把最后一条消息的本体拉出来作为列表预览
    LEFT JOIN message m ON c.last_msg_id = m.msg_id
    WHERE cm.member_user_id = $1
    -- NULLS LAST 保证新建的、还没发过消息的群排在最下面
    ORDER BY c.last_msg_time DESC NULLS LAST;
"""


async def db_get_conversation_list(
    conn: asyncpg.Connection, user_id: int
) -> list[dict]:
    """
    获取用户的群聊列表 (按最新活跃时间排序，包含最后一条消息预览)
    """
    records = await conn.fetch(QUERY_GET_CONVERSATION_LIST, user_id)

    result = []
    for row in records:
        # 解析你存入的 JSONB 类型的 msg_body，提取用于展示的预览文本
        preview_text = ""
        if row["msg_body"]:
            try:
                # 假设你的前端 msg_body 里面有一个 "text" 字段存文本
                body_dict = json.loads(row["msg_body"])
                preview_text = body_dict.get("text", "[非文本消息]")
            except json.JSONDecodeError:
                preview_text = "[解析错误]"

        result.append(
            {
                "conversation_id": row["conversation_id"],
                "conversation_name": row["conversation_name"],
                # 格式化时间戳，防范新建群聊还没发消息导致 time 为 None 的情况
                "last_msg_time": (
                    row["last_msg_time"].isoformat(
                    ) if row["last_msg_time"] else None
                ),
                "last_msg_preview": preview_text,
            }
        )

    return result


async def db_create_group(
    conn: asyncpg.Connection,
    creator_id: int,
    member_ids: list[int],
    avatar_url: str | None = None,
    group_name: str = "未命名群聊",
) -> int:
    """
    创建群聊 (对应 POST /api/group/create)
    将创建者设为 owner，其他好友设为 member
    """
    async with conn.transaction():
        # 1. 插入会话基础信息
        query_conv = (
            "INSERT INTO conversation (type, conversation_name, avatar_url) VALUES ('group', $1) RETURNING conversation_id;"
        )
        conv_id = await conn.fetchval(query_conv, group_name, avatar_url)

        # 2. 插入群主 (owner)
        query_owner = "INSERT INTO conversation_member (conversation_id, member_user_id, role) VALUES ($1, $2, 'owner');"
        await conn.execute(query_owner, conv_id, creator_id)

        # 3. 批量插入普通群员
        # 去重，并且把群主自己从列表里剔除（防止前端传错导致主键冲突）
        actual_members = list(set(member_ids) - {creator_id})
        if actual_members:
            records = [(conv_id, uid, "member") for uid in actual_members]
            query_members = "INSERT INTO conversation_member (conversation_id, member_user_id, role) VALUES ($1, $2, $3);"
            await conn.executemany(query_members, records)

    return conv_id


async def db_get_group_info(
    conn: asyncpg.Connection, user_id: int, conversation_id: int
) -> dict:
    """
    获取群详细信息 (对应 POST /api/group/info)
    包含群基础信息、群成员列表和历史公告
    """
    # 1. 鉴权：只有群成员能看群信息
    check_role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if not check_role:
        raise GroupException(GroupErrors.NotInGroup)

    # 2. 拉取群基础信息
    info = dict(
        await conn.fetchrow(
            "SELECT conversation_id, conversation_name, create_time FROM conversation WHERE conversation_id = $1;",
            conversation_id,
        )
    )

    # 3. 拉取群成员列表 (带上用户的昵称和头像)
    query_members = """
        SELECT cm.member_user_id, u.username, u.avatar_url, cm.role, cm.join_time
        FROM conversation_member cm
        JOIN user_account u ON cm.member_user_id = u.user_id
        WHERE cm.conversation_id = $1
        ORDER BY
            CASE role WHEN 'owner' THEN 1 WHEN 'admin' THEN 2 ELSE 3 END,
            cm.join_time ASC;
    """
    info["members"] = [
        dict(row) for row in await conn.fetch(query_members, conversation_id)
    ]

    # 4. 拉取历史公告
    query_announcements = """
        SELECT a.announcement_id, a.content, a.is_pinned, a.create_time, u.username as sender_name
        FROM group_announcement a
        JOIN user_account u ON a.sender_id = u.user_id
        WHERE a.conversation_id = $1
        ORDER BY a.is_pinned DESC, a.create_time DESC;
    """
    info["announcements"] = [
        dict(row) for row in await conn.fetch(query_announcements, conversation_id)
    ]

    return info


async def db_quit_group(
    conn: asyncpg.Connection, user_id: int, conversation_id: int
) -> None:
    """
    退出群聊 (对应 POST /api/group/quit)
    """
    role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if not role:
        raise GroupException(GroupErrors.NotInGroup)

    # 群主不能退群
    if role == "owner":
        raise GroupException(GroupErrors.OwnerCannotQuit)

    await conn.execute(
        "DELETE FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;",
        conversation_id,
        user_id,
    )


async def db_disband_group(
    conn: asyncpg.Connection, user_id: int, conversation_id: int
) -> None:
    """
    解散群聊 (对应 POST /api/group/bomb)
    """
    role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if role != "owner":
        raise GroupException(GroupErrors.PermissionDenied)
    await conn.execute(
        "DELETE FROM conversation WHERE conversation_id = $1;", conversation_id
    )


async def db_remove_group_member(
    conn: asyncpg.Connection,
    operator_id: int,
    conversation_id: int,
    target_user_id: int,
) -> None:
    """
    移除群员 (对应 DELETE /api/group/member)
    包含严格的阶级等级压制校验。
    """
    # 1. 不能自己踢自己 (自己退群应该调 quit 接口)
    if operator_id == target_user_id:
        raise GroupException(GroupErrors.PermissionDenied)

    # 2. 同时查出操作者和被踢者的角色
    query = """
        SELECT member_user_id, role
        FROM conversation_member
        WHERE conversation_id = $1 AND member_user_id IN ($2, $3);
    """
    rows = await conn.fetch(query, conversation_id, operator_id, target_user_id)

    role_map = {row["member_user_id"]: row["role"] for row in rows}

    operator_role = role_map.get(operator_id)
    target_role = role_map.get(target_user_id)

    # 如果其中有人不在群里
    if not operator_role or not target_role:
        raise GroupException(GroupErrors.NotInGroup)

    # 3. 核心鉴权逻辑 (等级压制)
    if operator_role == "member":
        # 普通人谁也踢不了
        raise GroupException(GroupErrors.PermissionDenied)
    elif operator_role == "admin" and target_role in ("owner", "admin"):
        # 管理员只能踢普通人，不能踢群主，也不能互踢
        raise GroupException(GroupErrors.CannotKickHigherRole)
    # 如果是 owner，则畅通无阻，可以直接往下走

    # 4. 执行踢人操作
    delete_query = "DELETE FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;"
    await conn.execute(delete_query, conversation_id, target_user_id)


async def db_manage_group_role(
    conn: asyncpg.Connection,
    operator_id: int,
    conversation_id: int,
    target_user_id: int,
    new_role: str,
) -> None:
    """
    群权限管理 (对应 PUT /api/group/admin)
    new_role 必须是 'admin', 'member' (取消管理员), 或 'owner' (转让群主)
    """
    if new_role not in ("admin", "member", "owner"):
        raise GroupException(GroupErrors.InvalidRole)

    # 1. 只有现任群主才有资格分配权限
    operator_role = await conn.fetchval(
        QUERY_GET_MEMBER_ROLE, conversation_id, operator_id
    )
    if operator_role != "owner":
        raise GroupException(GroupErrors.PermissionDenied)

    # 2. 确认目标在群里
    target_role = await conn.fetchval(
        QUERY_GET_MEMBER_ROLE, conversation_id, target_user_id
    )
    if not target_role:
        raise GroupException(GroupErrors.NotInGroup)

    async with conn.transaction():
        if new_role == "owner":
            # 转让群主
            # a. 先把自己降级为管理员 (或 member)
            await conn.execute(
                "UPDATE conversation_member SET role = 'admin' WHERE conversation_id = $1 AND member_user_id = $2;",
                conversation_id,
                operator_id,
            )
            # b. 把对方提拔为群主
            await conn.execute(
                "UPDATE conversation_member SET role = 'owner' WHERE conversation_id = $1 AND member_user_id = $2;",
                conversation_id,
                target_user_id,
            )
        else:
            # 普通的提拔管理员 / 撤销管理员
            await conn.execute(
                "UPDATE conversation_member SET role = $1 WHERE conversation_id = $2 AND member_user_id = $3;",
                new_role,
                conversation_id,
                target_user_id,
            )


async def db_post_group_announcement(
    conn: asyncpg.Connection,
    operator_id: int,
    conversation_id: int,
    content: str,
    is_pinned: bool = False,
) -> int:
    """
    发布群公告 (对应 POST /api/group/announcement)
    返回新生成的公告 ID
    """
    # 1. 鉴权：必须是 owner 或 admin
    operator_role = await conn.fetchval(
        QUERY_GET_MEMBER_ROLE, conversation_id, operator_id
    )
    if operator_role not in ("owner", "admin"):
        raise GroupException(GroupErrors.PermissionDenied)

    # 2. 插入公告记录
    query = """
        INSERT INTO group_announcement (conversation_id, sender_id, content, is_pinned)
        VALUES ($1, $2, $3, $4)
        RETURNING announcement_id;
    """
    announcement_id = await conn.fetchval(
        query, conversation_id, operator_id, content, is_pinned
    )
    return announcement_id


async def db_invite_to_group(
    conn: asyncpg.Connection, inviter_id: int, conversation_id: int, invitee_id: int
) -> int:
    """
    邀请好友加入群聊 (对应 POST /api/group/invite)
    产生一条 pending 状态的邀请记录，等待审核
    返回新生成的 invite_id
    """
    # 1. 不能邀请自己
    if inviter_id == invitee_id:
        raise GroupException(GroupErrors.CannotInviteSelf)

    # 2. 校验邀请人必须在群里
    inviter_role = await conn.fetchval(
        QUERY_GET_MEMBER_ROLE, conversation_id, inviter_id
    )
    if not inviter_role:
        raise GroupException(GroupErrors.NotInGroup)

    # 3. 校验被邀请人是否已经在群里了
    is_invitee_in_group = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2);",
        conversation_id,
        invitee_id,
    )
    if is_invitee_in_group:
        raise GroupException(GroupErrors.AlreadyInGroup)

    # 4. 防轰炸：校验是否已经有关于该用户的待审核邀请
    has_pending = await conn.fetchval(
        """
        SELECT EXISTS(SELECT 1 FROM group_invite
        WHERE conversation_id = $1 AND invitee_id = $2 AND status = 'pending');
        """,
        conversation_id,
        invitee_id,
    )
    if has_pending:
        raise GroupException(GroupErrors.InvitePending)

    # 5. 插入邀请记录
    query = """
        INSERT INTO group_invite (conversation_id, inviter_id, invitee_id)
        VALUES ($1, $2, $3)
        RETURNING invite_id;
    """
    invite_id = await conn.fetchval(query, conversation_id, inviter_id, invitee_id)
    return invite_id


async def db_review_group_invite(
    conn: asyncpg.Connection, reviewer_id: int, invite_id: int, action: str
) -> None:
    """
    审核群邀请 (对应 PUT /api/group/invite/review)
    action 必须是 'approved' 或 'ignored'
    """
    if action not in ("approved", "ignored"):
        raise GroupException(GroupErrors.InvalidReviewAction)

    # 1. 查找这条邀请记录
    query_invite = "SELECT conversation_id, invitee_id, status FROM group_invite WHERE invite_id = $1;"
    invite_record = await conn.fetchrow(query_invite, invite_id)

    if not invite_record or invite_record["status"] != "pending":
        raise GroupException(GroupErrors.InviteNotFound)

    conversation_id = invite_record["conversation_id"]
    invitee_id = invite_record["invitee_id"]

    # 2. 核心鉴权：审核人必须是这个群的 owner 或 admin
    reviewer_role = await conn.fetchval(
        QUERY_GET_MEMBER_ROLE, conversation_id, reviewer_id
    )
    if reviewer_role not in ("owner", "admin"):
        raise GroupException(GroupErrors.PermissionDenied)

    # 3. 开启强事务处理审核结果
    async with conn.transaction():
        # a. 更新邀请状态
        result = await conn.execute(
            "UPDATE group_invite SET status = $1 WHERE invite_id = $2 AND status = 'pending';",
            action,
            invite_id,
        )

        if result == "UPDATE 0":
            raise GroupException(GroupErrors.InviteNotFound)

        # b. 如果通过了，就把人拉进群
        if action == "approved":
            # ON CONFLICT DO NOTHING 防止极端并发下重复拉人报错
            insert_member = """
                INSERT INTO conversation_member (conversation_id, member_user_id, role)
                VALUES ($1, $2, 'member')
                ON CONFLICT (conversation_id, member_user_id) DO NOTHING;
            """
            await conn.execute(insert_member, conversation_id, invitee_id)
