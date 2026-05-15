import asyncpg
import json
from core.exceptions import GroupException, GroupErrors
from schemas.group import AnnouncementItem

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

QUERY_GET_GROUP_ANNOUNCEMENTS = """
    SELECT
        a.announcement_id,
        a.content,
        EXTRACT(EPOCH FROM a.create_time)::bigint AS create_time,
        u.username AS sender_name,
        COUNT(*) OVER() AS total
    FROM group_announcement a
    LEFT JOIN "user_account" u ON a.sender_id = u.user_id
    WHERE a.conversation_id = $1
    ORDER BY a.is_pinned DESC, a.create_time DESC
    OFFSET $2 LIMIT $3
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
            "INSERT INTO conversation (type, conversation_name, avatar_url) "
            "VALUES ('group', $1, $2) RETURNING conversation_id;"
        )
        conv_id = await conn.fetchval(query_conv, group_name, avatar_url)

        # 2. 插入群主 (owner)
        query_owner = (
            "INSERT INTO conversation_member (conversation_id, member_user_id, role) "
            "VALUES ($1, $2, 'owner');")
        await conn.execute(query_owner, conv_id, creator_id)

        # 3. 批量插入普通群员
        # 去重，并且把群主自己从列表里剔除（防止前端传错导致主键冲突）
        actual_members = list(set(member_ids) - {creator_id})
        if actual_members:
            records = [(conv_id, uid, "member") for uid in actual_members]
            query_members = (
                "INSERT INTO conversation_member (conversation_id, member_user_id, role) "
                "VALUES ($1, $2, $3);")
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
    # 鉴权（保证只能退群，且防御并发）
    await db_assert_can_quit_group(conn, user_id, conversation_id)

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
    await db_assert_can_disband_group(conn, user_id, conversation_id)
    # 标记群已解散
    await conn.execute(
        "UPDATE conversation SET is_disbanded = true WHERE conversation_id = $1",
        conversation_id
    )
    # 删除所有成员（除了系统账号）
    await conn.execute(
        "DELETE FROM conversation_member WHERE conversation_id = $1",
        conversation_id
    )


async def db_assert_can_disband_group(
    conn: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
) -> str:
    """
    校验用户是否有权解散群聊，并返回当前角色（必须是 owner）。
    - 不在群内 → GroupErrors.NotInGroup
    - 不是群主 → GroupErrors.PermissionDenied
    """
    role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if not role:
        raise GroupException(GroupErrors.NotInGroup)
    if role != "owner":
        raise GroupException(GroupErrors.PermissionDenied)
    return role


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
    # 校验权限（会抛出异常）
    await db_assert_can_remove_member(conn, operator_id, conversation_id, target_user_id)

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
        raise GroupException(GroupErrors.NotInGroup, message="该成员已退出群聊")

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
        conn: asyncpg.Connection,
        inviter_id: int,
        conversation_id: int,
        invitee_id: int) -> int:
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

    # 5. 开启事务（如果 conn 还未处于事务中，可显式开始）
    async with conn.transaction():
        # 5.1 插入邀请主记录
        query = """
            INSERT INTO group_invite (conversation_id, inviter_id, invitee_id)
            VALUES ($1, $2, $3)
            RETURNING invite_id;
        """
        invite_id = await conn.fetchval(query, conversation_id, inviter_id, invitee_id)

        # 5.2 获取群内所有管理员的用户ID（包括群主）
        admin_ids = await conn.fetch(
            """
            SELECT member_user_id
            FROM conversation_member
            WHERE conversation_id = $1
              AND (role = 'admin' OR role = 'owner');
            """,
            conversation_id,
        )
        admin_ids = [record["member_user_id"] for record in admin_ids]

        if not admin_ids:
            # 没有管理员：抛出异常，要求群必须至少有一个管理员
            raise GroupException(GroupErrors.NoAdminInGroup)

        # 5.3 为每个管理员创建审核状态记录
        insert_state_sql = """
            INSERT INTO group_invite_admin_state (invite_id, admin_id, state)
            VALUES ($1, $2, 'pending');
        """
        for admin_id in admin_ids:
            await conn.execute(insert_state_sql, invite_id, admin_id)

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
    query_invite = """
        SELECT conversation_id, invitee_id, status
        FROM group_invite
        WHERE invite_id = $1;
    """
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
        upsert_admin_state = """
            INSERT INTO group_invite_admin_state (invite_id, admin_id, state)
            VALUES ($1, $2, $3)
            ON CONFLICT (invite_id, admin_id)
            DO UPDATE SET state = EXCLUDED.state, updated_time = CURRENT_TIMESTAMP;
        """

        await conn.execute(upsert_admin_state, invite_id, reviewer_id, action)

        # 4. 如果批准，则全局生效并覆盖所有管理员的个人状态
        if action == "approved":
            # 4.1 更新全局邀请状态
            update_global = """
                UPDATE group_invite
                SET status = 'approved'
                WHERE invite_id = $1 AND status = 'pending';
            """
            result = await conn.execute(update_global, invite_id)

            if result == "UPDATE 0":
                # 理论上不会发生，因为上面已检查过 status='pending'
                raise GroupException(GroupErrors.InviteNotFound)

            # 4.2 将所有管理员在该邀请上的个人状态强制改为 'approved'
            force_approve_all = """
                UPDATE group_invite_admin_state
                SET state = 'approved', updated_time = CURRENT_TIMESTAMP
                WHERE invite_id = $1;
            """
            await conn.execute(force_approve_all, invite_id)

            insert_member = """
                INSERT INTO conversation_member (conversation_id, member_user_id, role)
                VALUES ($1, $2, 'member')
                ON CONFLICT (conversation_id, member_user_id) DO NOTHING;
            """
            await conn.execute(insert_member, conversation_id, invitee_id)


async def db_get_pending_group_invite_count(
        conn: asyncpg.Connection,
        user_id: int) -> int:
    """
    统计当前用户作为群主/管理员，待我审批的邀请数量
    （基于 group_invite_admin_state 表中 state='pending' 且邀请全局 status='pending'）
    """
    query = """
        SELECT COUNT(1)
        FROM group_invite_admin_state gias
        JOIN group_invite gi ON gias.invite_id = gi.invite_id
        JOIN conversation_member cm ON gi.conversation_id = cm.conversation_id
                                   AND cm.member_user_id = gias.admin_id
        WHERE gias.admin_id = $1
          AND gias.state = 'pending'
          AND gi.status = 'pending'
          AND cm.role IN ('owner', 'admin')
          AND cm.is_active = true;
    """
    count = await conn.fetchval(query, user_id)
    return count or 0


async def db_get_group_admins(
        conn: asyncpg.Connection,
        conversation_id: int) -> list[int]:
    """返回该群所有具有管理权限的用户 ID（群主 + 管理员）"""
    rows = await conn.fetch("""
        SELECT member_user_id
        FROM conversation_member
        WHERE conversation_id = $1
          AND role IN ('owner', 'admin')
    """, conversation_id)
    return [row["member_user_id"] for row in rows]


async def db_get_pending_group_invites(
    conn: asyncpg.Connection,
    admin_id: int
) -> list[dict]:
    """
    获取当前管理员待审批的入群邀请列表（仅限全局状态 pending 且该管理员状态 pending）
    返回字段包括：邀请ID、群信息、申请人信息、邀请人信息、创建时间等
    """
    query = """
        SELECT
            gi.invite_id,
            gi.conversation_id,
            c.conversation_name,
            c.avatar_url AS group_avatar,
            gi.invitee_id AS applicant_id,
            u1.username AS applicant_name,
            u1.avatar_url AS applicant_avatar,
            gi.inviter_id,
            u2.username AS inviter_name,
            u2.avatar_url AS inviter_avatar,
            gi.create_time
        FROM group_invite_admin_state gias
        JOIN group_invite gi ON gias.invite_id = gi.invite_id
        JOIN conversation c ON gi.conversation_id = c.conversation_id
        JOIN user_account u1 ON gi.invitee_id = u1.user_id
        JOIN user_account u2 ON gi.inviter_id = u2.user_id
        WHERE gias.admin_id = $1
          AND gias.state = 'pending'
          AND gi.status = 'pending'
        ORDER BY gi.create_time DESC
    """
    rows = await conn.fetch(query, admin_id)

    result = []
    for row in rows:
        result.append({
            "invite_id": row["invite_id"],
            "conversation_id": row["conversation_id"],
            "conversation_name": row["conversation_name"],
            "group_avatar": row["group_avatar"],
            "applicant_id": row["applicant_id"],
            "applicant_name": row["applicant_name"],
            "applicant_avatar": row["applicant_avatar"],
            "inviter_id": row["inviter_id"],
            "inviter_name": row["inviter_name"],
            "inviter_avatar": row["inviter_avatar"],
            "create_time": row["create_time"],  # datetime 对象
        })
    return result


async def db_remove_admin_invite_states(
    conn: asyncpg.Connection,
    conversation_id: int,
    admin_id: int,
) -> None:
    """
    删除指定管理员在某群的所有待处理邀请审核状态记录。
    用于当管理员被撤销或群主转让后，不再参与该群入群审核。
    """
    query = """
        DELETE FROM group_invite_admin_state
        WHERE admin_id = $1
          AND invite_id IN (
              SELECT invite_id FROM group_invite
              WHERE conversation_id = $2 AND status = 'pending'
          )
    """
    await conn.execute(query, admin_id, conversation_id)


async def db_assert_can_quit_group(
    conn: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
) -> str:
    """
    校验用户是否有权退出群聊，并返回当前角色。
    - 不在群内 → GroupErrors.NotInGroup
    - 是群主   → GroupErrors.OwnerCannotQuit
    """
    role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if not role:
        raise GroupException(GroupErrors.NotInGroup)
    if role == "owner":
        raise GroupException(GroupErrors.OwnerCannotQuit)
    return role


async def db_assert_can_remove_member(
    conn: asyncpg.Connection,
    operator_id: int,
    conversation_id: int,
    target_user_id: int,
) -> tuple[str, str]:
    """
    校验操作者是否有权踢出目标成员，并返回两者的角色。
    会抛出 GroupException 如果：
      - 操作者自踢
      - 任一用户不在群内
      - 等级压制不满足
    """
    if operator_id == target_user_id:
        raise GroupException(GroupErrors.PermissionDenied)

    query = """
        SELECT member_user_id, role
        FROM conversation_member
        WHERE conversation_id = $1 AND member_user_id IN ($2, $3);
    """
    rows = await conn.fetch(query, conversation_id, operator_id, target_user_id)
    role_map = {row["member_user_id"]: row["role"] for row in rows}

    operator_role = role_map.get(operator_id)
    target_role = role_map.get(target_user_id)

    if not operator_role:
        raise GroupException(GroupErrors.NotInGroup, message="你已不在群聊中")
    if not target_role:
        raise GroupException(GroupErrors.NotInGroup, message="该成员已退出群聊")

    # 等级压制
    if operator_role == "member":
        raise GroupException(GroupErrors.PermissionDenied)
    if operator_role == "admin" and target_role in ("owner", "admin"):
        raise GroupException(GroupErrors.CannotKickHigherRole)

    return operator_role, target_role


async def db_clean_group_invites(
        conn: asyncpg.Connection,
        conversation_id: int):
    await conn.execute("""
        DELETE FROM group_invite_admin_state
        WHERE invite_id IN (SELECT invite_id FROM group_invite WHERE conversation_id = $1)
    """, conversation_id)
    await conn.execute("""
        DELETE FROM group_invite WHERE conversation_id = $1
    """, conversation_id)


async def db_get_group_announcements(
    conn: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    查询群公告列表，需鉴权为群成员。
    返回包含 items、total、page、page_size 的字典。
    """
    # 1. 鉴权：必须是该群成员
    role = await conn.fetchval(QUERY_GET_MEMBER_ROLE, conversation_id, user_id)
    if not role:  # 非成员（角色不存在）
        raise GroupException(GroupErrors.NotInGroup)

    # 2. 分页查询
    offset = (page - 1) * page_size
    rows = await conn.fetch(
        QUERY_GET_GROUP_ANNOUNCEMENTS,
        conversation_id,
        offset,
        page_size,
    )

    # 3. 提取总数（从窗口函数返回的 total）
    total = rows[0]["total"] if rows else 0

    # 4. 构造 AnnouncementItem 列表
    items = [
        AnnouncementItem(
            announcement_id=row["announcement_id"],
            content=row["content"],
            create_time=int(row["create_time"]),  # 已经是 bigint
            sender_name=row["sender_name"],
        )
        for row in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def db_get_group_list(conn: asyncpg.Connection, user_id: int) -> list[dict]:
    """
    获取当前用户所在的群聊列表。
    需要联表查询 conversation 表拿到群名称和头像。
    """
    query = """
        SELECT
            c.conversation_id,
            c.conversation_name,
            c.avatar_url,
            cm.role,
            cm.join_time
        FROM conversation_member cm
        JOIN conversation c ON cm.conversation_id = c.conversation_id
        WHERE cm.member_user_id = $1
          AND c.type = 'group'
          AND cm.is_active = true
        ORDER BY cm.join_time DESC; -- 按加入时间倒序排列（或按你业务需求的字段排序）
    """
    rows = await conn.fetch(query, user_id)
    return [dict(row) for row in rows]


async def db_resolve_pending_invites_for_group(conn: asyncpg.Connection, conversation_id: int) -> list[int]:
    """
    【核心状态机】群邀请状态全局结算函数。 (适配“新管理员不审核老申请”的规则)
    """
    query = """
        WITH invite_stats AS (
            -- 针对该群所有还在 pending 的邀请，统计它们的“专属审核委员会”的投票情况
            SELECT 
                gias.invite_id,
                
                -- 【分母】：当年分配到这个邀请的管理员里，现在还没退群、没被撤职的人数
                COUNT(gias.admin_id)::int AS eligible_admin_count,
                
                -- 【分子】：这些合法的管理员里，已经点了 'ignored' 的人数
                COUNT(CASE WHEN gias.state = 'ignored' THEN 1 END)::int AS ignored_count
                
            FROM group_invite_admin_state gias
            -- 核心联表：确保这个管理员现在依然在群里，且依然是管理层
            -- （因为你改成了物理删除，只要能 JOIN 到 conversation_member，就说明人还在）
            JOIN conversation_member cm 
              ON gias.admin_id = cm.member_user_id
              AND cm.conversation_id = $1
              AND cm.role IN ('owner', 'admin')
            WHERE gias.invite_id IN (
                SELECT invite_id FROM group_invite WHERE conversation_id = $1 AND status = 'pending'
            )
            GROUP BY gias.invite_id
        )
        -- 将符合条件的申请，批量置为 rejected
        UPDATE group_invite gi
        SET status = 'rejected'
        FROM invite_stats stats
        WHERE gi.invite_id = stats.invite_id
          AND stats.eligible_admin_count > 0 
          AND stats.ignored_count >= stats.eligible_admin_count
          AND gi.status = 'pending'
        RETURNING gi.invite_id;
    """

    records = await conn.fetch(query, conversation_id)
    return [record["invite_id"] for record in records]


async def db_update_group_name(
    conn: asyncpg.Connection,
    conversation_id: int,
    new_name: str
) -> None:
    """
    更新群聊名称
    """
    query = """
        UPDATE conversation
        SET conversation_name = $1
        WHERE conversation_id = $2 AND type = 'group';
    """
    await conn.execute(query, new_name, conversation_id)
