import asyncpg
from core.exceptions import FriendErrors, BusinessException

async def db_create_friend_request(conn: asyncpg.Connection, sender_id: int, receiver_id: int, message: str) -> bool:
    """
    发起好友申请 (对应 POST /api/friend/apply)
    """
    # 1. 防止自己加自己
    if sender_id == receiver_id:
        raise FriendErrors.CantAddSelf()
        
    # 2. 校验是否已经是好友
    is_already_friend = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM friend_relationship WHERE user_id = $1 AND friend_user_id = $2)",
        sender_id, receiver_id
    )
    if is_already_friend:
        raise FriendErrors.AlreadyFriends()
        
    # 3. 校验是否有待处理的申请 (双向拦截)
    has_pending = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM friend_request WHERE status = 'pending' AND ((sender_id = $1 AND receiver_id = $2) OR (sender_id = $2 AND receiver_id = $1)))",
        sender_id, receiver_id
    )
    if has_pending:
        raise FriendErrors.RequestPending()
    
    query = """
        INSERT INTO friend_request (sender_id, receiver_id, message)
        VALUES ($1, $2, $3)
        RETURNING request_id;
    """
    request_id = await conn.fetchval(query, sender_id, receiver_id, message)
    if not request_id:
        raise BusinessException(status_code=500, detail="系统异常，申请发送失败")
    return request_id

async def db_handle_friend_request(conn: asyncpg.Connection, request_id: int, action: str) -> bool:
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
        if not row:
            raise FriendErrors.RequestNotFound()
            
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

async def db_get_friend_list(conn: asyncpg.Connection, user_id: int) -> list[dict]:
    """
    获取好友列表及信息 (对应 GET /api/friend)
    需要联表查询 (JOIN) 拿到好友的具体信息（头像、昵称等）
    """
    query = """
        SELECT 
            u.user_id, u.username, u.avatar_url, 
            f.tag, f.create_time as be_friend_time
        FROM friend_relationship f
        JOIN user_account u ON f.friend_user_id = u.user_id
        WHERE f.user_id = $1;
    """
    rows = await conn.fetch(query, user_id)
    return [dict(row) for row in rows]

async def db_remove_friend(conn: asyncpg.Connection, user_id: int, friend_user_id: int) -> bool:
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