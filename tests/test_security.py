import pytest
from datetime import timedelta
import jwt
from core.security import get_password_hash, verify_password, create_access_token
from core.config import settings


def test_password_hashing():
    """
    测试密码哈希的生成与比对逻辑
    """
    password = "secure_password_123"

    # 测试生成的哈希值不等于明文
    hashed_password = get_password_hash(password)
    assert hashed_password != password

    # 测试 bcrypt 的盐值机制：相同明文生成的哈希值应不同
    hashed_password_2 = get_password_hash(password)
    assert hashed_password != hashed_password_2

    # 测试正确密码的比对结果
    assert verify_password(password, hashed_password) is True

    # 测试错误密码的比对结果
    assert verify_password("wrong_password", hashed_password) is False


def test_create_access_token():
    """
    测试默认过期时间下的 Token 签发与解析
    """
    data = {"sub": "user_id_1"}
    token = create_access_token(data=data)

    # 验证返回值类型
    assert isinstance(token, str)

    # 解析 Token 并验证载荷内容
    decoded_data = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
    assert decoded_data["sub"] == "user_id_1"
    assert "exp" in decoded_data


def test_create_access_token_with_expires_delta():
    """
    测试自定义过期时间下的 Token 签发与解析
    """
    data = {"sub": "user_id_2"}
    expires_delta = timedelta(minutes=15)
    token = create_access_token(data=data, expires_delta=expires_delta)

    decoded_data = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
    assert decoded_data["sub"] == "user_id_2"
    assert "exp" in decoded_data
