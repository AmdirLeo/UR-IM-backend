import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from main import app
from core.security import create_access_token


@pytest.mark.asyncio
async def test_get_user_info_success():
    """测试成功获取个人信息"""

    # 1. 签发测试 Token (假设当前登录用户 ID 为 1)
    test_token = create_access_token(data={"sub": "1"})
    headers = {"Authorization": f"Bearer {test_token}"}

    # 2. 伪造数据库返回的字典数据
    mock_user_data = {
        "user_id": 1,
        "username": "TestUser",
        "email": "test@example.com",
        "avatar_url": "/static/avatars/test.png"
    }

    # 3. 拦截数据库查询操作 (Repository层)，让它直接返回伪造数据
    with patch("services.user_service.db_get_user_by_id", new_callable=AsyncMock) as mock_db:
        mock_db.return_value = mock_user_data

        # 4. 发起 HTTP GET 请求
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.get("/api/user/info", headers=headers)

        # 5. 极其严谨的断言
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["id"] == 1
        assert data["username"] == "TestUser"
        assert data["email"] == "test@example.com"
        assert data["avatar_url"] == "/static/avatars/test.png"


@pytest.mark.asyncio
async def test_get_user_info_not_found():
    """测试 Token 合法但数据库中找不到该用户（比如账号刚被注销）"""

    test_token = create_access_token(data={"sub": "999"})
    headers = {"Authorization": f"Bearer {test_token}"}

    with patch("services.user_service.db_get_user_by_id", new_callable=AsyncMock) as mock_db:
        # 模拟数据库查不到人，返回 None
        mock_db.return_value = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.get("/api/user/info", headers=headers)

        # 断言会被 Service 层拦截并抛出 404
        assert response.status_code == 404
        assert "用户不存在" in response.text


@pytest.mark.asyncio
async def test_get_user_info_unauthorized():
    """测试不带 Token 访问，会被 FastAPI 自动拒绝"""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        # 故意不传 headers
        response = await ac.get("/api/user/info")

    # 断言会被依赖注入 CurrentUserId 拦截并抛出 401
    assert response.status_code == 401
