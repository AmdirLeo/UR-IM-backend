import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
import jwt
from typing import Dict

# 导入 app，conftest.py 会自动接管数据库配置
from main import app
from api.routes.user import router
from core.exceptions import setup_exception_handlers
from core.config import settings
from db.database import get_db_conn
from services.user_service import search_users

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
app.include_router(router, prefix="/api/users")

# Helpers for tests
VALID_EMAIL = "test@tsinghua.edu.cn"
VALID_USERNAME = "tester"
VALID_PASSWORD = "password123"


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ==========================================
# 2. Test Cases
# ==========================================


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
async def test_search_users_repository():
    """测试 service 层的函数"""
    # 使用 async for 动态获取，完美避开导包陷阱
    async for conn in get_db_conn():
        results = await search_users(conn, "tester")
        assert isinstance(results, list)
        break  # 测完立刻退出


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
@patch("services.user_service.generate_verification_code", return_value="123456")
async def test_user_journey_and_edge_cases(mock_generate_code):
    """
    全量用户的 E2E 测试。必须使用 AsyncClient。
    """

    # 核心：使用 AsyncClient 替代 TestClient
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:

        # ---------------------------------------------------------
        # 1. Send Registration Email
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/register/email", json={"email": VALID_EMAIL}
        )
        assert response.status_code == 200
        res_data = response.json()
        assert "code" in res_data
        assert res_data["verification_code"] == "123456"  # 校验 mock 的值
        mock_generate_code.assert_called()

        # ---------------------------------------------------------
        # 2. Fail to register with a wrong verification code (400)
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/register",
            json={
                "username": VALID_USERNAME,
                "password": VALID_PASSWORD,
                "email": VALID_EMAIL,
                "verification_code": "wrong",
            },
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "验证码错误"

        # ---------------------------------------------------------
        # 3. Successfully register with code "123456" (200)
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/register",
            json={
                "username": VALID_USERNAME,
                "password": VALID_PASSWORD,
                "email": VALID_EMAIL,
                "verification_code": "123456",
            },
        )
        assert response.status_code == 200
        res_data = response.json()
        assert "id" in res_data
        user_id = res_data["id"]

        # ---------------------------------------------------------
        # 4. Try to register again with same email -> existing user (400)
        # ---------------------------------------------------------
        await client.post("/api/users/register/email", json={"email": VALID_EMAIL})
        response = await client.post(
            "/api/users/register",
            json={
                "username": VALID_USERNAME,
                "password": VALID_PASSWORD,
                "email": VALID_EMAIL,
                "verification_code": "123456",
            },
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "该邮箱已被注册"

        # ---------------------------------------------------------
        # 5. Login with incorrect credentials (400)
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/login", json={"id": VALID_EMAIL, "password": "wrong_password"}
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "密码错误"

        response = await client.post(
            "/api/users/login",
            json={"id": "nonexistent@example.com", "password": VALID_PASSWORD},
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "账号不存在"

        # ---------------------------------------------------------
        # 6. Login successfully -> obtain JWT token
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/login", json={"id": VALID_EMAIL, "password": VALID_PASSWORD}
        )
        assert response.status_code == 200
        token = response.json()["token"]

        response = await client.post(
            "/api/users/login", json={"id": str(user_id), "password": VALID_PASSWORD}
        )
        assert response.status_code == 200

        # ---------------------------------------------------------
        # 7. Access protected route with invalid/missing JWT (401)
        # ---------------------------------------------------------
        response = await client.put("/api/users/edit", json={"user_name": "new_name"})
        assert response.status_code == 401

        response = await client.put(
            "/api/users/edit",
            json={"user_name": "new_name"},
            headers=get_auth_headers("invalid_token"),
        )
        assert response.status_code == 401

        expired_token = jwt.encode(
            {"sub": str(user_id), "exp": 0},
            settings.JWT_SECRET_KEY,
            algorithm=getattr(settings, "ALGORITHM", "HS256"),
        )
        response = await client.put(
            "/api/users/edit",
            json={"user_name": "new_name"},
            headers=get_auth_headers(expired_token),
        )
        assert response.status_code == 401

        no_sub_token = jwt.encode(
            {"other": "field"},
            settings.JWT_SECRET_KEY,
            algorithm=getattr(settings, "ALGORITHM", "HS256"),
        )
        response = await client.put(
            "/api/users/edit",
            json={"user_name": "new_name"},
            headers=get_auth_headers(no_sub_token),
        )
        assert response.status_code == 401

        # ---------------------------------------------------------
        # 8. Edit profile using valid JWT token
        # ---------------------------------------------------------
        auth_headers = get_auth_headers(token)

        # 第一次请求：修改邮箱
        new_email = "new_email@tsinghua.edu.cn"
        response = await client.put(
            "/api/users/edit",
            json={"user_name": "new_tester", "email": new_email},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # 第二次请求：继续用原来的 auth_headers 修改密码
        new_password = "newpassword456"
        response = await client.put(
            "/api/users/edit",
            json={"old_password": VALID_PASSWORD,
                  "new_password": new_password},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # ---------------------------------------------------------
        # 9. Forget Password Flow
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/register/forgetpswdsend", json={"email": "notfound@example.com"}
        )
        assert response.status_code == 404

        response = await client.post(
            "/api/users/register/forgetpswdsend", json={"email": new_email}
        )
        assert response.status_code == 200

        response = await client.post(
            "/api/users/register/forgetpswdset",
            json={
                "email": new_email,
                "password": "recoveredpassword",
                "verification_code": "wrong",
            },
        )
        assert response.status_code == 400

        response = await client.post(
            "/api/users/register/forgetpswdset",
            json={
                "email": new_email,
                "password": "recoveredpassword",
                "verification_code": "123456",
            },
        )
        assert response.status_code == 200

        # ---------------------------------------------------------
        # 10. Delete Account
        # ---------------------------------------------------------
        response = await client.post(
            "/api/users/login",
            json={"id": str(user_id), "password": "recoveredpassword"},
        )
        assert response.status_code == 200
        token_for_logout = response.json()["token"]

        response = await client.post(
            "/api/users/logout",
            headers=get_auth_headers(token_for_logout)
        )
        assert response.status_code == 200

        # 为了防止登出导致旧 Token 失效，重新登录拿一个新 Token 去执行终极删号操作
        response = await client.post(
            "/api/users/login",
            json={"id": str(user_id), "password": "recoveredpassword"},
        )
        new_token_for_delete = response.json()["token"]
        delete_headers = get_auth_headers(new_token_for_delete)

        # 彻底注销账号
        response = await client.post("/api/users/delete", headers=delete_headers)
        assert response.status_code == 200
