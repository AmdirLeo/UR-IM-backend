import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict

# 引入项目核心依赖
from main import app
from api.routes.conversation import router as conversation_router
from core.exceptions import setup_exception_handlers

from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
# 前缀保持 /api/conversation
app.include_router(conversation_router, prefix="/api/conversation")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
async def test_conversation_journey_and_edge_cases():
    """
    全量会话(Conversation)功能的 E2E 测试。
    不使用任何 Mock，完全基于真实的测试数据库流转！
    """
    # ==========================================
    # 0. 准备测试数据：创建 1 个用户和 1 个群聊，并造一条假消息用于已读测试
    # ==========================================
    user_id = None
    conv_id = None
    msg_id = None

    async for conn in get_db_conn():
        hashed_pw = get_password_hash("password123")
        # 1. 创建测试用户
        user_id = await db_create_user(conn, "conv_tester", hashed_pw, "conv@test.com")  # type: ignore

        # 2. 强行在底层创建一个会话，并把该用户拉入会话
        conv_id = await conn.fetchval("INSERT INTO conversation (type) VALUES ('single') RETURNING conversation_id;")
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id, read_index) VALUES ($1, $2, 1);",
            conv_id,
            user_id,
        )

        # 3. 强行造一条消息，用于后面的 read_ack (已读上报) 测试
        msg_id = await conn.fetchval(
            'INSERT INTO message (msg_body) VALUES (\'{"text": "test"}\'::jsonb) RETURNING msg_id;'
        )
        await conn.execute(
            "INSERT INTO conversation_message (conversation_id, msg_id, sender_id, seq_id) VALUES ($1, $2, $3, 1);",
            conv_id,
            msg_id,
            user_id,
        )
        # 给用户收件箱塞一条未读消息
        await conn.execute(
            "INSERT INTO user_inbox (user_id, conversation_id, msg_id, is_read) VALUES ($1, $2, $3, false);",
            user_id,
            conv_id,
            msg_id,
        )
        break  # 取一次连接执行完毕即可

    # 为用户生成真实的 JWT Token
    token = create_access_token(data={"sub": str(user_id)})
    headers = get_auth_headers(token)

    # 开始端到端 HTTP 测试
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:

        # ---------------------------------------------------------
        # 1. 消息免打扰 (Mute)
        # ---------------------------------------------------------
        # 开启免打扰
        res = await client.put(
            "/api/conversation/mute",
            json={"conversation_id": conv_id, "is_muted": True},
            headers=headers,
        )
        assert res.status_code == 200

        # 关闭免打扰
        res = await client.put(
            "/api/conversation/mute",
            json={"conversation_id": conv_id, "is_muted": False},
            headers=headers,
        )
        assert res.status_code == 200

        # ---------------------------------------------------------
        # 2. 会话置顶 (Pin)
        # ---------------------------------------------------------
        # 开启置顶
        res = await client.put(
            "/api/conversation/pin",
            json={"conversation_id": conv_id, "is_pinned": True},
            headers=headers,
        )
        assert res.status_code == 200

        # ---------------------------------------------------------
        # 3. 异常边界测试：试图操作一个自己不在的会话 (或者根本不存在的会话)
        # ---------------------------------------------------------
        # 试图操作 ID 为 999999 的幽灵会话
        res_bad = await client.put(
            "/api/conversation/mute",
            json={"conversation_id": 999999, "is_muted": True},
            headers=headers,
        )
        # 对应 MessageException(MessageErrors.NotInConversation)
        assert res_bad.status_code >= 400

        # ---------------------------------------------------------
        # 4. 同步会话列表 (Sync)
        # ---------------------------------------------------------
        res = await client.get("/api/conversation/sync", headers=headers)
        assert res.status_code == 200
        sync_data = res.json()["data"]
        # 因为我们前面造了数据，所以这里必定能拉取到列表
        assert len(sync_data) >= 1
        # 验证拉取到的会话 ID 是对的
        assert sync_data[0]["conversation_id"] == conv_id

        # ---------------------------------------------------------
        # 5. 消息已读上报 (Read Acknowledgement)
        # ---------------------------------------------------------
        res = await client.post(
            "/api/conversation/read_ack",
            json={"conversation_id": conv_id, "msg_id": msg_id},
            headers=headers,
        )
        assert res.status_code == 200

        # 可选断言：已读后，再次拉取 sync 或者未读数接口，红点应该消失
        res_sync_after_read = await client.get("/api/conversation/sync", headers=headers)
        sync_data_after = res_sync_after_read.json()["data"]
        assert sync_data_after[0]["unread_count"] == 0
