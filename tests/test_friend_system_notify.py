import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from main import app
from db.database import get_db_conn
from core.exceptions import setup_exception_handlers
from core.security import get_password_hash

setup_exception_handlers(app)

# 测试常量
USER_A_EMAIL = "sender_a@example.com"
USER_B_EMAIL = "receiver_b@example.com"
SYSTEM_ID = 10000


@pytest.mark.asyncio(loop_scope="session")
@patch("core.ws_manager.manager.send_personal_message")
async def test_friend_accept_triggers_system_notification(mock_ws_send):
    """
    测试：B 同意 A 的好友请求后，A 是否收到系统助手的 WebSocket 通知
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # ---------------------------------------------------------
        # 1. 环境准备：创建 A, B 和 系统助手，并建立一条申请记录
        # ---------------------------------------------------------
        async for conn in get_db_conn():
            # --- 先把明文密码加密 ---
            hashed_pw = get_password_hash("pw")
            # 创建用户
            u_a = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) VALUES ('UserA', $1, $2) RETURNING user_id",
                hashed_pw, USER_A_EMAIL
            )
            u_b = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) VALUES ('UserB', $1, $2) RETURNING user_id",
                hashed_pw, USER_B_EMAIL
            )
            user_a_id = u_a['user_id']
            user_b_id = u_b['user_id']

            # 注入系统助手
            await conn.execute(
                "INSERT INTO user_account (user_id, username, password, email) "
                "VALUES ($1, '系统助手', 'nopass', 'sys@ur.im') ON CONFLICT DO NOTHING", SYSTEM_ID
            )

            # 为 User A 创建系统会话（模拟注册时的逻辑）
            sys_conv_id = await conn.fetchval(
                "INSERT INTO conversation (type) VALUES ('private') "
                "RETURNING conversation_id"
            )
            await conn.execute(
                "INSERT INTO conversation_member (conversation_id, member_user_id) "
                "VALUES ($1, $2), ($1, $3)",
                sys_conv_id, user_a_id, SYSTEM_ID
            )

            # 创建一条 A -> B 的待处理申请
            req_id = await conn.fetchval(
                "INSERT INTO friend_request (sender_id, receiver_id, status) "
                "VALUES ($1, $2, 'pending') RETURNING request_id",
                user_a_id, user_b_id
            )
            break

        # ---------------------------------------------------------
        # 2. 模拟登录：获取 User B 的 Token（因为是 B 在处理请求）
        # ---------------------------------------------------------
        login_res = await client.post("/api/user/login", json={"id": USER_B_EMAIL, "password": "pw"})
        token_b = login_res.json()["token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # ---------------------------------------------------------
        # 3. 核心动作：User B 点击“同意”
        # ---------------------------------------------------------
        # 假设你的路由是 PUT /api/friend/request/{id}/handle，动作为 accepted
        response = await client.put(
            "/api/friend/friend/handle",
            json={
                "request_id": req_id,
                "action": "accepted"
            },
            headers=headers_b
        )
        if response.status_code != 200:
            print(f"DEBUG: 错误详情: {response.json()}")

        assert response.status_code == 200

        # ---------------------------------------------------------
        # 4. 终极验证：检查 WebSocket 是否把系统消息发给了 User A
        # ---------------------------------------------------------
        # 检查是否触发了推送
        assert mock_ws_send.called, "系统通知未能通过 WebSocket 发出！"

        # 寻找发给 User A 的那一封信
        # 因为在 handle_friend_request 中，我们调用了 send_message_service
        # 而 send_message_service 内部会循环发送给会话里的所有人（A 和 10000）
        # 我们只需验证其中一次调用是发给 A 的即可

        a_received_notification = False
        for call in mock_ws_send.call_args_list:
            ws_payload = call[0][0]  # 推送的 JSON 字典
            target_id = call[0][1]   # 接收人 ID

            if target_id == user_a_id and ws_payload["type"] == "NEW_CHAT_MESSAGE":
                msg_data = ws_payload["data"]
                if msg_data["sender_id"] == SYSTEM_ID and "已同意" in msg_data["content"]:
                    a_received_notification = True
                    break

        assert a_received_notification, "User A 没有收到正确的系统通知内容"
        print(f"\n验证通过！系统已自动通知 User A({user_a_id})：User B({user_b_id})同意了申请。")
