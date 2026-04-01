import asyncpg
from core.exceptions import FriendErrors, BusinessException, FriendException

#Sonar
PG_DELETE_SUCCESS_TAG = 'DELETE 1'

async def db_create_friend_request(conn: asyncpg.Connection, sender_id: int, receiver_id: int, message: str) -> int:
    """
    发起好友申请 (对应 POST /api/friend/apply)
    """
    # 1. 防止自己加自己
    if sender_id == receiver_id:
        raise FriendException(FriendErrors.CantAddSelf)
        
    # 2. 校验是否已经是好友
    is_already_friend = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM friend_relationship WHERE user_id = $1 AND friend_user_id = $2)",
        sender_id, receiver_id
    )
    if is_already_friend:
        raise FriendException(FriendErrors.AlreadyFriends)
        
    # 3. 校验是否有待处理的申请 (双向拦截)
    has_pending = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM friend_request WHERE status = 'pending' AND ((sender_id = $1 AND receiver_id = $2) OR (sender_id = $2 AND receiver_id = $1)))",
        sender_id, receiver_id
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

async def db_handle_friend_request(conn: asyncpg.Connection, request_id: int, current_user_id: int, action: str) -> int:
    """
    处理好友申请 (对应 PUT /api/friend/handle)
    action 必须是 'accepted' 或 'rejected'
    """
    if action not in ('accepted', 'rejected'):
        raise BusinessException(status_code=400, detail="无效的操作类型")

    # asyncpg 开启事务的语法
    async with conn.transaction():
        # 1. 更新申请状态，并把申请人和接收人的 ID 拿出来
        update_query = """
            UPDATE friend_request 
            SET status = $1 
            WHERE request_id = $2 AND status = 'pending'
            RETURNING sender_id, receiver_id;
        """
        row = await conn.fetchrow(update_query, action, request_id)
        
        # 如果申请不存在或已经被处理过
        if not row or row['status'] != 'pending':
            raise FriendException(FriendErrors.RequestNotFound)
        

        if row['receiver_id'] != current_user_id:
            raise BusinessException(status_code=403, detail="无权处理他人的好友申请")
        await conn.execute(
            "UPDATE friend_request SET status = $1 WHERE request_id = $2",
            action, request_id
        )

        # 2. 如果是同意，则插入双向好友记录
        if action == 'accepted':
            sender_id, receiver_id = row['sender_id'], row['receiver_id']
            insert_query = """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1)
                -- 防止重复插入报错
                ON CONFLICT (user_id, friend_user_id) DO NOTHING;
            """
            await conn.execute(insert_query, sender_id, receiver_id)

        return row['receiver_id']
    
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

async def db_remove_friend(conn: asyncpg.Connection, user_id: int, friend_user_id: int) -> None:
    """
    删除好友 (对应 DELETE /api/friend/remove)
    需要同时斩断双向联系
    """
    query = """
        DELETE FROM friend_relationship 
        WHERE (user_id = $1 AND friend_user_id = $2)
           OR (user_id = $2 AND friend_user_id = $1);
    """
    status = await conn.execute(query, user_id, friend_user_id)
    if status not in ('DELETE 1', 'DELETE 2'):
        raise BusinessException(status_code=404, detail="好友关系不存在")

async def db_create_friend_tag(conn: asyncpg.Connection, user_id: int, tag_name: str) -> None:
    """新建好友分组 (对应 POST /api/friend/tag/new)"""
    query = """
        INSERT INTO user_friend_tag (user_id, tag_name) 
        VALUES ($1, $2)
        ON CONFLICT (user_id, tag_name) DO NOTHING;
    """
    status = await conn.execute(query, user_id, tag_name)
    if status != 'INSERT 0 1':
        raise BusinessException(status_code=409, detail="该分组已存在")

async def db_delete_friend_tag(conn: asyncpg.Connection, user_id: int, tag_name: str) -> None:
    """
    删除好友分组 (对应 POST /api/friend/tag/delete)
    """
    query = "DELETE FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2;"
    status = await conn.execute(query, user_id, tag_name)
    if status != PG_DELETE_SUCCESS_TAG:
        raise BusinessException(status_code=404, detail="分组不存在")

async def db_add_friends_to_tag(conn: asyncpg.Connection, user_id: int, tag_name: str, friend_ids: list[int]) -> None:
    check_tag = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM user_friend_tag WHERE user_id = $1 AND tag_name = $2)", 
        user_id, tag_name
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

async def db_get_friends_by_tag(conn: asyncpg.Connection, user_id: int, tag_name: str) -> list[dict]:
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

async def db_remove_friend_from_tag(conn: asyncpg.Connection, user_id: int, friend_user_id: int, tag_name: str) -> None:
    """
    将某个好友移出该分组 (对应 POST /api/friend/tag/remove)
    """
    query = """
        DELETE FROM friend_tag_mapping 
        WHERE user_id = $1 AND friend_user_id = $2 AND tag_name = $3;
    """
    status = await conn.execute(query, user_id, friend_user_id, tag_name)
    if status != PG_DELETE_SUCCESS_TAG:
        raise BusinessException(status_code=404, detail="该好友不在当前分组中")