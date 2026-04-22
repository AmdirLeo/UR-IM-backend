import asyncpg
from core.exceptions import FriendErrors, BusinessException, FriendException
from typing import Optional, List, Dict, Any

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

ERR_TAG_NOT_FOUND = "分组不存在"
ERR_TAG_CONFLICT = "该分组已存在"


async def db_create_friend_request(
    conn: asyncpg.Connection, sender_id: int, receiver_id: int, message: str
) -> int:
    """
    发起好友申请 (对应 POST /api/friend/apply)
    """

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
    conn: asyncpg.Connection, request_id: int, current_user_id: int, action: str
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
        sender_id = await conn.fetchval(
            update_query, action, request_id, current_user_id
        )

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

            # 💡 使用 GROUP BY 精准查找历史私聊会话
            find_conv_query = """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm ON c.conversation_id = cm.conversation_id
                WHERE c.type = 'private'
                  AND cm.member_user_id IN ($1, $2)
                GROUP BY c.conversation_id
                HAVING COUNT(DISTINCT cm.member_user_id) = 2;
            """
            conv_id = await conn.fetchval(find_conv_query, current_user_id, sender_id)

            if conv_id:
                # ==========================================
                # 【核心修复】：复用旧会话！把双方的 is_active 都恢复成 true
                # ==========================================
                await conn.execute("""
                    UPDATE conversation_member
                    SET is_active = true
                    WHERE conversation_id = $1 AND member_user_id IN ($2, $3);
                """, conv_id, current_user_id, sender_id)

            else:
                new_conv_id = await conn.fetchval(
                    "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;"
                )
                await conn.execute(
                    "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, $3);",
                    new_conv_id,
                    current_user_id,
                    sender_id,
                )
                conv_id = new_conv_id

            return {
                "status": "success",
                "friend_id": sender_id,
                "conversation_id": conv_id,
            }

    return {"status": action, "friend_id": sender_id}


