import asyncpg
from core.exceptions import FriendErrors, BusinessException, FriendException

# Constants for database operation status responses
DELETE_ONE = "DELETE 1"
DELETE_TWO = "DELETE 2"
INSERT_ONE = "INSERT 0 1"
QUERY_DELETE_FRIEND = """
    DELETE FROM friend_relationship 
    WHERE (user_id = $1 AND friend_user_id = $2) 
       OR (user_id = $2 AND friend_user_id = $1);
"""
QUERY_DELETE_REQUESTS = """
    DELETE FROM friend_request 
    WHERE (sender_id = $1 AND receiver_id = $2) 
       OR (sender_id = $2 AND receiver_id = $1);
"""
QUERY_FIND_DIRECT_CONV = """
    SELECT c.conversation_id 
    FROM conversation c
    JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
    JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
    WHERE c.type = 'private' 
      AND cm1.member_user_id = $1 
      AND cm2.member_user_id = $2;
"""
QUERY_WIPE_INBOX = """
    DELETE FROM user_inbox 
    WHERE conversation_id = $1;
"""


async def db_create_friend_request(
    conn: asyncpg.Connection, sender_id: int, receiver_id: int, message: str
) -> int:
    """
    发起好友申请 (对应 POST /api/friend/apply)
    """
    # 1. 防止自己加自己
    if sender_id == receiver_id:
        raise FriendException(FriendErrors.CantAddSelf)

    # 2. 校验是否已经是好友
    is_already_friend = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM friend_relationship WHERE user_id = $1 AND friend_user_id = $2)",
        sender_id,
        receiver_id,
    )
    if is_already_friend:
        raise FriendException(FriendErrors.AlreadyFriends)

    # 3. 校验是否有待处理的申请 (双向拦截)
    has_pending = await conn.fetchval(
        """
        SELECT EXISTS(
            SELECT 1 FROM friend_request
            WHERE status = 'pending'
            AND (
                (sender_id = $1 AND receiver_id = $2)
                OR (sender_id = $2 AND receiver_id = $1)
            )
        )
        """,
        sender_id,
        receiver_id,
    )
    if has_pending:
        raise FriendException(FriendErrors.RequestPending)

    query = """
        INSERT INTO friend_request (sender_id, receiver_id, message)
        VALUES ($1, $2, $3)
        RETURNING request_id;
    """
    request_id = await conn.fetchval(query, sender_id, receiver_id, message)
    if not request_id:
        raise BusinessException(status_code=500, detail="系统异常，申请发送失败")
    return request_id


async def db_handle_friend_request(
    conn: asyncpg.Connection, 
    request_id: int, 
    current_user_id: int, 
    action: str
) -> dict:
    if action not in ("accepted", "rejected"):
        raise FriendException(FriendErrors.InvalidAction)
    async with conn.transaction():
        # 1. 鉴权并更新状态
        update_query = """
            UPDATE friend_request
            SET status = $1
            WHERE request_id = $2 
              AND receiver_id = $3 
              AND status = 'pending'
            RETURNING sender_id;
        """
        sender_id = await conn.fetchval(update_query, action, request_id, current_user_id)

        if not sender_id:
            raise FriendException(FriendErrors.RequestNotFound)
        # 2. 如果是同意，执行初始化逻辑
        if action == "accepted":
            insert_friend_query = """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1)
                ON CONFLICT (user_id, friend_user_id) DO NOTHING;
            """
            await conn.execute(insert_friend_query, current_user_id, sender_id)

            find_conv_query = """
                SELECT c.conversation_id 
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private' 
                  AND cm1.member_user_id = $1 
                  AND cm2.member_user_id = $2;
            """
            conv_id = await conn.fetchval(find_conv_query, current_user_id, sender_id)

            if not conv_id:
                new_conv_id = await conn.fetchval(
                    "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;"
                )
                await conn.execute(
                    "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, $3);",
                    new_conv_id, current_user_id, sender_id
                )
                conv_id = new_conv_id

            return {
                "status": "success",
                "friend_id": sender_id,
                "conversation_id": conv_id
            }

    return {"status": action, "friend_id": sender_id}


