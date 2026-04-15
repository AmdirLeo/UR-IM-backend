import redis.asyncio as redis
import os
from fastapi import Request
from core.config import settings

# REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
# 初始化连接客户端
# 确保 settings.REDIS_URL 读到的是上面那个 redis://... 地址
redis_client = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True)


async def db_save_verification_code(email: str, code: str, expire_minutes: int = 5) -> None:
    """
    将验证码存入 Redis，并设置过期时间
    """
    key = f"verify_code:{email}"

    await redis_client.set(key, code, ex=expire_minutes * 60)


async def db_verify_code(email: str, code: str) -> bool:
    """
    校验验证码是否正确
    """
    key = f"verify_code:{email}"

    saved_code = await redis_client.get(key)

    # 如果验证码存在，且和用户填入的一致
    if saved_code and saved_code == code:
        await redis_client.delete(key)
        return True

    return False


def get_real_ip(request: Request) -> str:
    # 按照优先级获取真实 IP
    # 1. Nginx 转发的 X-Real-IP
    # 2. X-Forwarded-For 列表中的第一个
    # 3. 直连的 client.host
    ip = request.headers.get("X-Real-IP")
    if not ip:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0]
    return ip or request.client.host or "unknown"


async def db_check_rate_limit(key: str, limit: int, window: int) -> bool:
    """
    通用限流检查
    :param key: Redis 中的 key (例如 "limit:user_1:chat_api")
    :param limit: 允许的最大请求次数
    :param window: 时间窗口（秒）
    :return: True 表示未超限，False 表示被限流
    """
    if os.environ.get("DISABLE_RATE_LIMIT") == "1":
        return True

    # 1. 直接原子性地增加 1
    current = await redis_client.incr(key)

    # 2. 如果结果是 1，说明是这个窗口期的第一个请求，立刻给它加上过期时间
    if current == 1:
        await redis_client.expire(key, window)

    # 3. 检查自增后的值是否超过了限制
    return current <= limit


async def db_get_ttl(key: str) -> int:
    """获取剩余时间"""
    return await redis_client.ttl(key)


# ==========================================
# 幂等性 Token 相关操作 (追加到文件末尾)
# ==========================================

async def db_create_idempotent_token(token: str, expire_seconds: int = 300) -> None:
    """
    存入一个幂等性 Token (默认 5 分钟有效)
    """
    await redis_client.set(f"idempotent:{token}", "1", ex=expire_seconds)


async def db_consume_idempotent_token(token: str) -> bool:
    """
    尝试消费（删除）幂等性 Token。
    如果删除成功（返回1），说明 Token 有效且是第一次使用。
    """
    result = await redis_client.delete(f"idempotent:{token}")
    return result > 0
