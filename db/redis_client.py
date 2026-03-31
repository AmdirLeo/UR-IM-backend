import redis.asyncio as redis
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
# 初始化连接客户端
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

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