async def db_get_friend_list(conn: asyncpg.Connection, user_id: int) -> list[dict]:
    """
    获取好友列表及信息 (对应 GET /api/friend)
    需要联表查询 (JOIN) 拿到好友的具体信息（头像、昵称等）
    """
    query = """
        SELECT
            u.user_id, u.username, u.avatar_url, f.create_time as be_friend_time,
            COALESCE(array_agg(m.tag_name) FILTER (WHERE m.tag_name IS NOT NULL), '{}') as tags
        FROM friend_relationship f
        JOIN user_account u ON f.friend_user_id = u.user_id
        LEFT JOIN friend_tag_mapping m ON f.user_id = m.user_id AND f.friend_user_id = m.friend_user_id
        WHERE f.user_id = $1
        GROUP BY u.user_id, u.username, u.avatar_url, f.create_time;
    """
    rows = await conn.fetch(query, user_id)
    return [dict(row) for row in rows]


async def db_remove_friend(
    conn: asyncpg.Connection, user_id: int, friend_user_id: int
) -> None:
    """
    删除好友 (对应 DELETE /api/friend/remove)
    需要同时斩断双向联系
    """
    async with conn.transaction():
        
        # 第一步：物理斩断好友关系
        delete_status = await conn.execute(QUERY_DELETE_FRIEND, user_id, friend_user_id)
        # 如果发现删除了 0 行，说明他们根本不是好友！
        if delete_status == "DELETE 0":
            raise FriendException(FriendErrors.FriendNotFound)
        
        # 第二步：清理可能遗留的未处理申请记录
        await conn.execute(QUERY_DELETE_REQUESTS, user_id, friend_user_id)
        
        # 第三步：精准找到属于他们两人的“私聊房间”
        direct_conv_id = await conn.fetchval(QUERY_FIND_DIRECT_CONV, user_id, friend_user_id)
        
        # 第四步：如果他们曾经聊过天，直接炸毁这个房间对应的所有收件箱记录
        if direct_conv_id:
            await conn.execute(QUERY_WIPE_INBOX, direct_conv_id)



async def db_create_friend_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str
) -> None:
    """新建好友分组 (对应 POST /api/friend/tag/new)"""
    query = """
        INSERT INTO user_friend_tag (user_id, tag_name)
        VALUES ($1, $2)
        ON CONFLICT (user_id, tag_name) DO NOTHING;
    """
    status = await conn.execute(query, user_id, tag_name)
    if status != INSERT_ONE:
        raise BusinessException(status_code=409, detail="该分组已存在")


async def db_delete_friend_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str
) -> None:
    """
    删除好友分组 (对应 POST /api/friend/tag/delete)
    """
    query = "DELETE FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2;"
    status = await conn.execute(query, user_id, tag_name)
    if status != DELETE_ONE:
        raise BusinessException(status_code=404, detail="分组不存在")


async def db_add_friends_to_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str, friend_ids: list[int]
) -> None:
    check_tag = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2)",
        user_id,
        tag_name,
    )
    if not check_tag:
        raise BusinessException(status_code=404, detail="分组不存在")

    # 组装批量插入的数据: [(user_id, friend_id_1, tag), (user_id, friend_id_2, tag)...]
    records = [(user_id, fid, tag_name) for fid in friend_ids]

    query = """
        INSERT INTO friend_tag_mapping (user_id, friend_user_id, tag_name)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING; -- 如果已经在这个分组里了，就忽略
    """
    # executemany 是批量插入的神器
    await conn.executemany(query, records)


async def db_get_friends_by_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str
) -> list[dict]:
    """
    获取某个分组下的所有好友信息 (对应 POST /api/friend/tag/query)
    """
    query = """
        SELECT u.user_id, u.username, u.avatar_url
        FROM friend_tag_mapping m
        JOIN user_account u ON m.friend_user_id = u.user_id
        WHERE m.user_id = $1 AND m.tag_name = $2;
    """
    rows = await conn.fetch(query, user_id, tag_name)
    return [dict(row) for row in rows]


async def db_remove_friend_from_tag(
    conn: asyncpg.Connection, user_id: int, friend_user_id: int, tag_name: str
) -> None:
    """
    将某个好友移出该分组 (对应 POST /api/friend/tag/remove)
    """
    query = """
        DELETE FROM friend_tag_mapping
        WHERE user_id = $1 AND friend_user_id = $2 AND tag_name = $3;
    """
    status = await conn.execute(query, user_id, friend_user_id, tag_name)
    if status != DELETE_ONE:
        raise BusinessException(status_code=404, detail="该好友不在当前分组中")
