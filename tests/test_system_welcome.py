import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch

from main import app
from core.exceptions import setup_exception_handlers
from db.database import get_db_conn  # 必须引入这个，用来造数据

setup_exception_handlers(app)

NEW_USER_EMAIL = "new_system_tester@tsinghua.edu.cn"
NEW_USER_NAME = "system_tester"
NEW_USER_PASSWORD = "password123"


@pytest.mark.asyncio(loop_scope="session")
@patch("services.user_service.generate_verification_code", return_value="666666")
@patch("core.ws_manager.manager.send_personal_message")
async def test_registration_triggers_system_message(mock_ws_send, mock_generate_code):
    """
    测试新用户注册时，系统是否成功创建了会话并发送了欢迎消息。
    """
    # ==========================================
    # 0. 【强行注入】先给测试数据库塞入 10000 号！
    # ==========================================
    async for conn in get_db_conn():
        await conn.execute("""
            INSERT INTO user_account (user_id, username, password, email)
            VALUES (10000, '系统通知助手', 'system_fake_password', 'system@ur-im.com')
            ON CONFLICT (user_id) DO NOTHING;
        """)
        break  # 拿一次连接执行完就退出

    # ==========================================
    # 1. 下面开始走正常的 HTTP 注册流程
    # ==========================================
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:

        # 触发验证码
        res_email = await client.post("/api/user/register/email", json={"email": NEW_USER_EMAIL})

        # 执行注册
        res_reg = await client.post(
            "/api/user/register",
            json={
                "username": NEW_USER_NAME,
                "password": NEW_USER_PASSWORD,
                "email": NEW_USER_EMAIL,
                "verification_code": "666666",
            },
        )
        assert res_reg.status_code == 200
        res_data = res_reg.json()
        assert "id" in res_data

        new_user_id = res_data["id"]

        # ==========================================
        # 2. 终极断言：验证消息推送
        # ==========================================
        assert mock_ws_send.called, "系统欢迎消息没有被推送！"

        # 偷看邮局拦截下来的第一封信件
        sent_ws_data = mock_ws_send.call_args_list[0][0][0]
        target_user_id = mock_ws_send.call_args_list[0][0][1]

        assert target_user_id == new_user_id
        assert sent_ws_data["type"] == "NEW_CHAT_MESSAGE"

        msg_payload = sent_ws_data["data"]
        assert msg_payload["sender_id"] == 10000
        assert "欢迎来到 UR-IM" in msg_payload["content"]
        assert "系统小助手" in msg_payload["content"]

        print(
            f"\n✅ 成功拦截到发给新用户({new_user_id})的系统欢迎信！内容为: {
                msg_payload['content']}")
