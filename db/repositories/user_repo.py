import asyncpg
from core.exceptions import UserErrors, UserException
from typing import Optional


async def db_create_user(conn: asyncpg.Connection, username: str, password_hash: str, email: str) -> int:
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
    try:
        user_id = await conn.fetchval(query, username, password_hash, email)
        if user_id is None:
            raise UserException(UserErrors.NotFound)
        return user_id
    except asyncpg.exceptions.UniqueViolationError:
        # 捕获数据库层面的唯一性冲突（邮箱重复注册）
        raise UserException(UserErrors.AlreadyExists)


async def db_get_user_by_email(conn: asyncpg.Connection, email: str) -> dict | None:
    """
    通过邮箱查找用户（主要用于登录时校验密码，或注册时检查邮箱是否已存在）
    """
    query = "SELECT * FROM user_account WHERE email = $1;"
    # fetchrow 用于获取单行记录
    row = await conn.fetchrow(query, email)
    return dict(row) if row else None


async def db_get_user_by_id(conn: asyncpg.Connection, user_id: int) -> dict | None:
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
    if not row:
        raise UserException(UserErrors.NotFound)
    return dict(row)


async def db_get_password_by_id(conn: asyncpg.Connection, user_id: int) -> str | None:
    """
    通过 ID 获取用户的密码哈希（仅用于登录时验证密码）
    注意：这个函数只返回 password 字段，其他信息都不返回
    """
    query = "SELECT password FROM user_account WHERE user_id = $1;"
    password_hash = await conn.fetchval(query, user_id)
    return password_hash


async def db_update_user_login_time(conn: asyncpg.Connection, user_id: int):
    """
    更新用户的最后登录时间
    """
    query = """
        UPDATE user_account
        SET login_time = CURRENT_TIMESTAMP
        WHERE user_id = $1;
    """
    await conn.execute(query, user_id)


async def db_delete_user(conn: asyncpg.Connection, user_id: int):
    """
    注销用户账号。
    得益于建表时的 ON DELETE CASCADE 机制，
    删除此行会自动清理 friend_relationship, conversation_member, user_inbox 等表中的关联数据。
    """
    query = "DELETE FROM user_account WHERE user_id = $1;"

    # execute 返回的是命令状态字符串，例如成功删除了1行会返回 'DELETE 1'
    status = await conn.execute(query, user_id)

    if status != "DELETE 1":
        raise UserException(UserErrors.NotFound)


async def db_update_user_password(conn: asyncpg.Connection, user_id: int, new_password_hash: str):
    """
    专门用于修改密码（对应忘记密码或主动修改密码接口）
    """
    query = "UPDATE user_account SET password = $1 WHERE user_id = $2;"
    status = await conn.execute(query, new_password_hash, user_id)
    return status == "UPDATE 1"


async def db_update_user_profile(
    conn: asyncpg.Connection,
    user_id: int,
    username: Optional[str] = None,
    email: Optional[str] = None,
    avatar_url: Optional[str] = None,
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
        return False  # 没有任何字段需要更新

    values.append(user_id)
    query = f"""
        UPDATE user_account
        SET {', '.join(updates)}
        WHERE user_id = ${len(values)};
    """
    status = await conn.execute(query, *values)
    return status == "UPDATE 1"


async def db_search_users(conn: asyncpg.Connection, keyword: str, page: int = 1, page_size: int = 20) -> dict:
    """
    通过用户名模糊查找用户 (支持分页)

    参数:
        keyword: 搜索关键字
        page: 当前页码 (从 1 开始)
        page_size: 每页显示的条数

    返回:
        包含当前页数据 (items) 和总匹配人数 (total) 的字典
    """
    # 1. 计算需要跳过的记录数 (OFFSET)
    # 比如：第 1 页跳过 0 条，第 2 页跳过 20 条
    offset = (page - 1) * page_size
    search_pattern = f"%{keyword}%"

    # 2. 查询当前页的详细数据
    # 注意：必须加 ORDER BY，通常用主键 user_id 排序，保证分页结果稳定不乱序
    query_items = """
        SELECT user_id, username, avatar_url
        FROM user_account
        WHERE username ILIKE $1
        ORDER BY user_id ASC
        LIMIT $2 OFFSET $3;
    """
    rows = await conn.fetch(query_items, search_pattern, page_size, offset)
    items = [dict(row) for row in rows]

    # 3. 查询符合搜索条件的总人数 (前端分页器强依赖这个数据)
    query_total = """
        SELECT COUNT(*)
        FROM user_account
        WHERE username ILIKE $1;
    """
    total_count = await conn.fetchval(query_total, search_pattern)

    # 4. 组装成标准的分页返回格式
    return {
        "items": items,  # 当前页的用户列表
        "total": total_count,  # 满足条件的总条数
        "page": page,  # 当前页码
        "page_size": page_size,  # 每页大小
    }
