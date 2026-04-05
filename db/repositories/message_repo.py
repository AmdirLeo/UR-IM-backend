import asyncpg
import json
from datetime import datetime 
from core.exceptions import MessageException, MessageErrors

#Sonar
QUERY_CHECK_MEMBER_EXISTS = "SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2;"
QUERY_INSERT_MSG = """
    INSERT INTO message (sender_id, content, reply_to_id) 
    VALUES ($1, $2, $3) RETURNING msg_id, created_at;
"""

QUERY_LOCK_CONV = "SELECT 1 FROM conversation WHERE conversation_id = $1 FOR UPDATE;"

QUERY_GET_NEXT_SEQ = "SELECT COALESCE(MAX(seq_id), 0) + 1 FROM conversation_message WHERE conversation_id = $1;"

QUERY_INSERT_CONV_MSG = "INSERT INTO conversation_message (conversation_id, msg_id, seq_id) VALUES ($1, $2, $3);"

QUERY_UPDATE_CONV_SORT = """
    UPDATE conversation 
    SET last_msg_id = $1, last_msg_time = $2 
    WHERE conversation_id = $3;
"""

async def db_send_message(
    conn: asyncpg.Connection, 
    sender_id: int, 
    conversation_id: int, 
    msg_body: dict, 
    quote_id: int = None
) -> dict:
    """
    发送消息的核心逻辑：生成全局ID -> 生成会话Seq ID -> 更新置顶状态 -> 写扩散分发
    """
    
    # 1. 权限校验：你必须在这个会话里才能发消息
    check_member_query = """
        SELECT EXISTS(
            SELECT 1 FROM conversation_member 
            WHERE conversation_id = $1 AND member_user_id = $2
        );
    """
    is_member = await conn.fetchval(check_member_query, conversation_id, sender_id)
    if not is_member:
        # 对应 MessageErrors.NotInConversation
        raise MessageException(MessageErrors.NotInConversation)

    # 开启强事务，保证发消息的一致性
    async with conn.transaction():
        
        # 2. 插入消息本体 
        insert_msg_query = """
            INSERT INTO message (msg_body, quote_id) 
            VALUES ($1::jsonb) 
            RETURNING msg_id;
        """
        msg_id = await conn.fetchval(insert_msg_query, json.dumps(msg_body))
        
        if not msg_id:
            raise MessageException(MessageErrors.MessageNotFound)

        await conn.execute(QUERY_LOCK_CONV, conversation_id)  # 锁住会话，防止并发发消息导致 seq_id 冲突
        next_seq_id = await conn.fetchval(QUERY_GET_NEXT_SEQ, conversation_id)
        # 3. 插入会话消息映射表，并处理引用逻辑
        insert_conv_msg_query = """
            INSERT INTO conversation_message (conversation_id, msg_id, sender_id, seq_id)
            VALUES ($1, $2, $3, $4);
        """
        await conn.execute(insert_conv_msg_query, conversation_id, msg_id, sender_id, quote_id)
        
        if quote_id:
            update_quote_query = """
                UPDATE message 
                SET quote_count = quote_count + 1 
                WHERE msg_id = $1;
            """
            await conn.execute(update_quote_query, quote_id)
        await conn.execute(QUERY_UPDATE_CONV_SORT, msg_id, 'now()', conversation_id)  # 更新会话的 last_msg_id 和 last_msg_time，靠这个排序 

        # 4.获取会话所有成员，并批量写入收件箱
        get_members_query = "SELECT member_user_id FROM conversation_member WHERE conversation_id = $1;"
        members = await conn.fetch(get_members_query, conversation_id)
        
        if members:
            # 构建批量插入的数据结构: [(user1, conv, msg), (user2, conv, msg), ...]
            inbox_records = [
                (member['member_user_id'], conversation_id, msg_id) 
                for member in members
            ]
            
            insert_inbox_query = """
                INSERT INTO user_inbox (user_id, conversation_id, msg_id) 
                VALUES ($1, $2, $3);
            """
            await conn.executemany(insert_inbox_query, inbox_records)

    return {"msg_id": msg_id, "seq_id": next_seq_id}

async def db_get_all_unread_counts(conn: asyncpg.Connection, user_id: int) -> dict:
    """
    获取当前用户所有会话的未读消息数 (对应 GET /api/conversation/unread)
    返回格式: {conversation_id: unread_count, ...} 比如 {101: 5, 102: 12}
    """
    # 利用写扩散的收件箱，直接按会话分组统计 is_read = false 的数量
    query = """
        SELECT conversation_id, COUNT(msg_id) as unread_count
        FROM user_inbox
        WHERE user_id = $1 AND is_read = false
        GROUP BY conversation_id;
    """
    rows = await conn.fetch(query, user_id)
    
    # 转换成易于前端解析的字典格式
    # 如果没有任何未读消息，会直接返回一个空字典 {}
    return {row['conversation_id']: row['unread_count'] for row in rows}


