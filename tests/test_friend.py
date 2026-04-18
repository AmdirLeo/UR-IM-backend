import pytest
import json
from typing import Dict
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

# 引入项目核心依赖
from main import app
from api.routes.friend import router
from core.exceptions import setup_exception_handlers
from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user

# ==========================================
# Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
# 注意：确保这里不会重复 include 导致路由冲突。如果 main.py 已经包含了，可注释掉下面这行。
app.include_router(router, prefix="/api")

# 测试专用常量
SYSTEM_ID = -1
TEST_USER_A_EMAIL = "apply_sender@ur-im.com"
TEST_USER_B_EMAIL = "apply_receiver@ur-im.com"


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ==========================================
# 测试用例 1：全量好友功能业务流转 E2E 测试
# ==========================================
@pytest.mark.asyncio(loop_scope="session")
async def test_friend_journey_and_edge_cases():
    """
    全量好友功能的 E2E 测试。
    不使用任何 Mock，完全基于真实的测试数据库和数据流转！
    """
    user_a_id = None
    user_b_id = None
    user_c_id = None

    async for conn in get_db_conn():
        await conn.execute("""
            INSERT INTO user_account (user_id, username, password, email)
            VALUES ($1, '系统管家', 'nopass', 'sys_admin@ur-im.com')
            ON CONFLICT (user_id) DO NOTHING
        """, SYSTEM_ID)
        
        hashed_pw = get_password_hash("password123")
        # type: ignore
        user_a_id = await db_create_user(conn, "friend_user_A", hashed_pw, "friend_a@test.com")
        # type: ignore
        user_b_id = await db_create_user(conn, "friend_user_B", hashed_pw, "friend_b@test.com")
        # type: ignore
        user_c_id = await db_create_user(conn, "friend_user_C", hashed_pw, "friend_c@test.com")
        break

    token_a = create_access_token(data={"sub": str(user_a_id)})
    token_b = create_access_token(data={"sub": str(user_b_id)})

    headers_a = get_auth_headers(token_a)
    headers_b = get_auth_headers(token_b)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:

        # 1. 搜索用户 (Search)
        res = await client.get("/api/friend/search?keyword=friend_user_B", headers=headers_a)
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) >= 1
        assert data[0]["username"] == "friend_user_B"

        # ==========================================
        # 👇 这是为你新增的：测试获取其他用户信息功能
        # ==========================================
        # 1.5 查看目标用户详细信息 (Get Other User Info)
        # 注意：如果你的路由前缀不同，请将 /api/user/info 替换为你实际的路径 (比如 /api/friend/info)
        res_info = await client.get(f"/api/friend/info/{user_b_id}", headers=headers_a)
        assert res_info.status_code == 200
        user_info = res_info.json()

        # 根据你之前定义的 UserInfoResponse 结构进行断言
        assert user_info["id"] == user_b_id
        assert user_info["username"] == "friend_user_B"
        assert "email" in user_info  # 验证返回了邮箱字段

        # 1.6 边界情况 (Edge Case)：查询一个根本不存在的 user_id
        res_404 = await client.get("/api/friend/info/9999999", headers=headers_a)
        # 验证 Service 层抛出的 BusinessException(status_code=404) 被正确处理
        assert res_404.status_code == 404
        # ==========================================
        # 👆 新增结束
        # ==========================================

        # 2. 发送好友申请 (Apply)
        res = await client.post(
            "/api/friend/apply",
            json={"target_user_id": user_b_id, "message": "hello B"},
            headers=headers_a,
        )
        assert res.status_code == 200

        # A 不能申请加自己
        res = await client.post(
            "/api/friend/apply",
            json={"target_user_id": user_a_id, "message": "hello me"},
            headers=headers_a,
        )
        assert res.status_code == 400

        # 3. 处理好友申请 (Handle)
        request_id = None
        async for conn in get_db_conn():
            req_record = await conn.fetchrow(
                "SELECT request_id FROM friend_request WHERE sender_id=$1 AND receiver_id=$2",
                user_a_id, user_b_id,
            )
            request_id = req_record["request_id"]
            break

        res = await client.post(
            "/api/friend/handle",
            json={"request_id": request_id, "action": "accepted"},
            headers=headers_b,
        )
        assert res.status_code == 200

        handle_data = res.json().get("data", {})
        assert handle_data is not None, "返回的 data 字段不应为空"
        assert "conversation_id" in handle_data, "返回的 data 中缺少 conversation_id"
        assert isinstance(handle_data["conversation_id"], int), "conversation_id 应该是一个整数"

        # 4. 获取好友列表 (Get List)
        res = await client.get("/api/friend", headers=headers_a)
        assert res.status_code == 200
        friends = res.json()["data"]
        assert any(f["user_id"] == user_b_id for f in friends)

        # ---------------------------------------------------------
        # 5. 好友分组标签流转 (Tag Journey) - 多标签版
        # ---------------------------------------------------------
        tag_1 = "BestFriends"
        tag_2 = "Colleagues"

        # 创建两个标签
        await client.post("/api/friend/tag/new", json={"tag_name": tag_1}, headers=headers_a)
        await client.post("/api/friend/tag/new", json={"tag_name": tag_2}, headers=headers_a)

        # ==========================================
        # 👇 这是为你新增的：测试获取标签列表功能
        # ==========================================
        res_tag_list = await client.get("/api/friend/tag/list", headers=headers_a)
        assert res_tag_list.status_code == 200
        tag_list_data = res_tag_list.json().get("data", [])

        # 断言返回的是列表，并且刚刚创建的两个标签都在列表内
        assert isinstance(tag_list_data, list), "返回的 data 应该是一个列表"
        assert tag_1 in tag_list_data, f"标签列表中缺少刚创建的 {tag_1}"
        assert tag_2 in tag_list_data, f"标签列表中缺少刚创建的 {tag_2}"
        # ==========================================
        # 👆 新增结束
        # ==========================================

        # 创建两个标签
        await client.post("/api/friend/tag/new", json={"tag_name": tag_1}, headers=headers_a)
        await client.post("/api/friend/tag/new", json={"tag_name": tag_2}, headers=headers_a)

        # 把 user_b 同时加入两个标签
        await client.post("/api/friend/tag/add", json={"tag_name": tag_1, "friend_ids": [user_b_id]}, headers=headers_a)
        await client.post("/api/friend/tag/add", json={"tag_name": tag_2, "friend_ids": [user_b_id]}, headers=headers_a)

        # 🚨 验证多标签功能：拉取好友列表并检查 Pydantic 模型是否能正确序列化数组
        res_list = await client.get("/api/friend", headers=headers_a)
        assert res_list.status_code == 200
        friends_data = res_list.json()["data"]

        # 找出 user_b 的数据
        user_b_data = next(
            f for f in friends_data if f["user_id"] == user_b_id)

        # 断言：user_b 的 tags 字段必须是一个列表，并且同时包含这两个标签
        assert isinstance(user_b_data["tags"], list)
        assert tag_1 in user_b_data["tags"]
        assert tag_2 in user_b_data["tags"]

        # 后续的清理测试（可以只清理其中一个，测一下删除功能）
        await client.post("/api/friend/tag/remove", json={"tag_name": tag_1, "friend_id": user_b_id}, headers=headers_a)
        await client.post("/api/friend/tag/delete", json={"tag_name": tag_1}, headers=headers_a)

        # ==========================================
        # 👇 可选新增：验证删除后，tag_1 确实从标签列表中消失了
        # ==========================================
        res_tag_list_after_delete = await client.get("/api/friend/tag/list", headers=headers_a)
        assert res_tag_list_after_delete.status_code == 200
        tag_list_data_after = res_tag_list_after_delete.json().get("data", [])
        assert tag_1 not in tag_list_data_after, f"删除失败，{tag_1} 仍然存在于列表中"
        assert tag_2 in tag_list_data_after, f"误删，{tag_2} 应该还在列表中"
        # ==========================================

        # ---------------------------------------------------------
        # 5.5. 模拟两人聊天 (生成 conv_id 和历史记录)
        # ---------------------------------------------------------

        # 1. 直接从数据库查询两人已经建好的私聊会话 ID
        # 1. 🌟 [完美升级] 调接口获取 A 和 B 的私聊会话 ID (替代了以前冗长的 SQL 查询)
        res_conv = await client.get(
            f"/api/conversation/direct/{user_b_id}",
            headers=headers_a
        )
        assert res_conv.status_code == 200, "获取私聊会话 ID 接口报错了！"

        conv_data = res_conv.json().get("data", {})
        conv_id = conv_data.get("conversation_id")
        assert conv_id is not None, "接口没有返回 conversation_id！"

        # 1.1 边界测试：尝试获取与一个不存在的用户 (或非好友) 的会话 ID
        res_conv_404 = await client.get(
            "/api/conversation/direct/999999",
            headers=headers_a
        )
        # 验证我们的 Service 层确实抛出了 404 异常
        assert res_conv_404.status_code == 404, "安全漏洞：查不存在的好友会话竟然没报 404！"

        # 2. A 给 B (即这个私聊会话) 发一条消息
        res_msg = await client.post(
            "/api/message/send",
            json={
                "conversation_id": conv_id,  # 👈 改为标准传参
                "local_id": "local_test_123",
                "message_content": "你好，这是删好友前的测试消息",
                "msg_type": "text",
            },
            headers=headers_a
        )
        assert res_msg.status_code == 200

        # 3. B 也给 A 回复一条消息
        res_msg_b = await client.post(
            "/api/message/send",
            json={
                "conversation_id": conv_id,  # 👈 同理
                "local_id": "local_test_456",
                "message_content": "收到了",
                "msg_type": "text",
            },
            headers=headers_b
        )
        assert res_msg_b.status_code == 200

        # ---------------------------------------------------------
        # 6. 删除好友 (Remove) - 测试带删除历史的选项
        # ---------------------------------------------------------
        # A 翻脸无情，删除了 B，并且勾选了“删除聊天记录”
        res = await client.post(
            url="/api/friend/remove",
            json={                                  # 👈 传入 JSON Body
                "friend_user_id": user_b_id,
                "delete_history": True
            },
            headers=headers_a
        )
        assert res.status_code == 200

        # 验证 A 的好友列表确实空了
        res = await client.get("/api/friend", headers=headers_a)
        friends = res.json()["data"]
        assert not any(f["user_id"] == user_b_id for f in friends)

        # ---------------------------------------------------------
        # 7. 终极验证：检查双端收件箱的“物理隔离”删除效果
        # ---------------------------------------------------------

        # 验证 1：A 勾选了删除历史，所以 A 拉取该会话的历史消息应该为空
        res_history_a = await client.post(
            "/api/message/history",
            json={"conversation_id": conv_id, "limit": 20},
            headers=headers_a,
        )
        # 💡 核心修改：预期状态码就是 403，证明 A 无权再看该房间信息
        assert res_history_a.status_code == 200
        assert len(res_history_a.json()["data"]) == 0, "核心逻辑错误：A 的历史记录没有被成功清空！"

        # 验证 2：B 作为被动方，Ta 的历史记录必须毫发无损！
        res_history_b = await client.post(
            "/api/message/history",
            json={"conversation_id": conv_id, "limit": 20},
            headers=headers_b,
        )
        assert res_history_b.status_code == 200
        # 断言 B 依然能拉取到之前发的那 2 条消息
        assert len(res_history_b.json()["data"]) >= 2, "严重 Bug：B 的聊天记录被误删了！"
        
        
        # ---------------------------------------------------------
        # 8. [新增] 终极验证：重新加回好友，测试会话 ID 是否完美复用！
        # ---------------------------------------------------------
        # A 厚着脸皮再次申请加 B
        await client.post(
            "/api/friend/apply", 
            json={"target_user_id": user_b_id, "message": "求求你加回我吧"}, 
            headers=headers_a
        )

        # 获取最新的 request_id
        async for conn in get_db_conn():
            req_record = await conn.fetchrow(
                "SELECT request_id FROM friend_request WHERE sender_id=$1 AND receiver_id=$2 ORDER BY create_time DESC LIMIT 1",
                user_a_id, user_b_id,
            )
            request_id_new = req_record["request_id"]
            break

        # B 再次同意
        res_re_accept = await client.post(
            "/api/friend/handle", 
            json={"request_id": request_id_new, "action": "accepted"}, 
            headers=headers_b
        )
        assert res_re_accept.status_code == 200

        # 提取这次生成的会话 ID
        new_conv_id = res_re_accept.json()["data"]["conversation_id"]

        # 💡 核心断言：这里的 conv_id 是你在前面步骤 5.5 获取的那个旧 ID
        assert new_conv_id == conv_id, "底层架构 Bug：重新加好友产生了新的会话，没有复用旧的！"


