import asyncpg

async def create_user(conn: asyncpg.Connection, username: str, password_hash: str, email: str) -> int:
    """
    创建一个新用户
    返回新创建的 user_id
    """
    # 在 PostgreSQL 中，占位符是 $1, $2, $3... 而不是 MySQL 的 %s
    query = """
        INSERT INTO user_account (username, password, email)
        VALUES ($1, $2, $3)
        RETURNING user_id;
    """
    # fetchval 用于执行 INSERT 并直接拿回 RETURNING 返回的那个单值（即 user_id）
    user_id = await conn.fetchval(query, username, password_hash, email)
    return user_id

async def get_user_by_email(conn: asyncpg.Connection, email: str) -> dict | None:
    """
    通过邮箱查找用户（主要用于登录时校验密码，或注册时检查邮箱是否已存在）
    """
    query = "SELECT * FROM user_account WHERE email = $1;"
    # fetchrow 用于获取单行记录
    row = await conn.fetchrow(query, email)
    return dict(row) if row else None

async def get_user_by_id(conn: asyncpg.Connection, user_id: int) -> dict | None:
    """
    通过 ID 获取用户信息（用于展示个人主页）
    注意：这里刻意没有 SELECT password 字段，防止密码哈希被意外泄露给前端
    """
    query = """
        SELECT user_id, username, email, avatar_url, register_time, login_time
        FROM user_account
        WHERE user_id = $1;
    """
    row = await conn.fetchrow(query, user_id)
    return dict(row) if row else None

async def update_user_login_time(conn: asyncpg.Connection, user_id: int):
    """
    更新用户的最后登录时间
    """
    query = """
        UPDATE user_account 
        SET login_time = CURRENT_TIMESTAMP 
        WHERE user_id = $1;
    """
    await conn.execute(query, user_id)

async def delete_user(conn: asyncpg.Connection, user_id: int) -> bool:
    """
    注销用户账号。
    得益于建表时的 ON DELETE CASCADE 机制，
    删除此行会自动清理 friend_relationship, conversation_member, user_inbox 等表中的关联数据。
    """
    query = "DELETE FROM user_account WHERE user_id = $1;"
    
    # execute 返回的是命令状态字符串，例如成功删除了1行会返回 'DELETE 1'
    status = await conn.execute(query, user_id)
    
    # 如果状态字符串包含 'DELETE 1'，说明真的删掉了一个用户
    return status == 'DELETE 1'

async def update_user_password(conn: asyncpg.Connection, user_id: int, new_password_hash: str) -> bool:
    """
    专门用于修改密码（对应忘记密码或主动修改密码接口）
    """
    query = "UPDATE user_account SET password = $1 WHERE user_id = $2;"
    status = await conn.execute(query, new_password_hash, user_id)
    return status == 'UPDATE 1'
async def update_user_profile(
    conn: asyncpg.Connection, 
    user_id: int, 
    username: str = None, 
    email: str = None, 
    avatar_url: str = None
) -> bool:
    """
    通用的资料修改接口（对应 /api/user/edit 系列接口）
    动态拼接 SQL，只更新传入了非空值的字段
    """
    # 动态构建 UPDATE 语句的技巧
    updates = []
    values = []
    
    if username is not None:
        values.append(username)
        updates.append(f"username = ${len(values)}")
    if email is not None:
        values.append(email)
        updates.append(f"email = ${len(values)}")
    if avatar_url is not None:
        values.append(avatar_url)
        updates.append(f"avatar_url = ${len(values)}")
        
    if not updates:
        return False # 没有任何字段需要更新

    values.append(user_id)
    query = f"""
        UPDATE user_account 
        SET {', '.join(updates)} 
        WHERE user_id = ${len(values)};
    """
    status = await conn.execute(query, *values)
    return status == 'UPDATE 1'

async def search_users(conn: asyncpg.Connection, keyword: str) -> list[dict]:
    """
    通过用户名或邮箱模糊查找用户
    返回脱敏后的信息列表（用户名，id，头像url）
    """
    query = """
        SELECT user_id, username, avatar_url 
        FROM user_account 
        WHERE username ILIKE $1 OR email ILIKE $1
        LIMIT 20; -- 限制返回数量，防止恶意查询拖垮数据库
    """
    # 拼接模糊查询的通配符 %
    search_pattern = f"%{keyword}%"
    rows = await conn.fetch(query, search_pattern)
    return [dict(row) for row in rows]