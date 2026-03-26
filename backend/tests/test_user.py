import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

# 1. 导入真实的 app！
# 此时 conftest.py 会自动接管它，将其数据库连接重定向到 test_im_db
from main import app

client = TestClient(app)

# ==========================================
# 真实的端到端 (E2E) 集成测试
# 不 Mock 数据库，完全测试真实的数据流转与 JWT 签发
# ==========================================

# 仅仅 Mock 随机数生成器，让真实的 Redis 存入固定的 "123456"
@patch("api.routes.user.generate_verification_code", return_value="123456")
def test_full_user_lifecycle(mock_gen_code):
    """
    用户全生命周期连贯测试 (User Journey)
    """
    email = "real_e2e@tsinghua.edu.cn"
    password = "strong_password_123"
    new_password = "new_password_456"
    
    # ------------------------------------------------
    # 1. 发送验证码 (真实写入本地 Redis)
    # ------------------------------------------------
    res_email = client.post("/api/users/register/email", json={"email": email})
    assert res_email.status_code == 200
    
    # ------------------------------------------------
    # 2. 测试注册验证码错误 (读取真实 Redis 进行对比)
    # ------------------------------------------------
    res_reg_fail = client.post(
        "/api/users/register",
        json={
            "username": "e2e_tester",
            "password": password,
            "email": email,
            "verification_code": "wrong_code"
        }
    )
    assert res_reg_fail.status_code == 400
    assert res_reg_fail.json().get("msg") == "验证码错误" or res_reg_fail.json().get("detail") == "验证码错误"
    
    # ------------------------------------------------
    # 3. 真实注册成功 (真实执行 INSERT 写入 PostgreSQL)
    # ------------------------------------------------
    res_reg_ok = client.post(
        "/api/users/register",
        json={
            "username": "e2e_tester",
            "password": password,
            "email": email,
            "verification_code": "123456"
        }
    )
    assert res_reg_ok.status_code == 200
    assert "id" in res_reg_ok.json()
    
    # ------------------------------------------------
    # 4. 真实登录 (真实执行 SELECT 校验哈希并签发 JWT)
    # ------------------------------------------------
    res_login = client.post(
        "/api/users/login",
        json={"id": email, "password": password}
    )
    assert res_login.status_code == 200
    token = res_login.json()["token"]
    assert token is not None
    
    # ------------------------------------------------
    # 5. 真实修改资料 (真实解析 Token 并执行 UPDATE)
    # ------------------------------------------------
    headers = {"Authorization": f"Bearer {token}"}
    res_edit = client.put(
        "/api/users/edit",
        json={
            "old_password": password,
            "new_password": new_password,
            "user_name": "updated_e2e_tester",
            "email": email
        },
        headers=headers
    )
    assert res_edit.status_code == 200
    
    # ------------------------------------------------
    # 6. 验证密码修改是否生效 (用旧密码登录应失败)
    # ------------------------------------------------
    res_login_fail = client.post(
        "/api/users/login",
        json={"id": email, "password": password}
    )
    assert res_login_fail.status_code == 400
    
    # ------------------------------------------------
    # 7. 真实注销账号 (真实执行 DELETE CASCADE)
    # ------------------------------------------------
    # 先用新密码重新登录，获取最新有效 Token
    res_login_new = client.post(
        "/api/users/login",
        json={"id": email, "password": new_password}
    )
    new_token = res_login_new.json()["token"]
    
    res_delete = client.post(
        "/api/users/delete",
        headers={"Authorization": f"Bearer {new_token}"}
    )
    assert res_delete.status_code == 200
    
    # ------------------------------------------------
    # 8. 确认账号已被彻底删除
    # ------------------------------------------------
    res_login_deleted = client.post(
        "/api/users/login",
        json={"id": email, "password": new_password}
    )
    assert res_login_deleted.status_code == 400