# ==========================================
# 测试用例 2：A 申请加好友，B 收到系统助手发来的 JSONB 多态卡片
# ==========================================
@pytest.mark.asyncio(loop_scope="session")
@patch("core.ws_manager.manager.send_personal_message")
async def test_friend_request_triggers_system_card(mock_ws_send):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # 1. 环境准备：创建 A, B, -1 号及 B 的系统会话
        async for conn in get_db_conn():
            hashed_pw = get_password_hash("testpassword")
            await conn.execute("""
                INSERT INTO user_account (user_id, username, password, email)
                VALUES ($1, '系统助手', 'nopass', 'sys_bot1@ur-im.com')
                ON CONFLICT (user_id) DO NOTHING
            """, SYSTEM_ID)

            u_a = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) VALUES ('UserA_Apply', $1, $2) RETURNING user_id",
                hashed_pw, TEST_USER_A_EMAIL
            )
            user_a_id = u_a['user_id']

            u_b = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) "
                "VALUES ('UserB_Receiver', $1, $2) RETURNING user_id",
                hashed_pw, TEST_USER_B_EMAIL
            )
            user_b_id = u_b['user_id']

            sys_conv_id = await conn.fetchval(
                "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id"
            )
            await conn.execute(
                "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, $3)",
                sys_conv_id, user_b_id, SYSTEM_ID
            )
            break

        login_res = await client.post("/api/user/login", json={"id": TEST_USER_A_EMAIL, "password": "testpassword"})
        token_a = login_res.json()["token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. 发起好友申请
        apply_res = await client.post(
            "/api/friend/apply",  # 确保路由与你的 controller 一致
            json={"target_user_id": user_b_id, "message": "你好，我是 User A"},
            headers=headers_a
        )
        assert apply_res.status_code == 200

        # 3. 验证多态卡片 (extra_data)
        assert mock_ws_send.called, "系统卡片未能发出！"

        b_received_payload = None
        for call in mock_ws_send.call_args_list:
            payload, target = call[0][0], call[0][1]
            if target == user_b_id:
                b_received_payload = payload
                break

        assert b_received_payload is not None
        assert b_received_payload["type"] == "NEW_CHAT_MESSAGE"

        message_data = b_received_payload["data"]
        assert message_data["sender_id"] == SYSTEM_ID

        # 💡 新架构断言：检查类型和纯文本摘要
        assert message_data["msg_type"] == "card", "期望类型为 'card'"
        assert message_data["content"] == "[收到一条好友申请]", "列表摘要文案不匹配"

        # 💡 新架构断言：检查 extra 字典里的具体参数
        extra = message_data.get("extra", {})
        assert extra.get("card_type") == "friend_apply"
        assert extra.get("sender_id") == user_a_id
        assert "你好，我是 User A" in extra.get("reason", "")
        assert "request_id" in extra


# ==========================================
# 测试用例 3：B 同意申请，A 收到系统通知指令
# ==========================================
@pytest.mark.asyncio(loop_scope="session")
@patch("core.ws_manager.manager.send_personal_message")
async def test_friend_accept_triggers_system_notification(mock_ws_send):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # 1. 环境准备
        async for conn in get_db_conn():
            hashed_pw = get_password_hash("pw")
            u_a = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) "
                "VALUES ('UserA_Accept', $1, $2) RETURNING user_id",
                hashed_pw, "a_accept@ur-im.com"
            )
            u_b = await conn.fetchrow(
                "INSERT INTO user_account (username, password, email) "
                "VALUES ('UserB_Accept', $1, $2) RETURNING user_id",
                hashed_pw, "b_accept@ur-im.com"
            )
            user_a_id, user_b_id = u_a['user_id'], u_b['user_id']

            await conn.execute(
                "INSERT INTO user_account (user_id, username, password, email) "
                "VALUES ($1, 'sys', '1', 'sys2@ur.im') ON CONFLICT DO NOTHING",
                SYSTEM_ID
            )


            req_id = await conn.fetchval(
                "INSERT INTO friend_request (sender_id, receiver_id, status) "
                "VALUES ($1, $2, 'pending') RETURNING request_id",
                user_a_id, user_b_id
            )
            break

        login_res = await client.post("/api/user/login", json={"id": "b_accept@ur-im.com", "password": "pw"})
        headers_b = {"Authorization": f"Bearer {login_res.json()['token']}"}

        # 2. B 同意请求
        response = await client.post(
            "/api/friend/handle",
            json={"request_id": req_id, "action": "accepted"},
            headers=headers_b
        )
        assert response.status_code == 200

        # 👇 新增：提取出后端在 HTTP 响应里返回给 B 的 conversation_id
        http_conv_id = response.json().get("data", {}).get("conversation_id")
        assert http_conv_id is not None, "HTTP 响应里漏掉了 conversation_id！"

        # 3. 验证系统通知指令 (extra_data)
        assert mock_ws_send.called

        a_received_notification = False
        for call in mock_ws_send.call_args_list:
            ws_payload, target_id = call[0][0], call[0][1]

            if target_id == user_a_id and ws_payload["type"] == "NEW_CHAT_MESSAGE":
                msg_data = ws_payload["data"]
                if msg_data.get("conversation_id") == http_conv_id:
                    # 💡 新架构断言：检查指令类型和内容
                    assert msg_data["sender_id"] == SYSTEM_ID, "发件人必须是系统上帝账号"
                    assert msg_data["msg_type"] == "notify"
                    extra = msg_data.get("extra", {})
                    assert extra.get("action") == "friend_accept"

                    
                    a_received_notification = True
                    break

        assert a_received_notification, "严重错误：User A 未能在【私聊会话】中收到系统同意通知！"
