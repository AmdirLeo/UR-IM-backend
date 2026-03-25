from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional


# ==========================================
# 1. 注册相关结构
# ==========================================
class EmailRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱地址")


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, description="用户名")
    password: str = Field(..., min_length=6, max_length=50, description="明文密码")
    email: EmailStr = Field(..., description="用户邮箱")
    verification_code: str = Field(..., description="邮箱验证码")


class RegisterResponse(BaseModel):
    code: int = 200
    id: int = Field(..., description="数据库生成的自增全局唯一ID")


# ==========================================
# 2. 登录相关结构
# ==========================================
class UserLogin(BaseModel):
    # 严格按照新文档，使用 id (数据库自增的数字，前端可能会传字符串格式)
    id: str = Field(..., description="用户ID (数据库自增主键)")
    password: str = Field(..., description="明文密码")


class LoginResponse(BaseModel):
    code: int = 200
    token: str = Field(..., description="JWT 访问令牌")


# 忘记密码
class UserForgetPWD(BaseModel):
    password: str = Field(..., min_length=6, max_length=50, description="明文密码")
    email: EmailStr = Field(..., description="用户邮箱")
    verification_code: str = Field(..., description="邮箱验证码")


# ==========================================
# 3. 信息修改相关结构
# ==========================================
class UserEdit(BaseModel):
    user_name: Optional[str] = Field(
        None, min_length=3, max_length=20, description="新用户名"
    )
    old_password: Optional[str] = Field(
        None, min_length=6, max_length=50, description="旧密码"
    )
    new_password: Optional[str] = Field(
        None, min_length=6, max_length=50, description="新密码"
    )
    email: Optional[EmailStr] = Field(None, description="新邮箱")


class EmailEdit(BaseModel):
    password: str = Field(..., description="当前明文密码，用于验证身份")
    # 注意：新文档中写的是 new-email，在 Pydantic 中可以通过 alias 完美映射到 Python 变量
    new_email: EmailStr = Field(..., alias="new-email", description="新邮箱地址")


# ==========================================
# 4. 通用基础响应
# ==========================================
class BaseResponse(BaseModel):
    code: int = 200
    msg: Optional[str] = None


# ==========================================
# 5. 搜索用户相关结构
# ==========================================
# 搜索用户请求参数 (GET 使用 query)
class SearchUserQuery(BaseModel):
    keyword: str = Field(
        ..., min_length=1, max_length=50, description="搜索关键词（用户名模糊匹配）"
    )
    # 可选分页参数，可后续扩展
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=100)


# 单个用户搜索结果
class UserSearchResult(BaseModel):
    user_id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    avatar_url: Optional[str] = Field(None, description="头像URL")  # 图像功能的类型存疑


# 搜索用户响应 (继承 BaseResponse)
class SearchUserResponse(BaseResponse):
    data: List[UserSearchResult] = Field(default_factory=list)
