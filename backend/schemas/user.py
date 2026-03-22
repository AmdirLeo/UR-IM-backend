from pydantic import BaseModel, EmailStr, Field

# ==========================================
# 1. 共享的基础属性
# ==========================================
class UserBase(BaseModel):
    # Field(...) 表示必填项，并利用 min_length/max_length 防御恶意超长字符串
    username: str = Field(..., min_length=3, max_length=20, description="用户名")
    email: EmailStr = Field(..., description="用户邮箱")

# ==========================================
# 2. 注册时的请求体 (接收前端传来的 JSON)
# ==========================================
class UserCreate(UserBase):
    # 继承了 username 和 email，额外增加 password
    password: str = Field(..., min_length=6, max_length=50, description="明文密码，后端必须哈希后存库")

# ==========================================
# 3. 登录时的请求体
# ==========================================
class UserLogin(BaseModel):
    # 工业界标准：允许用户用用户名或邮箱登录
    username_or_email: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="明文密码")

# ==========================================
# 4. 返回给前端的用户信息
# ==========================================
class UserResponse(UserBase):
    # 继承了 username 和 email，增加全局唯一 ID，但绝对没有 password！
    user_id: int  
    
    # 核心配置：允许直接把数据库查询结果（ORM 对象或 Record）自动转化为 JSON
    model_config = {"from_attributes": True}

# ==========================================
# 5. JWT Token 返回结构
# ==========================================
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"