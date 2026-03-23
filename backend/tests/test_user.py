import pytest
from fastapi.testclient import TestClient
from main import app
from core.security import create_access_token

client = TestClient(app, raise_server_exceptions=False)

# 辅助函数：生成合法的 Auth Header 用于测试需要登录的接口
def get_auth_headers(user_id: int = 999) -> dict:
    token = create_access_token(data={"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}

# ==========================================
# 1. 注册与密码找回测试
# ==========================================
def test_send_register_email():
    response = client.post("/api/user/register/email", json={"email": "test@example.com"})
    assert response.status_code == 200
    assert response.json()["code"] == 200

def test_register_success():
    """测试注册成功路径（验证码正确）"""
    payload = {
        "username": "new_user",
        "password": "secure_password",
        "email": "test@example.com",
        "verification_code": "123456" # Mock 中设定的正确验证码
    }
    response = client.post("/api/user/register", json=payload)
    assert response.status_code == 200
    assert response.json()["id"] == 1

def test_register_wrong_code():
    """测试注册失败路径（验证码错误）"""
    payload = {
        "username": "new_user",
        "password": "secure_password",
        "email": "test@example.com",
        "verification_code": "000000"
    }
    response = client.post("/api/user/register", json=payload)
    assert response.status_code == 400
    # 🔽 修改这里：从 "detail" 改为 "msg"，对齐你的全局异常处理器
    assert "验证码错误" in response.json()["msg"]

def test_forget_password():
    response = client.post("/api/user/register/forgetpswd", json={"email": "test@example.com"})
    assert response.status_code == 200

# ==========================================
# 2. 登录、登出与注销测试
# ==========================================
def test_login_success():
    """测试登录成功路径"""
    payload = {"id": "1", "password": "correct_password"}
    response = client.post("/api/user/login", json=payload)
    assert response.status_code == 200
    assert "token" in response.json()

def test_login_fail():
    """测试登录失败路径（模拟密码错误）"""
    payload = {"id": "1", "password": "wrong"} 
    response = client.post("/api/user/login", json=payload)
    assert response.status_code == 400
    # 🔽 修改这里：从 "detail" 改为 "msg"
    assert "密码不一致" in response.json()["msg"]

def test_logout():
    """测试正常登出（需携带 Token）"""
    response = client.post("/api/user/logout", headers=get_auth_headers(1))
    assert response.status_code == 200
    assert "登出成功" in response.json()["msg"]

def test_logout_unauthorized():
    """测试未携带 Token 尝试登出被拦截"""
    response = client.post("/api/user/logout")
    assert response.status_code == 401 # FastAPI 依赖注入拦截

def test_delete_account():
    response = client.post("/api/user/delete", headers=get_auth_headers(1))
    assert response.status_code == 200

# ==========================================
# 3. 个人信息修改测试
# ==========================================
def test_edit_profile():
    payload = {"user_name": "updated_name", "new_password": "new_secure_password"}
    response = client.put("/api/user/edit", json=payload, headers=get_auth_headers(1))
    assert response.status_code == 200

def test_edit_email():
    payload = {"password": "current_password", "new-email": "new@example.com"}
    response = client.put("/api/user/edit/email", json=payload, headers=get_auth_headers(1))
    assert response.status_code == 200

def test_edit_portrait():
    """测试头像上传（Multipart/form-data 格式）"""
    # 模拟一个极简的图片文件内容
    file_content = b"fake_image_bytes_for_testing"
    files = {"file": ("test_avatar.png", file_content, "image/png")}
    
    response = client.put(
        "/api/user/edit/portrait", 
        headers=get_auth_headers(1),
        files=files  # 注意这里使用的是 files 参数而不是 json
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert "test_avatar.png" in data["filekey"]
    assert data["width"] == 1024