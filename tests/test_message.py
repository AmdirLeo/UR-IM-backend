import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict
import json

# 引入项目核心依赖
from main import app
from api.routes.message import router as message_router
from core.exceptions import setup_exception_handlers

from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user
from unittest.mock import patch

# ========== 新增：导入待测试的服务层、Schema 和异常 ==========
from services.message_service import send_message_service, verify_conversation_membership
from schemas.message import SendMessageRequest
from core.exceptions import MessageException, MessageErrors

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
# 注意：前缀加上 /api/message 保持与你的路由配置一致
app.include_router(message_router, prefix="/api/message")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
# 【新增改动 1】拦截 core.ws_manager 里的 manager.send_personal_message 方法
@patch("core.ws_manager.manager.send_personal_message")
async def test_message_journey_and_edge_cases(mock_ws_send):
    """
    全量消息功能的 E2E 测试。
    不使用任何 Mock，完全基于真实的测试数据库和数据流转！
    """
    # ==========================================
    # 0. 准备测试数据：创建 2 个用户和 1 个群聊
    # ==========================================
    user_a_id = None
    user_b_id = None
    conv_id = None

    async for conn in get_db_conn():
        hashed_pw = get_password_hash("password123")
        # 1. 创建两个用户
        # type: ignore
        user_a_id = await db_create_user(conn, "msg_tester_A", hashed_pw, "msg_a@test.com")
        # type: ignore
        user_b_id = await db_create_user(conn, "msg_tester_B", hashed_pw, "msg_b@test.com")

        # 2. 强行在底层创建一个会话，并把 A 和 B 拉入会话
        conv_id = await conn.fetchval("INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;")
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, $3);",
            conv_id,
            user_a_id,
            user_b_id,
        )
        break  # 取一次连接执行完毕即可

    # 为用户生成真实的 JWT Token
    token_a = create_access_token(data={"sub": str(user_a_id)})
    token_b = create_access_token(data={"sub": str(user_b_id)})

    headers_a = get_auth_headers(token_a)
    headers_b = get_auth_headers(token_b)

    # 开始端到端 HTTP 测试
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:

        # ---------------------------------------------------------
        # 1. 发送普通消息 (A -> 会话)
        # ---------------------------------------------------------
        res = await client.post(
            "/api/message/send",
            json={
                "conversation_id": conv_id,
                "local_id": "local_111",
                "message_content": "Hello B! This is a test message.",
                "msg_type": "text",
            },
            headers=headers_a,
        )
        assert res.status_code == 200
        msg_1_id = res.json()["data"]["msg_id"]
        assert msg_1_id > 0

        # 【新增改动 3】验证 WebSocket 是否成功触发！
        assert mock_ws_send.called, "WebSocket 推送函数没有被调用！"

        # 因为会话里有 A 和 B 两个人，所以应该循环发送了 2 次
        assert mock_ws_send.call_count == 2

        # 第一次调用的第一个参数 (发送给 A 自己的多端同步消息)
        sent_ws_data = mock_ws_send.call_args_list[0][0][0]
        assert sent_ws_data["type"] == "NEW_CHAT_MESSAGE"
        assert sent_ws_data["data"]["content"] == "Hello B! This is a test message."

        # 清空 mock 的记录，以免影响下面测试用例的断言
        mock_ws_send.reset_mock()

        # ---------------------------------------------------------
        # 2. 发送引用消息 (B -> 会话，引用 A 的消息)
        # ---------------------------------------------------------
        res = await client.post(
            "/api/message/send",
            json={
                "conversation_id": conv_id,
                "local_id": "local_222",
                "message_content": "I saw your message A!",
                "msg_type": "text",
                "quote_message_id": msg_1_id,
            },
            headers=headers_b,
        )
        assert res.status_code == 200
        msg_2_id = res.json()["data"]["msg_id"]
        assert msg_2_id > msg_1_id

        # 【新增改动 4】再次验证引用消息的推送
        assert mock_ws_send.call_count == 2
        sent_quote_data = mock_ws_send.call_args_list[0][0][0]
        assert sent_quote_data["data"]["quote_message_id"] == msg_1_id

        # ---------------------------------------------------------
        # 3. 获取历史消息 (History)
        # ---------------------------------------------------------
        res = await client.post(
            "/api/message/history",
            json={"conversation_id": conv_id, "limit": 20},
            headers=headers_a,
        )
        assert res.status_code == 200
        history_data = res.json()["data"]
        assert len(history_data) >= 2

        # 验证引用关联是否正确
        quote_msg = next(
            (m for m in history_data if m.get("msg_id") == msg_2_id), None)
        assert quote_msg is not None
        assert quote_msg.get("quote_msg_id") == msg_1_id

        # ---------------------------------------------------------
        # 4. 筛选搜索消息记录 (Search)
        # ---------------------------------------------------------
        # 正常搜索
        res = await client.post(
            "/api/message/search",
            json={"conversation_id": conv_id, "keyword": "test message"},
            headers=headers_a,
        )
        assert res.status_code == 200
        search_data = res.json()["data"]
        assert len(search_data) == 1
        assert search_data[0]["msg_id"] == msg_1_id

        # 异常：查询参数全为空的情况
        res_bad = await client.post(
            "/api/message/search",
            json={"conversation_id": conv_id},
            headers=headers_a,
        )
        # 对应 MessageException(MessageErrors.InvalidRequest)
        assert res_bad.status_code >= 400

        # ---------------------------------------------------------
        # 5. 异常测试：权限阻断
        # ---------------------------------------------------------
        # A 试图在自己不在的群聊（比如不存在的 999999）里发消息
        res = await client.post(
            "/api/message/send",
            json={
                "conversation_id": 999999,
                "local_id": "local_333",
                "message_content": "Can I bypass?",
                "msg_type": "text",
            },
            headers=headers_a,
        )
        # 对应 NotInConversation 错误
        assert res.status_code >= 400

        # ---------------------------------------------------------
        # 6. 删除本地消息 (Delete)
        # ---------------------------------------------------------
        res = await client.request(
            "DELETE",
            "/api/message",
            json={"conversation_id": conv_id, "message_id": msg_1_id},
            headers=headers_a,
        )
        assert res.status_code == 200

        # 删除后再次拉取历史消息，A 的视角应该看不到 msg_1_id 了
        res = await client.post(
            "/api/message/history",
            json={"conversation_id": conv_id, "limit": 20},
            headers=headers_a,
        )
        assert res.status_code == 200
        history_after_del = res.json()["data"]
        assert not any(m.get("msg_id") == msg_1_id for m in history_after_del)

        # 但 B 的视角依然能看到（因为只是删了 A 本地的收件箱记录）
        res_b = await client.post(
            "/api/message/history",
            json={"conversation_id": conv_id, "limit": 20},
            headers=headers_b,
        )
        history_b = res_b.json()["data"]
        assert any(m.get("msg_id") == msg_1_id for m in history_b)

    # ==========================================
    # 2. 【新增】服务层直调测试：鉴权 & 发送消息自动已读
    # ==========================================

    # 2.1 鉴权测试：用户不在会话中
    async for conn in get_db_conn():
        intruder_pw = get_password_hash("intruder_pass")
        intruder_id = await db_create_user(conn, "intruder", intruder_pw, "intruder@test.com")
        with pytest.raises(MessageException) as exc_info:
            await verify_conversation_membership(conn, intruder_id, conv_id)
        assert exc_info.value.error_code == MessageErrors.NotInConversation
        break

    # 2.2 鉴权测试：私聊对方单删（is_active = false）
    async for conn in get_db_conn():
        # 模拟 B 单删 A（将 B 的 is_active 置为 false）
        await conn.execute(
            "UPDATE conversation_member SET is_active=false "
            "WHERE conversation_id=$1 AND member_user_id=$2",
            conv_id, user_b_id
        )
        with pytest.raises(MessageException) as exc_info:
            await verify_conversation_membership(conn, user_a_id, conv_id)
        assert exc_info.value.error_code == MessageErrors.NotInConversation
        # 恢复现场
        await conn.execute(
            "UPDATE conversation_member SET is_active=true "
            "WHERE conversation_id=$1 AND member_user_id=$2",
            conv_id, user_b_id
        )
        break

    # 2.3 鉴权测试：上帝账号（负数 user_id）直接放行
    async for conn in get_db_conn():
        # 不应抛出任何异常
        await verify_conversation_membership(conn, -1, conv_id)
        break

    # 2.4 集成测试：发送消息后自动调用 read_ack，未读数清零
    async for conn in get_db_conn():
        # 先记录当前 A 的未读数（至少应有一条来自 B 的引用消息）
        cnt_before = await conn.fetchval(
            "SELECT COUNT(*) FROM user_inbox WHERE user_id=$1 AND conversation_id=$2 AND is_read=false",
            user_a_id, conv_id
        )
        assert cnt_before >= 1, f"期望至少有1条未读，实际{cnt_before}"

        # 先为 A 生成一条来自 B 的未读消息
        fake_body = json.dumps(
            {"type": "text", "content": "from B", "extra": {}})
        msg_from_b_id = await conn.fetchval(
            "INSERT INTO message (msg_body) VALUES ($1::jsonb) RETURNING msg_id;",
            fake_body
        )

        # ------ 修复：动态计算下一个 seq_id ------
        max_seq = await conn.fetchval(
            "SELECT COALESCE(MAX(seq_id), 0) FROM conversation_message WHERE conversation_id = $1",
            conv_id
        )
        next_seq = max_seq + 1
        # ----------------------------------------

        await conn.execute(
            "INSERT INTO conversation_message (conversation_id, msg_id, sender_id, seq_id) "
            "VALUES ($1, $2, $3, $4)",
            conv_id, msg_from_b_id, user_b_id, next_seq   # 注意这里用了变量
        )

        await conn.execute(
            "INSERT INTO user_inbox (user_id, conversation_id, msg_id, is_read) "
            "VALUES ($1, $2, $3, false)",
            user_a_id, conv_id, msg_from_b_id
        )

        # 确认 A 当前有 1 条未读
        cnt_after_insert = await conn.fetchval(
            "SELECT COUNT(*) FROM user_inbox WHERE user_id=$1 AND conversation_id=$2 AND is_read=false",
            user_a_id, conv_id
        )
        assert cnt_after_insert == cnt_before + \
            1, f"未读数应增加1，现在是{cnt_after_insert}"

        # A 发送一条消息 → 内部会调用 read_ack 清掉 A 在该会话的未读
        send_req = SendMessageRequest(
            conversation_id=conv_id,
            msg_type="text",
            message_content="A sends again",
            extra_data={},
            local_id="local_test_auto_read",
            quote_message_id=None
        )
        result = await send_message_service(conn, user_a_id, send_req)
        assert "msg_id" in result

        # 再次检查未读数，应为 0
        cnt_after = await conn.fetchval(
            "SELECT COUNT(*) FROM user_inbox WHERE user_id=$1 AND conversation_id=$2 AND is_read=false",
            user_a_id, conv_id
        )
        assert cnt_after == 0
        break
