import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict

# 引入项目核心依赖
from main import app
from api.routes.message import router as message_router
from core.exceptions import setup_exception_handlers

from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user

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
async def test_message_journey_and_edge_cases():
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
