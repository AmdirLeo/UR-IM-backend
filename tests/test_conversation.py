import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict
import json

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
    friend_id = None   # 新增好友用户
    conv_id = None
    msg_id = None

    async for conn in get_db_conn():
        hashed_pw = get_password_hash("password123")
        # 1. 创建测试用户
        # type: ignore
        user_id = await db_create_user(conn, "conv_tester", hashed_pw, "conv@test.com")

        # 2. 创建好友用户（对方）
        friend_id = await db_create_user(conn, "conv_friend", hashed_pw, "friend@test.com")

        # 3. 强行在底层创建一个会话，并把该用户拉入会话
        conv_id = await conn.fetchval("INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;")

        # 4. 将两个用户都加入会话
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id, read_index) VALUES ($1, $2, 1), ($1, $3, 1);",
            conv_id, user_id, friend_id
        )

        # 5. 强行造一条消息，用于后面的 read_ack (已读上报) 测试
        fake_msg_body = {
            # 对应 msg_type (注意你的 DB 查询用的是 'type')
            "type": "text",
            # 对应 message_content (你的 DB 查询用的是 'content')
            "content": "测试消息内容",
            "local_id": "test-local-uuid-001",  # 必填的本地 ID
            "extra_data": {                   # 测试万能口袋
                "test_flag": True,
                "at_users": [2, 3]
            },
            "quote_message_id": None          # 引用消息 ID
        }

        # 使用 json.dumps 序列化，并通过 $1 安全地插入到 jsonb 字段中
        msg_id = await conn.fetchval(
            'INSERT INTO message (msg_body) VALUES ($1::jsonb) RETURNING msg_id;',
            json.dumps(fake_msg_body)
        )

        # 👇 【新增这一段】：将最新生成的 msg_id 更新到会话表的 last_msg_id 中！
        await conn.execute(
            "UPDATE conversation SET last_msg_id = $1 WHERE conversation_id = $2;",
            msg_id,
            conv_id
        )
        # 👆 新增结束

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
        sync_data = res.json()

        # 校验聚合响应的新数据结构
        assert "conversations" in sync_data
        assert "pending_friend_requests" in sync_data
        assert "pending_group_requests" in sync_data

        conv_list = sync_data["conversations"]
        assert len(conv_list) >= 1

        # 验证会话数据及新的状态机
        target_conv = conv_list[0]
        assert target_conv["conversation_id"] == conv_id
        assert target_conv["status"] == "normal"  # 正常加入的群，状态应为 normal
        # 因为之前在 inbox 里塞了一条 is_read=false
        assert target_conv["unread_count"] == 1

        assert target_conv["is_pinned"] is True   # Step 2 中我们开启了置顶
        assert target_conv["is_muted"] is False   # Step 1 中我们最后关闭了免打扰

        # ----- 新增 target_id 字段验证 -----
        assert "target_id" in target_conv, "私聊会话应包含 target_id 字段"
        assert target_conv["target_id"] == friend_id, f"target_id 应为对方用户ID {friend_id}"
        assert isinstance(target_conv["target_id"], int), "target_id 应为整数类型"

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
        # 断言：已读后，再次拉取 sync，验证对应会话的 unread_count 红点应该清零
        res_sync_after_read = await client.get("/api/conversation/sync", headers=headers)
        sync_data_after = res_sync_after_read.json()["conversations"]
        assert sync_data_after[0]["unread_count"] == 0

        # ---------------------------------------------------------
        # 6. 好友申请红点同步测试 (Pending Friend Requests Sync)
        # ---------------------------------------------------------
        # 6.1 先验证当前的未处理好友申请应该是 0（因为刚建好的号没人加他）
        res_sync_before = await client.get("/api/conversation/sync", headers=headers)
        assert res_sync_before.status_code == 200
        assert res_sync_before.json()["pending_friend_requests"] == 0

        # 6.2 在底层强制制造一条“别人发给我的”未处理好友申请
        async for conn in get_db_conn():
            # 创建一个陌生人用户
            stranger_pw = get_password_hash("password123")
            # 注意邮箱和用户名别跟上面冲突了
            stranger_id = await db_create_user(conn, "stranger_tester", stranger_pw, "stranger@test.com")

            # 插入一条待处理的好友申请 (sender=陌生人, receiver=当前测试用户, status=pending)
            await conn.execute(
                "INSERT INTO friend_request (sender_id, receiver_id, status) VALUES ($1, $2, 'pending');",
                stranger_id, user_id
            )
            break  # 操作完立马释放连接归还给连接池

        # 6.3 再次拉取 Sync 接口，验证好友申请红点数是否精准变成了 1
        res_sync_after = await client.get("/api/conversation/sync", headers=headers)
        assert res_sync_after.status_code == 200
        sync_data_friends = res_sync_after.json()
        assert sync_data_friends["pending_friend_requests"] == 1
