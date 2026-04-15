import jwt
from fastapi import Depends, Header, Request
from typing import Annotated, Any
from fastapi.security import OAuth2PasswordBearer
from core.config import settings
from core.exceptions import BusinessException
import asyncpg
from db.database import get_db_conn
from db.redis_client import db_consume_idempotent_token, db_check_rate_limit, db_get_ttl
from schemas.user import EmailRequest

# 声明前端携带 Token 的标准方式：在 HTTP Header 中使用 Authorization: Bearer <token>
# 这里的 tokenUrl 只是给 Swagger UI 测试用的提示，告诉它去哪里换取 Token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    """
    全局 Token 拦截与解析依赖。
    如果 Token 合法，返回解密后的 user_id；如果非法或过期，直接抛出全局 401 异常拦截请求。
    """
    try:
        # 使用你在 security.py 中配置的同一个密钥和算法进行解密
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[
                             getattr(settings, "ALGORITHM", "HS256")])

        # 提取之前在 create_access_token 中存入的 "sub" 字段
        user_id_str = payload.get("sub")

        if user_id_str is None:
            raise BusinessException(status_code=401, detail="无效的凭证载荷")

        return int(user_id_str)

    except jwt.ExpiredSignatureError:
        # 捕获 Token 过期异常
        raise BusinessException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        # 捕获 Token 签名错误、被篡改或格式错误等异常
        raise BusinessException(status_code=401, detail="无效的身份凭证")


# ==========================================
# 2. 新增：通用限流器 (Rate Limiter)
# ==========================================
class RateLimiter:
    def __init__(self, times: int, seconds: int):
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request):
        # 这里的 Key 包含：路径 + 客户端IP
        # 如果你想针对用户限制，可以结合 get_current_user_id
        path = request.url.path
        client_ip = request.client.host
        key = f"rate_limit:{path}:{client_ip}"

        is_allowed = await db_check_rate_limit(key, self.times, self.seconds)
        if not is_allowed:
            ttl = await db_get_ttl(key)
            # 这里用了标准的 HTTPException，你也可以改成你的 BusinessException
            raise BusinessException(
                status_code=429,
                detail=f"请求太频繁，请在 {ttl} 秒后再试"
            )
        return True

# ==========================================
# 3. 新增：幂等性 Token 校验 (防止重复提交)
# ==========================================


async def verify_idempotent_token(x_idempotent_token: str = Header(None)):
    if not x_idempotent_token:
        raise BusinessException(status_code=400, detail="提交失败：缺少防重令牌")

    # 调用你 db/redis_client.py 里的函数
    if not await db_consume_idempotent_token(x_idempotent_token):
        raise BusinessException(status_code=429, detail="请勿重复提交或令牌已失效")
    return True


async def vcode_limit_check(request: EmailRequest):
    """
    第三层：验证码 60 秒一次。
    直接从 Pydantic 模型中提取 email 字段。
    """
    email = request.email
    if not email:
        raise BusinessException(status_code=400, detail="邮箱不能为空")

    key = f"limit:vcode:{email}"

    # 限制 1 次 / 60 秒
    if not await db_check_rate_limit(key, limit=1, window=60):
        ttl = await db_get_ttl(key)
        raise BusinessException(
            status_code=429,
            detail=f"验证码发送过于频繁，请在 {ttl}s 后重试"
        )

    # 返回提取出的 email，方便路由直接使用
    return email

# 别名定义
VCodeLimit = Annotated[str, Depends(vcode_limit_check)]

CurrentUserId = Annotated[int, Depends(get_current_user_id)]

DBConnection = Annotated[asyncpg.Connection, Depends(get_db_conn)]

IdempotencyCheck = Annotated[bool, Depends(verify_idempotent_token)]
