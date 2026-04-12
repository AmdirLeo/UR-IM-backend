import pytest
import json
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

# 请根据你的实际项目结构调整这些导入
from main import app
from db.database import get_db_conn
from core.security import get_password_hash

# 测试专用的常量
TEST_USER_A_EMAIL = "apply_sender@ur-im.com"
TEST_USER_B_EMAIL = "apply_receiver@ur-im.com"
SYSTEM_ID = 10000


@pytest.mark.asyncio(loop_scope="session")
@patch("core.ws_manager.manager.send_personal_message")
async def test_friend_request_triggers_system_card(mock_ws_send):
    """
    终极集成测试：测试 A 发起好友申请后，B 是否通过 WebSocket 收到了 JSON 格式的申请卡片。
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # ==========================================
        # 1. 环境准备：创建用户 A, B，以及 B 的系统会话
        # ==========================================
        async for conn in get_db_conn():
            hashed_pw = get_password_hash("testpassword")

            # 1.1 插入系统助手 10000 (如果已存在则跳过)
            await conn.execute("""
                INSERT INTO user_account (user_id, username, password, email)
                VALUES ($1, '系统助手', 'nopass', 'sys_bot@ur-im.com')
                ON CONFLICT (user_id) DO NOTHING
            """, SYSTEM_ID)

            # 1.2 创建 User A (发送方)
            u_a = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) VALUES ('UserA_Apply', $1, $2) RETURNING user_id",
                hashed_pw, TEST_USER_A_EMAIL
            )
            user_a_id = u_a['user_id']

            # 1.3 创建 User B (接收方)
            u_b = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) "
                "VALUES ('UserB_Receiver', $1, $2) RETURNING user_id",
                hashed_pw, TEST_USER_B_EMAIL
            )
            user_b_id = u_b['user_id']

            # 1.4 为 User B 创建与 10000 号的系统会话
            sys_conv_id = await conn.fetchval(
                "INSERT INTO conversation (type) "
                "VALUES ('private') RETURNING conversation_id"
            )
            await conn.execute(
                "INSERT INTO conversation_member (conversation_id, member_user_id, role) "
                "VALUES ($1, $2, 'member'), ($1, $3, 'member')",
                sys_conv_id, user_b_id, SYSTEM_ID
            )
            break  # 释放连接

        # ==========================================
        # 2. 模拟登录：获取 User A 的 Token
        # ==========================================
        login_res = await client.post("/api/user/login", json={"id": TEST_USER_A_EMAIL, "password": "testpassword"})
        assert login_res.status_code == 200, "User A 登录失败"
        token_a = login_res.json()["token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # ==========================================
        # 3. 核心动作：User A 发起好友申请
        # ==========================================
        # 注意：这里请替换为你实际的申请路由，比如 /api/friend/apply 或 /api/friend/friend/apply
        apply_res = await client.post(
            "/api/friend/friend/apply",
            json={
                "target_user_id": user_b_id,  # 替换为你接口实际接收的字段名
                "message": "你好，我是 User A，久仰大名！"
            },
            headers=headers_a
        )
        assert apply_res.status_code == 200, f"好友申请接口报错: {apply_res.text}"

        # ==========================================
        # 4. 终极断言：解析 WebSocket 推送的“包裹”
        # ==========================================
        # 4.1 确认发信动作被触发了
        assert mock_ws_send.called, "系统卡片未能通过 WebSocket 发出！"

        # 4.2 遍历所有的推送记录，找出专门推给 User B 的那一条
        b_received_payload = None
        for call in mock_ws_send.call_args_list:
            payload, target = call[0][0], call[0][1]
            if target == user_b_id:
                b_received_payload = payload
                break

        # 4.3 验证接收者是对的
        assert b_received_payload is not None, "User B 并没有收到 WebSocket 推送！"

        # 4.4 验证消息的外壳
        assert b_received_payload["type"] == "NEW_CHAT_MESSAGE"
        message_data = b_received_payload["data"]

        # 4.5 验证发件人是 10000，且类型是好友申请
        assert message_data["sender_id"] == SYSTEM_ID
        assert message_data["msg_type"] == "friend_apply", "消息类型标签错误，期望 'friend_apply'"

        # 4.6 深入验证内部的 JSON 卡片数据！
        content = message_data["content"]
        card_dict = json.loads(content) if isinstance(
            content, str) else content

        print("\n\n 成功截获系统发出的好友申请卡片！")
        print(json.dumps(card_dict, indent=2, ensure_ascii=False))
        print("\n")

        assert card_dict["sender_id"] == user_a_id, "卡片中记录的申请人 ID 错误"
        assert "你好，我是 User A" in card_dict["reason"], "申请理由未正确传递"
        assert "request_id" in card_dict, "卡片缺失了最重要的 request_id"
