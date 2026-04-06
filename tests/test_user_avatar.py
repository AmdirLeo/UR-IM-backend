import os
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock
from main import app
from api.dependencies import CurrentUserId

from core.security import create_access_token


@pytest.mark.asyncio
async def test_upload_avatar_success():
    """测试头像上传流程"""
    # 1. 制造一把真正的钥匙：签发一个代表 user_id = 1 的 Token
    test_token = create_access_token(data={"sub": "1"})
    # 2. 把钥匙放在 HTTP 请求头里
    headers = {"Authorization": f"Bearer {test_token}"}
    # 模拟拦截数据库操作
    with patch("services.user_service.db_update_user_profile", new_callable=AsyncMock) as mock_db:
        mock_db.return_value = True

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            files = {"file": ("test.png", b"fake_data", "image/png")}
            response = await ac.put(
                "/api/user/edit/portrait",
                files=files,
                headers=headers
            )

        assert response.status_code == 200
        data = response.json()
        saved_path = data["filekey"].lstrip("/")
        if os.path.exists(saved_path):
            os.remove(saved_path)  # 清理测试产生的图片
