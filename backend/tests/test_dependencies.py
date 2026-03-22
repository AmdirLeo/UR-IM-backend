import pytest
import jwt
import asyncio
from datetime import datetime, timedelta, timezone
from core.config import settings
from core.exceptions import BusinessException
from api.dependencies import get_current_user_id

def create_test_token(payload_override: dict, secret: str = settings.JWT_SECRET_KEY) -> str:
    """辅助函数：快速生成用于测试的 JWT"""
    payload = {"sub": "999", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}
    payload.update(payload_override)
    return jwt.encode(payload, secret, algorithm="HS256")

def test_valid_token():
    """测试场景 1：合法的 Token 应该成功返回 user_id"""
    token = create_test_token({})
    # 因为 get_current_user_id 是 async 函数，在普通测试中用 asyncio.run 驱动它
    user_id = asyncio.run(get_current_user_id(token))
    assert user_id == 999

def test_expired_token():
    """测试场景 2：过期的 Token 应该被拦截并抛出 401"""
    # 制造一个 10 分钟前就已经过期的 token
    expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    token = create_test_token({"exp": expired_time})
    
    with pytest.raises(BusinessException) as exc_info:
        asyncio.run(get_current_user_id(token))
        
    assert exc_info.value.status_code == 401
    assert "过期" in exc_info.value.detail

def test_invalid_signature_token():
    """测试场景 3：被篡改或签名错误的 Token 应该被拦截"""
    # 使用一个假密钥来签发 token
    token = create_test_token({}, secret="fake_hacker_key_that_is_at_least_32_bytes_long")
    
    with pytest.raises(BusinessException) as exc_info:
        asyncio.run(get_current_user_id(token))
        
    assert exc_info.value.status_code == 401
    assert "无效的身份凭证" in exc_info.value.detail

def test_missing_sub_token():
    """测试场景 4：缺少 sub (user_id) 字段的畸形 Token 应该被拦截"""
    # 不使用 create_test_token，我们手动签发一个完全没有 sub 键的 Token
    payload_without_sub = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10)
    }
    token = jwt.encode(payload_without_sub, settings.JWT_SECRET_KEY, algorithm="HS256")
    
    with pytest.raises(BusinessException) as exc_info:
        asyncio.run(get_current_user_id(token))
        
    assert exc_info.value.status_code == 401
    # 这次它会完美绕过 PyJWT 的底层校验，精确命中我们的 if 判断！
    assert "无效的凭证载荷" in exc_info.value.detail