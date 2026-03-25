import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from api.routes.user import router
from api.dependencies import get_current_user_id, get_db_conn
from core.exceptions import setup_exception_handlers

# ==========================================
# 1. Setup & Mocks
# ==========================================
app = FastAPI()
setup_exception_handlers(app)
app.include_router(router, prefix="/api/users")

class MockDBConnection:
    pass

async def override_get_db_conn():
    yield MockDBConnection()

async def override_get_current_user_id():
    return 1 

app.dependency_overrides[get_db_conn] = override_get_db_conn
app.dependency_overrides[get_current_user_id] = override_get_current_user_id

client = TestClient(app)

# ==========================================
# 2. Registration Tests
# ==========================================

@patch("api.routes.user.generate_verification_code", return_value="123456")
@patch("api.routes.user.db_save_verification_code", new_callable=AsyncMock)
def test_send_register_email(mock_save_redis, mock_gen_code):
    response = client.post(
        "/api/users/register/email",
        json={"email": "test@tsinghua.edu.cn"}
    )
    assert response.status_code == 200
    mock_save_redis.assert_called_once_with("test@tsinghua.edu.cn", "123456")

@patch("api.routes.user.db_verify_code", new_callable=AsyncMock)
def test_register_invalid_code(mock_verify):
    # 模拟验证码错误的情况，返回 False
    mock_verify.return_value = False 
    
    response = client.post(
        "/api/users/register",
        json={
            "username": "tester",
            "password": "password123",
            "email": "test@tsinghua.edu.cn",
            "verification_code": "wrong"
        }
    )
    assert response.status_code == 400
    response_data = response.json()
    assert response_data.get("msg") == "验证码错误"

@patch("api.routes.user.db_verify_code", new_callable=AsyncMock)
@patch("api.routes.user.db_get_user_by_email", new_callable=AsyncMock)
@patch("api.routes.user.db_create_user", new_callable=AsyncMock)
def test_register_success(mock_create, mock_get_user, mock_verify):
    # 模拟验证码正确的情况，返回 True
    mock_verify.return_value = True  
    mock_get_user.return_value = None 
    mock_create.return_value = 101    

    response = client.post(
        "/api/users/register",
        json={
            "username": "tester",
            "password": "password123",
            "email": "test@tsinghua.edu.cn",
            "verification_code": "123456"
        }
    )
    assert response.status_code == 200
    assert response.json()["id"] == 101

# ==========================================
# 3. Login & Profile Tests
# ==========================================

@patch("api.routes.user.db_get_user_by_email", new_callable=AsyncMock)
@patch("api.routes.user.verify_password", return_value=True)
@patch("api.routes.user.db_update_user_login_time", new_callable=AsyncMock)
def test_login_email_success(mock_time, mock_verify_pwd, mock_get_user):
    mock_get_user.return_value = {"user_id": 1, "password": "hashed_string"}
    
    response = client.post(
        "/api/users/login",
        json={"id": "test@tsinghua.edu.cn", "password": "real_password"}
    )
    assert response.status_code == 200
    assert "token" in response.json()

@patch("api.routes.user.db_get_password_by_id", new_callable=AsyncMock)
@patch("api.routes.user.verify_password", return_value=True)
@patch("api.routes.user.db_update_user_password", new_callable=AsyncMock)
def test_edit_password(mock_update, mock_verify, mock_get_pwd):
    mock_get_pwd.return_value = "old_hash"
    
    response = client.put(
        "/api/users/edit",
        json={
            "old_password": "123456", 
            "new_password": "456789",
            "user_name": None, 
            "email": None
        }
    )
    assert response.status_code == 200
    mock_update.assert_called_once()

@patch("api.routes.user.db_delete_user", new_callable=AsyncMock)
def test_delete_account(mock_delete):
    mock_delete.return_value = True
    response = client.post("/api/users/delete")
    assert response.status_code == 200