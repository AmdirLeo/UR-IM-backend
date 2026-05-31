import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock
import jwt
import os
from typing import Dict
import uuid

# 导入 app，conftest.py 会自动接管数据库配置
from main import app
from api.routes.user import router
from core.exceptions import setup_exception_handlers
from core.config import settings
from db.database import get_db_conn
from services.user_service import search_users
from core.security import create_access_token

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
app.include_router(router, prefix="/api/users")

# Helpers for tests
VALID_EMAIL = "test@tsinghua.edu.cn"
VALID_USERNAME = "tester"
VALID_PASSWORD = "password123"
REGISTER_API_PATH = "/api/users/register"
LOGIN_API_PATH = "/api/users/login"
EDIT_USERNAME_API_PATH = "/api/users/edit/username"
EDIT_PASSWORD_API_PATH = "/api/users/edit/password"
EDIT_EMAIL_API_PATH = "/api/users/edit/email"


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

    async for conn in get_db_conn():
        await conn.execute(
            """
            INSERT INTO user_account (user_id, username, password, email)
            VALUES (-1, '系统通知助手', 'system_fake_password', 'system@ur-im.com')
            ON CONFLICT (user_id) DO NOTHING;
        """
        )
        await conn.execute("""
                INSERT INTO user_account (user_id, username, password, email)
                VALUES (-2, '群聊通知助手', 'system_fake_password', 'system_group@ur-im.com')
                ON CONFLICT (user_id) DO NOTHING;
            """)
        break

    # 核心：使用 AsyncClient 替代 TestClient
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:

        # ---------------------------------------------------------
        # 0. 测试不带 Token 访问，会被 FastAPI 自动拒绝
        # ---------------------------------------------------------
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            # 故意不传 headers
            response = await ac.get("/api/user/info")

        # 断言会被依赖注入 CurrentUserId 拦截并抛出 401
        assert response.status_code == 401

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
            REGISTER_API_PATH,
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
            REGISTER_API_PATH,
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
            REGISTER_API_PATH,
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
            LOGIN_API_PATH, json={"id": VALID_EMAIL,
                                  "password": "wrong_password"}
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "账号不存在或密码错误"

        response = await client.post(
            LOGIN_API_PATH,
            json={"id": "nonexistent@example.com", "password": VALID_PASSWORD},
        )
        assert response.status_code == 400
        assert response.json()["msg"] == "账号不存在或密码错误"

        # 👇 新增：非法格式登录测试（既不是邮箱也不是纯数字）
        response = await client.post(
            LOGIN_API_PATH,
            json={"id": "invalid_username_format", "password": VALID_PASSWORD},
        )
        assert response.status_code == 400
        # 注意：这里取决于你的全局异常处理器把 detail 映射到了哪个字段
        # 用 in response.text 是一种最稳妥的断言方式
        assert "请输入正确的邮箱或数字 ID" in response.text

        # ---------------------------------------------------------
        # 6. Login successfully -> obtain JWT token
        # ---------------------------------------------------------
        response = await client.post(
            LOGIN_API_PATH, json={
                "id": VALID_EMAIL, "password": VALID_PASSWORD}
        )
        assert response.status_code == 200

        # ---------------------------------------------------------
        # 👇👇👇 新增：6.5 测试单点登录（设备互踢）功能 👇👇👇
        # ---------------------------------------------------------
        # 模拟设备 A 登录
        res_device_a = await client.post(
            LOGIN_API_PATH, json={"id": VALID_EMAIL, "password": VALID_PASSWORD}
        )
        token_a = res_device_a.json()["token"]

        # 模拟设备 B 登录（此时数据库的 current_jti 被更新为 B 的）
        res_device_b = await client.post(
            LOGIN_API_PATH, json={"id": VALID_EMAIL, "password": VALID_PASSWORD}
        )
        token_b = res_device_b.json()["token"]

        # 验证设备 A 的 Token 被判定为失效 (401)
        res_kick = await client.get(
            "/api/user/info", headers=get_auth_headers(token_a)
        )
        assert res_kick.status_code == 401
        assert "其他设备登录" in res_kick.text

        # 验证设备 B 的 Token 依然有效 (200)
        res_success = await client.get(
            "/api/user/info", headers=get_auth_headers(token_b)
        )
        assert res_success.status_code == 200

        # ⚠️ 非常重要：更新后续流程使用的 token 为存活的 token_b，防止后续测试全部 401 失败
        # 👆👆👆 互踢测试结束 👆👆👆

        response = await client.post(
            LOGIN_API_PATH, json={
                "id": str(user_id), "password": VALID_PASSWORD}
        )
        assert response.status_code == 200
        assert "token" in response.json()
        token = response.json()["token"]

        # ---------------------------------------------------------
        # 6.6 测试成功获取个人信息 (原孤立测试)
        # ---------------------------------------------------------
        # 此时手里拿着刚刚登录成功的 token
        res_info = await client.get(
            "/api/user/info",
            headers=get_auth_headers(token)
        )
        assert res_info.status_code == 200
        info_data = res_info.json()
        assert info_data["code"] == 200
        assert "id" in info_data
        assert info_data["email"] == VALID_EMAIL
        # 验证能查到数据即可，不需要像 Mock 那样写死具体的 ID

        # ---------------------------------------------------------
        # 7. Access protected route with invalid/missing JWT (401)
        # ---------------------------------------------------------
        # 用编辑用户名接口来做鉴权测试
        response = await client.put(
            EDIT_USERNAME_API_PATH, json={"new_username": "new_name"}
        )
        assert response.status_code == 401

        response = await client.put(
            EDIT_USERNAME_API_PATH,
            json={"new_username": "new_name"},
            headers=get_auth_headers("invalid_token"),
        )
        assert response.status_code == 401

        expired_token = jwt.encode(
            {"sub": str(user_id), "exp": 0},
            settings.JWT_SECRET_KEY,
            algorithm=getattr(settings, "ALGORITHM", "HS256"),
        )
        response = await client.put(
            EDIT_USERNAME_API_PATH,
            json={"new_username": "new_name"},
            headers=get_auth_headers(expired_token),
        )
        assert response.status_code == 401

        no_sub_token = jwt.encode(
            {"other": "field"},
            settings.JWT_SECRET_KEY,
            algorithm=getattr(settings, "ALGORITHM", "HS256"),
        )
        response = await client.put(
            EDIT_USERNAME_API_PATH,
            json={"new_username": "new_name"},
            headers=get_auth_headers(no_sub_token),
        )
        assert response.status_code == 401

        # ---------------------------------------------------------
        # 8. Edit profile using valid JWT token
        # ---------------------------------------------------------
        auth_headers = get_auth_headers(token)

        # 8a. 第一次请求：修改用户名
        response = await client.put(
            EDIT_USERNAME_API_PATH,
            json={"new_username": "new_tester"},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # 8b. 第二次请求：修改邮箱
        # ⚠️ 注意这里：必须携带 password，且邮箱字段名必须是 new-email (对应 Pydantic 的 alias)
        new_email = "new_email@tsinghua.edu.cn"
        response = await client.put(
            EDIT_EMAIL_API_PATH,
            json={"password": VALID_PASSWORD, "new-email": new_email},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # 8c. 第三次请求：修改密码
        new_password = "newpassword456"
        response = await client.put(
            EDIT_PASSWORD_API_PATH,
            json={"old_password": VALID_PASSWORD,
                  "new_password": new_password},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # 8d. 第四次请求：修改头像
        with patch(
            "services.user_service.db_update_user_profile", new_callable=AsyncMock
        ) as mock_db:
            mock_db.return_value = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as ac:
                files = {"file": ("test.png", b"fake_data", "image/png")}
                response = await ac.put(
                    "/api/user/edit/portrait", files=files, headers=auth_headers
                )
            assert response.status_code == 200
            data = response.json()
            saved_path = data["filekey"].lstrip("/")
            if os.path.exists(saved_path):
                os.remove(saved_path)  # 清理测试产生的图片

        # 8e. 测试上传超过 2MB 的超大文件会被拒绝
        # 伪造一个大于 2MB 的垃圾数据 (2MB + 1KB)
        large_file_content = b"0" * (2 * 1024 * 1024 + 1024)
        filename = "too_large_avatar.png"
        # 发起请求
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            files = {"file": (filename, large_file_content, "image/png")}
            response = await ac.put(
                "/api/user/edit/portrait", files=files, headers=auth_headers
            )
        # 断言结果：期望被拦截，并返回 400 状态码
        assert response.status_code == 400
        assert "不能超过 2MB" in response.text
        # 上传 txt 文件作为头像
        files = {
            "file": (
                "test.txt",
                b"Hello, I am a text file",
                "text/plain")}
        response = await client.put(
            "/api/user/edit/portrait", files=files, headers=auth_headers
        )
        assert response.status_code == 400
        assert "不支持的图片格式" in response.text

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

        # --- 修改点 A：验证返回包里是否有短路验证码 ---
        forget_res_data = response.json()
        assert "code" in forget_res_data
        assert forget_res_data["verification_code"] == "123456"  # 确认拿到了码
        # ------------------------------------------

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

        # 9b. 验证码是对的，但该邮箱根本没注册过账号
        ghost_email = "ghost@tsinghua.edu.cn"
        # 第一步：巧妙利用“注册发码”接口，往 Redis 里强制塞入这个邮箱的有效验证码
        await client.post("/api/users/register/email", json={"email": ghost_email})
        # 第二步：拿着有效的验证码，去请求“重置密码”接口
        # 此时代码会通过 db_verify_code(Redis校验)，但在 db_get_user_by_email 时查不到数据
        response = await client.post(
            "/api/users/register/forgetpswdset",
            json={
                "email": ghost_email,
                "password": "new_password123",
                "verification_code": "123456",
            },
        )
        assert response.status_code == 404
        assert "未找到绑定该邮箱的账号" in response.text

        # ---------------------------------------------------------
        # 10. Delete Account
        # ---------------------------------------------------------
        response = await client.post(
            LOGIN_API_PATH,
            json={"id": str(user_id), "password": "recoveredpassword"},
        )
        assert response.status_code == 200
        token_for_logout = response.json()["token"]

        response = await client.post(
            "/api/users/logout", headers=get_auth_headers(token_for_logout)
        )
        assert response.status_code == 200

        # 为了防止登出导致旧 Token 失效，重新登录拿一个新 Token 去执行终极删号操作
        response = await client.post(
            LOGIN_API_PATH,
            json={"id": str(user_id), "password": "recoveredpassword"},
        )
        new_token_for_delete = response.json()["token"]
        delete_headers = get_auth_headers(new_token_for_delete)

        # 10a. 测试密码错误的情况 (400)
        response = await client.post(
            "/api/users/delete",
            headers=delete_headers,
            json={"password": "wrong_password_here"},  # 故意传错
        )
        assert response.status_code == 400
        assert "密码错误" in response.json()["msg"]

        # =========================================================
        # 🌟 新增：10b. 准备数据，让该用户成为一个群聊的群主
        # =========================================================
        async for conn in get_db_conn():
            # 强行插入一个群聊
            test_group_id = await conn.fetchval(
                "INSERT INTO conversation (type, conversation_name)"
                "VALUES ('group', '注销连带解散测试群') RETURNING conversation_id;"
            )
            # 强行将当前用户设为群主
            await conn.execute(
                "INSERT INTO conversation_member (conversation_id, member_user_id, role) VALUES ($1, $2, 'owner');",
                test_group_id, user_id
            )
            break

        # =========================================================
        # 🌟 新增：10c. 尝试常规注销 -> 预期被拦截，因为他是群主
        # =========================================================
        response = await client.post(
            "/api/users/delete",
            headers=delete_headers,
            json={"password": "recoveredpassword"},
        )
        assert response.status_code == 400
        assert "您还是以下群聊的群主" in response.text
        assert "注销连带解散测试群" in response.text

        # =========================================================
        # 🌟 新增：10d. 使用强制注销接口连带解散群聊
        # 注意：这里假设你的强力注销路由设计为 /api/users/delete/force，如果不同请自行修改！
        # =========================================================
        response = await client.post(
            "/api/users/delete/force",
            headers=delete_headers,
            json={"password": "recoveredpassword"},
        )
        assert response.status_code == 200
        assert "连带解散了" in response.text

        # =========================================================
        # 🌟 新增：10e. 验证注销后无法再次登录，且群聊已物理解散
        # =========================================================
        # 验证无法登录
        response = await client.post(
            LOGIN_API_PATH,
            json={"id": str(user_id), "password": "recoveredpassword"},
        )
        assert response.status_code == 400

        # 验证数据库中群聊状态变成了 is_disbanded = true
        async for conn in get_db_conn():
            is_disbanded = await conn.fetchval(
                "SELECT is_disbanded FROM conversation WHERE conversation_id = $1;",
                test_group_id
            )
            assert is_disbanded is True, "强力注销后，群聊没有被成功标记为已解散！"

            # 顺手验证群成员是否被清空
            member_count = await conn.fetchval(
                "SELECT count(1) FROM conversation_member WHERE conversation_id = $1;",
                test_group_id
            )
            assert member_count == 0, "强力注销后，群聊成员没有被物理清空！"
            break

        # =========================================================
        # 🌟 新增：10f. 测试 Token 合法但数据库中找不到该用户 (原孤立测试)
        # =========================================================
        # 此时用户刚刚经过 delete/force 被物理删除了，但 new_token_for_delete 在时间上还没过期
        res_info_not_found = await client.get(
            "/api/user/info",
            headers=delete_headers  # 拿着注销前生成的有效 Token 去请求
        )
        # 预期：鉴权层通过了 Token 格式校验，但在 Service 层查库时发现人没了
        assert res_info_not_found.status_code == 401
        assert "已注销" in res_info_not_found.text or "失效" in res_info_not_found.text