async def db_mark_conversation_as_read(conn: asyncpg.Connection, user_id: int, conversation_id: int) -> None:
    """
    清除特定会话的未读红点（已读上报）
    需要同时更新 inbox 的状态和 member 表的 read_index 水位线
    """
    # 确保用户在这个会话里
    check_query = QUERY_CHECK_MEMBER_EXISTS
    if not await conn.fetchval(check_query, conversation_id, user_id):
        raise MessageException(MessageErrors.NotInConversation)

    async with conn.transaction():
        # 把收件箱里的该会话的所有未读消息标记为已读
        update_inbox_query = """
            UPDATE user_inbox 
            SET is_read = true 
            WHERE user_id = $1 AND conversation_id = $2 AND is_read = false;
        """
        await conn.execute(update_inbox_query, user_id, conversation_id)
        
        # 找到这个会话目前最大的 msg_id，更新给这个用户
        update_watermark_query = """
            UPDATE conversation_member
            SET read_index = (
                SELECT COALESCE(MAX(msg_id), 0) 
                FROM conversation_message 
                WHERE conversation_id = $2
            )
            WHERE conversation_id = $2 AND member_user_id = $1;
        """
        await conn.execute(update_watermark_query, user_id, conversation_id)

async def db_quote_message(
    conn: asyncpg.Connection, 
    sender_id: int, 
    conversation_id: int, 
    msg_body: dict, 
    quote_message_id: int
) -> dict:
    """
    引用特定消息并发送 (对应 POST /api/message/quote)
    """
    # 校验是否在群里
    is_member = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2)", conversation_id, sender_id)
    if not is_member:
        raise MessageException(MessageErrors.NotInConversation)

    # 校验被引用的消息是否存在于该会话中
    quote_exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM conversation_message WHERE conversation_id = $1 AND msg_id = $2)", conversation_id, quote_message_id)
    if not quote_exists:
        raise MessageException(MessageErrors.QuoteNotFound)

    # 复用之前的发送逻辑 (开启事务，插入 message，更新 quote_count，写扩散)
    return await db_send_message(conn, sender_id, conversation_id, msg_body, quote_id=quote_message_id)
async def db_get_message_history(
    conn: asyncpg.Connection, 
    user_id: int, 
    conversation_id: int, 
    cursor_msg_id: int = None, 
    limit: int = 20
) -> list[dict]:
    """
    基于游标拉取历史消息 / 离线消息 (对应 POST /api/message/offlinemsg 和 /history)
    
    参数:
        cursor_msg_id: 前端当前列表中最老（最上面）的一条消息的 msg_id。
                       如果不传 (None)，代表用户刚点开聊天框，拉取最新的一批。
        limit: 每次拉取的条数
    """
    
    # 1. 安全防线：必须是该会话的成员才能看聊天记录
    check_query = QUERY_CHECK_MEMBER_EXISTS
    if not await conn.fetchval(check_query, conversation_id, user_id):
        raise MessageException(MessageErrors.NotInConversation)

    # 2. 核心 SQL：联表查询拿到消息本体、发送者信息、引用信息
    base_query = """
        SELECT 
            cm.msg_id, 
            cm.sender_id, 
            u.username AS sender_name, 
            u.avatar_url AS sender_avatar, 
            m.msg_body, 
            cm.quote_id, 
            cm.quote_count, 
            cm.create_time
        FROM conversation_message cm
        JOIN message m ON cm.msg_id = m.msg_id
        JOIN user_account u ON cm.sender_id = u.user_id
    """
    
    # 3. 动态拼接游标条件
    if cursor_msg_id:
        # 向上滑动拉取更老的历史消息 (找比游标更小的 ID)
        query = base_query + """
            WHERE cm.conversation_id = $1 AND cm.msg_id < $3
            ORDER BY cm.msg_id DESC 
            LIMIT $2;
        """
        rows = await conn.fetch(query, conversation_id, limit, cursor_msg_id)
    else:
        # 第一次打开，没有游标，直接拉取最新的 limit 条
        query = base_query + """
            WHERE cm.conversation_id = $1
            ORDER BY cm.msg_id DESC 
            LIMIT $2;
        """
        rows = await conn.fetch(query, conversation_id, limit)

    # 4. 格式化返回
    # 注意：因为使用了 DESC 排序，拿到的列表是时间倒序的（最新的一条在 [0]）。
    return [dict(row) for row in rows]

async def db_delete_local_messages(conn: asyncpg.Connection, user_id: int, conversation_id: int, msg_ids: list[int]) -> None:
    """
    删除用户的本地聊天记录 (对应 DELETE /api/message/delete)
    注意：这只是从当前用户的收件箱中抹去记录，不影响真正的 message 实体和其他群成员。
    """
    if not msg_ids:
        return

    query = """
        DELETE FROM user_inbox 
        WHERE user_id = $1 AND conversation_id = $2 AND msg_id = ANY($3::bigint[]);
    """
    await conn.execute(query, user_id, conversation_id, msg_ids)

