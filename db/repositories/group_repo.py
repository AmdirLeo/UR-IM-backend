import asyncpg
from exceptions import GroupException, GroupErrors

async def db_create_group(conn: asyncpg.Connection, creator_id: int, member_ids: list[int], group_name: str = "未命名群聊") -> int:
    """
    创建群聊 (对应 POST /api/group/create)
    将创建者设为 owner，其他好友设为 member
    """
    async with conn.transaction():
        # 1. 插入会话基础信息
        query_conv = "INSERT INTO conversation (type, conversation_name) VALUES ('group', $1) RETURNING conversation_id;"
        conv_id = await conn.fetchval(query_conv, group_name)

        # 2. 插入群主 (owner)
        query_owner = "INSERT INTO conversation_member (conversation_id, member_user_id, role) VALUES ($1, $2, 'owner');"
        await conn.execute(query_owner, conv_id, creator_id)

        # 3. 批量插入普通群员
        # 去重，并且把群主自己从列表里剔除（防止前端传错导致主键冲突）
        actual_members = list(set(member_ids) - {creator_id})
        if actual_members:
            records = [(conv_id, uid, 'member') for uid in actual_members]
            query_members = "INSERT INTO conversation_member (conversation_id, member_user_id, role) VALUES ($1, $2, $3);"
            await conn.executemany(query_members, records)

    return conv_id


async def db_get_group_info(conn: asyncpg.Connection, user_id: int, conversation_id: int) -> dict:
    """
    获取群详细信息 (对应 POST /api/group/info)
    包含群基础信息、群成员列表和历史公告
    """
    # 1. 鉴权：只有群成员能看群信息
    check_role = await conn.fetchval("SELECT role FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;", conversation_id, user_id)
    if not check_role:
        raise GroupException(GroupErrors.NotInGroup)

    # 2. 拉取群基础信息
    info = dict(await conn.fetchrow("SELECT conversation_id, conversation_name, create_time FROM conversation WHERE conversation_id = $1;", conversation_id))
    
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
    info['members'] = [dict(row) for row in await conn.fetch(query_members, conversation_id)]

    # 4. 拉取历史公告
    query_announcements = """
        SELECT a.announcement_id, a.content, a.is_pinned, a.create_time, u.username as sender_name
        FROM group_announcement a
        JOIN user_account u ON a.sender_id = u.user_id
        WHERE a.conversation_id = $1
        ORDER BY a.is_pinned DESC, a.create_time DESC;
    """
    info['announcements'] = [dict(row) for row in await conn.fetch(query_announcements, conversation_id)]

    return info


async def db_quit_group(conn: asyncpg.Connection, user_id: int, conversation_id: int) -> None:
    """
    退出群聊 (对应 POST /api/group/quit)
    """
    role = await conn.fetchval("SELECT role FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;", conversation_id, user_id)
    if not role:
        raise GroupException(GroupErrors.NotInGroup)
    
    # 群主不能退群
    if role == 'owner':
        raise GroupException(GroupErrors.OwnerCannotQuit)

    await conn.execute("DELETE FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;", conversation_id, user_id)


async def db_disband_group(conn: asyncpg.Connection, user_id: int, conversation_id: int) -> None:
    """
    解散群聊 (对应 POST /api/group/bomb)
    """
    role = await conn.fetchval("SELECT role FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;", conversation_id, user_id)
    if role != 'owner':
        raise GroupException(GroupErrors.PermissionDenied)
    await conn.execute("DELETE FROM conversation WHERE conversation_id = $1;", conversation_id)

