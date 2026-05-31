import pytest
import jwt
from unittest.mock import patch, AsyncMock

from api.dependencies import get_current_user_id
from core.exceptions import BusinessException

# ==========================================
# 依赖注入单元测试 (同步函数测试)
# ==========================================


@patch("api.dependencies.jwt.decode")
async def test_get_current_user_id_success(mock_decode):
    """测试场景 1：合法的 Token，成功解析出 user_id"""
    # 模拟 jwt.decode 成功返回包含 sub 字段的字典
    mock_decode.return_value = {"sub": "1001", "jti": "mock_jti_123"}

    # 2. 模拟数据库查询结果（数据库里的 jti 和 Token 里的一致）
    mock_db = AsyncMock()
    mock_db.fetchval.return_value = "mock_jti_123"

    # 直接像调用普通函数一样调用，不需要 await
    user_id = await get_current_user_id("valid_mock_token", db_session=mock_db)

    assert user_id == 1001
    mock_decode.assert_called_once()


@patch("api.dependencies.jwt.decode")
async def test_get_current_user_id_missing_sub(mock_decode):
    """测试场景 2：Token 合法但载荷里没有 sub 字段"""
    # 模拟返回的 JSON 里只有别的字段，缺了我们需要的 sub
    mock_decode.return_value = {"role": "user", "exp": 123456789}
    mock_db = AsyncMock()

    # 捕获 BusinessException 异常
    with pytest.raises(BusinessException) as exc_info:
        await get_current_user_id("mock_token_no_sub", db_session=mock_db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "无效的凭证载荷"


@patch("api.dependencies.jwt.decode")
async def test_get_current_user_id_expired(mock_decode):
    """测试场景 3：Token 已过期 (触发 ExpiredSignatureError)"""
    # 让 mock 函数强行抛出 jwt 的过期异常
    mock_decode.side_effect = jwt.ExpiredSignatureError("Token expired")
    mock_db = AsyncMock()

    with pytest.raises(BusinessException) as exc_info:
        await get_current_user_id("expired_token", db_session=mock_db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "登录已过期，请重新登录"


@patch("api.dependencies.jwt.decode")
async def test_get_current_user_id_invalid(mock_decode):
    """测试场景 4：Token 格式错误或被篡改 (触发 InvalidTokenError)"""
    mock_decode.side_effect = jwt.InvalidTokenError("Invalid token format")
    mock_db = AsyncMock()

    with pytest.raises(BusinessException) as exc_info:
        await get_current_user_id("invalid_token", db_session=mock_db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "无效的身份凭证"