async def db_set_conversation_mute(conn: asyncpg.Connection, user_id: int, conversation_id: int, is_muted: bool) -> None:
    """设置消息免打扰 (对应 PUT /api/conversation/mute)"""
    query = "UPDATE conversation_member SET is_muted = $1 WHERE conversation_id = $2 AND member_user_id = $3;"
    status = await conn.execute(query, is_muted, conversation_id, user_id)
    if status != 'UPDATE 1':
        raise MessageException(MessageErrors.NotInConversation)

async def db_set_conversation_pin(conn: asyncpg.Connection, user_id: int, conversation_id: int, is_pinned: bool) -> None:
    """设置置顶会话 (对应 PUT /api/conversation/pin)"""
    query = "UPDATE conversation_member SET is_pinned = $1 WHERE conversation_id = $2 AND member_user_id = $3;"
    status = await conn.execute(query, is_pinned, conversation_id, user_id)
    if status != 'UPDATE 1':
        raise MessageException(MessageErrors.NotInConversation)


async def db_get_all_unread_counts_with_mute(conn: asyncpg.Connection, user_id: int) -> list[dict]:
    """
    获取用户的未读数列表（带上该群是否免打扰的标记，前端靠这个区分红点和灰点）
    """
    query = """
        SELECT 
            i.conversation_id, 
            COUNT(i.msg_id) as unread_count,
            m.is_muted,
            m.is_pinned
        FROM user_inbox i
        JOIN conversation_member m ON i.conversation_id = m.conversation_id AND i.user_id = m.member_user_id
        WHERE i.user_id = $1 AND i.is_read = false
        GROUP BY i.conversation_id, m.is_muted, m.is_pinned;
    """
    rows = await conn.fetch(query, user_id)
    return [dict(row) for row in rows]

async def db_filter_messages(
    conn: asyncpg.Connection,
    user_id: int,
    conversation_id: int,
    keyword: str |None = None,
    sender_id: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    cursor_msg_id: int | None = None,
    limit: int = 20
) -> list[dict]:
    """
    群聊消息全能筛选器 (支持动态条件 + 游标分页 + 尊重本地删除逻辑)
    """
    
    # 1. 基础查询：从当前用户的收件箱出发，连表查出全局消息和会话映射表(拿 seq_id)
    base_query = """
        SELECT 
            m.msg_id, 
            m.sender_id, 
            m.msg_body, 
            m.created_at,
            cm.seq_id,
            m.reply_to_id
        FROM user_inbox ui
        JOIN message m ON ui.msg_id = m.msg_id
        JOIN conversation_message cm ON m.msg_id = cm.msg_id AND cm.conversation_id = ui.conversation_id
        WHERE ui.user_id = $1 AND ui.conversation_id = $2
    """
    
    # 前两个参数已经固定
    params = [user_id, conversation_id]
    conditions = []
    
    # 2. 动态拼接筛选条件 (核心魔法，绝对防 SQL 注入)
    # len(params) + 1 就是下一个 $N 的占位符编号
    
    # A. 关键词模糊匹配 (针对 JSONB 里的 text 字段)
    if keyword:
        params.append(f"%{keyword}%")  # PostgreSQL 的 LIKE 语法需要加 %
        # 使用 ->> 提取 JSONB 中的字符串进行模糊匹配
        conditions.append(f"m.msg_body->>'text' ILIKE ${len(params)}")

    # B. 发送者筛选
    if sender_id is not None:
        params.append(sender_id)
        conditions.append(f"cm.sender_id = ${len(params)}")

    # C. 时间段筛选 (开始时间)
    if start_time:
        params.append(start_time)
        conditions.append(f"m.created_at >= ${len(params)}")

    # D. 时间段筛选 (结束时间)
    if end_time:
        params.append(end_time)
        conditions.append(f"m.created_at <= ${len(params)}")
        
    # E. 游标分页 (极其重要，滑动加载历史搜索结果)
    if cursor_msg_id:
        params.append(cursor_msg_id)
        conditions.append(f"m.msg_id < ${len(params)}")

    # 3. 组装最终的 SQL 语句
    if conditions:
        # 把动态条件用 AND 连起来拼接到基础 SQL 后面
        final_query = base_query + " AND " + " AND ".join(conditions)
    else:
        final_query = base_query
        
    # 加上强制排序和分页截断
    final_query += f" ORDER BY m.msg_id DESC LIMIT {limit};"

    # 4. 执行极其安全的参数化查询
    records = await conn.fetch(final_query, *params)
    
    # 5. 格式化返回值
    result = []
    for row in records:
        try:
            body_dict = json.loads(row['msg_body']) if isinstance(row['msg_body'], str) else row['msg_body']
        except Exception:
            body_dict = {"text": "[解析错误]"}
            
        result.append({
            "msg_id": row['msg_id'],
            "seq_id": row['seq_id'],  # 把后端强校验的 seq_id 传回给前端
            "sender_id": row['sender_id'],
            "msg_body": body_dict,
            "created_at": row['created_at'].isoformat(),
            "reply_to_id": row['reply_to_id']
        })
        
    return result

