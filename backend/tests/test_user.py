import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch
import jwt
from typing import Dict, Any

from api.routes.user import router
from core.exceptions import setup_exception_handlers
from core.config import settings
from db.repositories.user_repo import db_search_users
from db.database import get_db_conn

# ==========================================
# 1. Setup FastAPI Client
# ==========================================
app = FastAPI()
setup_exception_handlers(app)
app.include_router(router, prefix="/api/users")

client = TestClient(app)

# Note: We do NOT use dependency overrides for the database or auth.
# The `conftest.py` is expected to manage the database connection pool correctly.

# Helpers for tests
VALID_EMAIL = "test@tsinghua.edu.cn"
VALID_USERNAME = "tester"
VALID_PASSWORD = "password123"

def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

# ==========================================
# 2. Test Cases
# ==========================================

@pytest.mark.asyncio
async def test_search_users_repository():
    """Test the repository search user function directly as it's not in the router."""
    conn_gen = get_db_conn()
    conn = await anext(conn_gen)
    try:
        results = await db_search_users(conn, "tester")
        assert isinstance(results, list)
    finally:
        pass

@patch("api.routes.user.generate_verification_code", return_value="123456")
def test_user_journey_and_edge_cases(mock_generate_code):
    """
    Run the entire user journey and edge cases sequentially to handle state properly.
    This avoids complex setup/teardown and ensures the database state flows correctly.
    """

    # ---------------------------------------------------------
    # 1. Send Registration Email
    # ---------------------------------------------------------
    response = client.post(
        "/api/users/register/email",
        json={"email": VALID_EMAIL}
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "验证码已发送至邮箱"
    mock_generate_code.assert_called()

    # ---------------------------------------------------------
    # 2. Fail to register with a wrong verification code (400)
    # ---------------------------------------------------------
    response = client.post(
        "/api/users/register",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "email": VALID_EMAIL,
            "verification_code": "wrong"
        }
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "验证码错误"

    # ---------------------------------------------------------
    # 3. Successfully register with code "123456" (200)
    # ---------------------------------------------------------
    response = client.post(
        "/api/users/register",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "email": VALID_EMAIL,
            "verification_code": "123456"
        }
    )
    assert response.status_code == 200
    res_data = response.json()
    assert "id" in res_data
    user_id = res_data["id"]

    # ---------------------------------------------------------
    # 4. Try to register again with same email -> existing user logic (400)
    # ---------------------------------------------------------
    client.post("/api/users/register/email", json={"email": VALID_EMAIL})
    response = client.post(
        "/api/users/register",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "email": VALID_EMAIL,
            "verification_code": "123456"
        }
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "该邮箱已被注册"

    # ---------------------------------------------------------
    # 5. Login with incorrect credentials (400/401)
    # ---------------------------------------------------------
    # 5.1 Incorrect Password
    response = client.post(
        "/api/users/login",
        json={"id": VALID_EMAIL, "password": "wrong_password"}
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "密码错误"
    
    # 5.2 Incorrect Email (Not found)
    response = client.post(
        "/api/users/login",
        json={"id": "nonexistent@example.com", "password": VALID_PASSWORD}
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "账号不存在"

    # ---------------------------------------------------------
    # 6. Login successfully with new credentials -> obtain JWT token
    # ---------------------------------------------------------
    # 6.1 Using Email
    response = client.post(
        "/api/users/login",
        json={"id": VALID_EMAIL, "password": VALID_PASSWORD}
    )
    assert response.status_code == 200
    assert "token" in response.json()
    token = response.json()["token"]

    # 6.2 Using User ID (to cover id login logic)
    response = client.post(
        "/api/users/login",
        json={"id": str(user_id), "password": VALID_PASSWORD}
    )
    assert response.status_code == 200
    assert "token" in response.json()

    # ---------------------------------------------------------
    # 7. Access protected route with invalid/missing JWT (401)
    # ---------------------------------------------------------
    response = client.put("/api/users/edit", json={"user_name": "new_name"})
    assert response.status_code == 401 # Missing token
    
    response = client.put("/api/users/edit", json={"user_name": "new_name"}, headers=get_auth_headers("invalid_token"))
    assert response.status_code == 401 # Invalid token

    expired_token = jwt.encode({"sub": str(user_id), "exp": 0}, settings.JWT_SECRET_KEY, algorithm=getattr(settings, "ALGORITHM", "HS256"))
    response = client.put("/api/users/edit", json={"user_name": "new_name"}, headers=get_auth_headers(expired_token))
    assert response.status_code == 401

    no_sub_token = jwt.encode({"other": "field"}, settings.JWT_SECRET_KEY, algorithm=getattr(settings, "ALGORITHM", "HS256"))
    response = client.put("/api/users/edit", json={"user_name": "new_name"}, headers=get_auth_headers(no_sub_token))
    assert response.status_code == 401

    # ---------------------------------------------------------
    # 8. Edit profile using valid JWT token
    # ---------------------------------------------------------
    auth_headers = get_auth_headers(token)

    # 8.1 Edit Username & Email
    new_email = "new_email@tsinghua.edu.cn"
    response = client.put(
        "/api/users/edit",
        json={"user_name": "new_tester", "email": new_email},
        headers=auth_headers
    )
    assert response.status_code == 200

    # 8.2 Edit Password
    new_password = "newpassword456"
    response = client.put(
        "/api/users/edit",
        json={"old_password": VALID_PASSWORD, "new_password": new_password},
        headers=auth_headers
    )
    assert response.status_code == 200

    # 8.3 Edit Password with Wrong Old Password
    response = client.put(
        "/api/users/edit",
        json={"old_password": "wrong", "new_password": "willnotwork"},
        headers=auth_headers
    )
    assert response.status_code == 400

    # 8.4 Edit Email (via dedicated endpoint)
    response = client.put(
        "/api/users/edit/email",
        json={"password": new_password, "new-email": "another@tsinghua.edu.cn"},
        headers=auth_headers
    )
    assert response.status_code == 200

    # ---------------------------------------------------------
    # 9. Forget Password Flow
    # ---------------------------------------------------------
    response = client.post(
        "/api/users/register/forgetpswdsend",
        json={"email": "notfound@example.com"}
    )
    assert response.status_code == 404

    response = client.post(
        "/api/users/register/forgetpswdsend",
        json={"email": "another@tsinghua.edu.cn"}
    )
    assert response.status_code == 200

    response = client.post(
        "/api/users/register/forgetpswdset",
        json={"email": "another@tsinghua.edu.cn", "password": "recoveredpassword", "verification_code": "wrong"}
    )
    assert response.status_code == 400

    response = client.post(
        "/api/users/register/forgetpswdset",
        json={"email": "another@tsinghua.edu.cn", "password": "recoveredpassword", "verification_code": "123456"}
    )
    assert response.status_code == 200

    # ---------------------------------------------------------
    # 10. Logout
    # ---------------------------------------------------------
    response = client.post(
        "/api/users/login",
        json={"id": str(user_id), "password": "recoveredpassword"}
    )
    assert response.status_code == 200
    token = response.json()["token"]
    auth_headers = get_auth_headers(token)

    response = client.post("/api/users/logout", headers=auth_headers)
    assert response.status_code == 200

    # ---------------------------------------------------------
    # 11. Delete Account
    # ---------------------------------------------------------
    response = client.post("/api/users/delete", headers=auth_headers)
    assert response.status_code == 200

    response = client.post("/api/users/delete", headers=auth_headers)
    assert response.status_code == 404