async def db_get_friend_requests(
    conn: asyncpg.Connection,
    user_id: int,
    cursor_req_id: int | None = None,
    limit: int = 20
) -> list[dict]:
    """
    获取当前用户的所有好友申请记录（包含我发出的 + 我收到的，游标分页）
    """

    # 核心 SQL：动态判断方向，并始终 JOIN “对方”的账户信息
    base_query = """
        SELECT
            fr.request_id,
            fr.reason,
            fr.status,
            fr.create_time,

            -- 【魔法 1：判断方向】
            CASE
                WHEN fr.sender_id = $1 THEN 'outbound'
                ELSE 'inbound'
            END AS direction,

            -- 【魔法 2：获取对方 ID】我发的对方就是 receiver，别人发给我的对方就是 sender
            CASE
                WHEN fr.sender_id = $1 THEN fr.receiver_id
                ELSE fr.sender_id
            END AS target_user_id,

            u.username AS target_user_name,
            u.avatar_url AS target_user_avatar

        FROM friend_request fr
        -- 根据魔法 2 的逻辑，精准 JOIN 对方的用户表
        JOIN user_account u ON u.user_id = (
            CASE WHEN fr.sender_id = $1 THEN fr.receiver_id ELSE fr.sender_id END
        )
        WHERE (fr.sender_id = $1 OR fr.receiver_id = $1)
    """

    # 动态拼接游标
    if cursor_req_id:
        query = (
            base_query +
            " AND fr.request_id < $2 ORDER BY fr.request_id DESC LIMIT $3;")
        rows = await conn.fetch(query, user_id, cursor_req_id, limit)
    else:
        query = base_query + " ORDER BY fr.request_id DESC LIMIT $2;"
        rows = await conn.fetch(query, user_id, limit)

    # 格式化返回：参考你的原格式，但将 sender 统一升级为 target，并增加 direction
    result = []
    for row in rows:
        result.append({
            "request_id": row['request_id'],
            # 新增：'inbound' (收到) 或 'outbound' (发出)
            "direction": row['direction'],
            "target_user_id": row['target_user_id'],     # 替代原 sender_id
            "target_user_name": row['target_user_name'],  # 替代原 sender_name
            # 替代原 sender_avatar
            "target_user_avatar": row['target_user_avatar'],
            "reason": row['reason'],
            "status": row['status'],
            "create_time": row['create_time']            # datetime 对象
        })

    return result


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
    conn: asyncpg.Connection,
    user_id: int,
    friend_user_id: int,
    delete_history: bool
) -> Optional[int]:  # 👈 注意修改返回类型
    """
    删除好友 (对应 DELETE /api/friend/remove)
    逻辑重构：
    1. 斩断好友关系
    2. 清理可能遗留的申请记录
    2. 逻辑隐藏会话 (is_active = false)
    3. 根据参数决定是否物理清空个人收件箱
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
        direct_conv_id = await conn.fetchval(
            QUERY_FIND_DIRECT_CONV, user_id, friend_user_id
        )

        if direct_conv_id:
            # 4. 【核心改动】逻辑删除成员状态，并通过推进 read_index 抹平未读数
            await conn.execute("""
                UPDATE conversation_member
                SET is_active = false,
                    read_index = COALESCE((SELECT last_msg_id FROM conversation WHERE conversation_id = $1), 0)
                WHERE conversation_id = $1 AND member_user_id = $2
            """, direct_conv_id, user_id)
            # 5. 【清空历史记录】
            if delete_history:
                # 💡 只删操作者自己 (user_id) 的收件箱，绝不影响对方 (friend_user_id) 的消息记录！
                await conn.execute("""
                    DELETE FROM user_inbox
                    WHERE conversation_id = $1 AND user_id = $2
                """, direct_conv_id, user_id)
        return direct_conv_id


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
        raise BusinessException(status_code=409, detail=ERR_TAG_CONFLICT)


async def db_delete_friend_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str
) -> None:
    """
    删除好友分组 (对应 POST /api/friend/tag/delete)
    """
    query = "DELETE FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2;"
    status = await conn.execute(query, user_id, tag_name)
    if status != DELETE_ONE:
        raise BusinessException(status_code=404, detail=ERR_TAG_NOT_FOUND)


async def db_get_friend_tags(
    conn: asyncpg.Connection, user_id: int
) -> list[str]:
    """
    获取好友分组/标签列表 (对应 GET /api/friend/tag/list)
    """
    query = "SELECT tag_name FROM user_friend_tag WHERE user_id = $1 ORDER BY tag_name;"

    # fetch 返回的是一个 asyncpg.Record 的列表
    records = await conn.fetch(query, user_id)

    # 提取 tag_name 组成一个普通的 Python 字符串列表
    return [record["tag_name"] for record in records]


async def db_add_friends_to_tag(
    conn: asyncpg.Connection, user_id: int, tag_name: str, friend_ids: list[int]
) -> None:
    check_tag = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2)",
        user_id,
        tag_name,
    )
    if not check_tag:
        raise BusinessException(status_code=404, detail=ERR_TAG_NOT_FOUND)

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
    # 1. 先查这个分组到底存不存在
    tag_exists = await conn.fetchval(
        "SELECT 1 FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2",
        user_id, tag_name
    )
    if not tag_exists:
        raise BusinessException(status_code=404, detail=ERR_TAG_NOT_FOUND)
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


async def db_get_pending_request_count(conn: asyncpg.Connection, user_id: int) -> int:
    """
    查询指定用户当前未处理的好友申请数量
    """
    query = """
        SELECT COUNT(1)
        FROM friend_request
        WHERE receiver_id = $1 AND status = 'pending';
    """
    # fetchval 专门用来获取单行单列的单一值，非常适合 COUNT() 查询
    count = await conn.fetchval(query, user_id)

    return count or 0


async def db_get_pending_friend_requests(
    conn: asyncpg.Connection,
    user_id: int
) -> List[Dict[str, Any]]:
    """
    从数据库查询指定用户收到的所有待处理好友申请。
    返回包含申请详情和发送者信息的列表。
    """
    rows = await conn.fetch("""
        SELECT
            fr.request_id,
            fr.sender_id,
            u.username AS sender_name,
            u.avatar_url AS sender_avatar,
            fr.message,
            fr.create_time
        FROM friend_request fr
        JOIN user_account u ON fr.sender_id = u.user_id
        WHERE fr.receiver_id = $1
          AND fr.status = 'pending'
        ORDER BY fr.create_time DESC
    """, user_id)

    # 将 asyncpg.Record 转换为普通字典列表
    result = []
    for row in rows:
        result.append({
            "request_id": row["request_id"],
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"],
            "sender_avatar": row["sender_avatar"],
            "message": row["message"],
            "create_time": row["create_time"],  # 保留 datetime 对象，服务层再转换
        })
    return result
