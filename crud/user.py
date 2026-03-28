from schemas.user import UserCreate
from core.security import get_password_hash

# ==========================================
# 约定：所有的 db_session 参数由路由层通过依赖注入传进来，
# 你不需要关心它是 SQLAlchemy 的 AsyncSession 还是 asyncpg 的 Connection。
# ==========================================

async def create_user(db_session, user_in: UserCreate):
    """
    [注册逻辑] 接收规范化的数据，处理密码加密，并落库。
    """
    # 1. 核心安全操作：将明文密码转换为哈希密文
    hashed_password = get_password_hash(user_in.password)
    
    # 2. 将 Pydantic 模型转换为字典，并剔除明文密码
    db_user_data = user_in.model_dump(exclude={"password"})
    
    # 3. 换上加密后的密码
    db_user_data["password"] = hashed_password
    
    # -----------------------------------------------------------
    # [TODO: 交给数据库同学补充]
    # 执行 INSERT 操作，将 db_user_data 存入 users 表。
    # 必须返回包含自动生成的 user_id 的完整用户记录 (字典或 ORM 对象)。
    # 例如: return await db_model.insert(db_session, **db_user_data)
    # -----------------------------------------------------------
    print(f"[Mock DB] 正在向数据库插入用户: {db_user_data['username']}")
    db_user_data["user_id"] = 1001  # 假装数据库生成了 ID
    return db_user_data


async def get_user_by_email(db_session, email: str):
    """
    [防重逻辑] 注册前检查邮箱是否已被占用。
    """
    # -----------------------------------------------------------
    # [TODO: 交给数据库同学补充]
    # 执行 SELECT 操作，如果找到记录返回对象，找不到返回 None。
    # 例如: return await db_model.query_by_email(db_session, email)
    # -----------------------------------------------------------
    print(f"[Mock DB] 正在数据库中查询邮箱: {email}")
    return None  # 假装邮箱没被占用


async def get_user_by_id_or_email(db_session, id_or_email: str):
    """
    [登录逻辑] 智能判断用户输入的是 ID 还是邮箱，并路由到底层不同的查询。
    """
    # 1. 如果包含 @ 符号，走邮箱查询逻辑
    if "@" in id_or_email:
        # -------------------------------------------------------
        # [TO-DO: 交给数据库同学补充] 执行 SELECT * WHERE email = ?
        # -------------------------------------------------------
        print(f"[Mock DB] 用户使用邮箱登录: {id_or_email}")
        pass
        
    # 2. 如果全是纯数字，走 ID 查询逻辑
    elif id_or_email.isdigit():
        user_id = int(id_or_email)
        # -------------------------------------------------------
        # [TO-DO: 交给数据库同学补充] 执行 SELECT * WHERE user_id = ?
        # -------------------------------------------------------
        print(f"[Mock DB] 用户使用 ID 登录: {user_id}")
        pass
        
    # 3. 既不是数字也不包含 @，格式绝对非法，直接在底层拦截，防数据库爆破
    else:
        return None
        
    # [Mock 返回假数据用于你后续跑通登录接口]
    return {
        "user_id": int(id_or_email) if id_or_email.isdigit() else 1001,
        "username": "mock_user",
        "email": id_or_email if "@" in id_or_email else "mock@test.com",
        "password": get_password_hash("test_password_123")
    }