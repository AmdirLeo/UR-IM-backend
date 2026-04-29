import pytest
import asyncpg
import json
from httpx import AsyncClient, ASGITransport
from typing import Dict, cast
from unittest.mock import patch

# 引入项目核心依赖
from main import app
from api.routes.group import router
from core.exceptions import setup_exception_handlers
from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user
from db.repositories.group_repo import QUERY_GET_MEMBER_ROLE

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
# 如果 main.py 中已经通过 include_router 挂载过，其实这里再挂载可能会报警告。
# 但为了能单独跑测试，我们显式挂载，保持和 test_friend.py 类似。
app.include_router(router, prefix="/api/group")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
async def test_group_journey_and_edge_cases():
    """
    全量群组管理功能的 E2E 测试。
    不使用任何 Mock，完全基于真实的测试数据库和数据流转！
    """
    # ==========================================
    # 0. 准备测试数据：在数据库中创建 4 个真实用户
    # ==========================================
    user_owner_id = None
    user_admin_id = None
    user_member_id = None
    user_stranger_id = None
    async for proxy_conn in get_db_conn():
        conn = cast(asyncpg.Connection, proxy_conn)
        hashed_pw = get_password_hash("password123")
        # 确保系统账号 -2 存在
        await conn.execute("""
            INSERT INTO user_account (user_id, username, password, email)
            VALUES (-2, '群聊助手', 'system_fake_password', 'group_assistant@ur-im.com')
            ON CONFLICT (user_id) DO NOTHING
        """)
        # 假设每次测试前 conftest.py 都会清理数据库，邮箱不会冲突
        user_owner_id = await db_create_user(
            conn, "group_owner", hashed_pw, "g_owner@test.com"
        )
        user_admin_id = await db_create_user(
            conn, "group_admin", hashed_pw, "g_admin@test.com"
        )
        user_member_id = await db_create_user(
            conn, "group_member", hashed_pw, "g_member@test.com"
        )
        user_stranger_id = await db_create_user(
            conn, "group_stranger", hashed_pw, "g_stranger@test.com"
        )
        user_outsider_id = await db_create_user(
            conn, "group_outsider", hashed_pw, "g_outsider@test.com"
        )
        # 👇 新增：为每个用户创建与群聊助手(-2)的私聊会话
        for uid in [
                user_owner_id,
                user_admin_id,
                user_member_id,
                user_stranger_id,
                user_outsider_id]:
            conv_id = await conn.fetchval("""
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                  AND cm1.member_user_id = $1
                  AND cm2.member_user_id = -2
            """, uid)
            if conv_id is None:
                conv_id = await conn.fetchval(
                    "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id"
                )
                await conn.execute(
                    "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, -2)",
                    conv_id, uid
                )
        break  # 取一次连接执行完毕即可
    # 为用户生成真实的 JWT Token，完美通过路由的鉴权依赖
    token_owner = create_access_token(data={"sub": str(user_owner_id)})
    token_admin = create_access_token(data={"sub": str(user_admin_id)})
    token_member = create_access_token(data={"sub": str(user_member_id)})
    token_stranger = create_access_token(data={"sub": str(user_stranger_id)})
    headers_owner = get_auth_headers(token_owner)
    headers_admin = get_auth_headers(token_admin)
    headers_member = get_auth_headers(token_member)
    headers_stranger = get_auth_headers(token_stranger)
    # 记录在流程中创建的 conversation_id 和 invite_id
    conversation_id = None
    apply_id = None
    # 开始端到端 HTTP 测试
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        # ---------------------------------------------------------
        # 1. 创建群聊 (POST /api/group/create)
        # ---------------------------------------------------------
        # Owner 创建群聊，带上 admin 和 member，排除了 stranger
        res = await client.post(
            "/api/group/create",
            json={"user_ids": [user_admin_id, user_member_id],
                  "name": "Test Avengers"},
            headers=headers_owner,
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["name"] == "Test Avengers"
        assert "conversation_id" in data
        conversation_id = data["conversation_id"]

        # ========== 新增：验证创建群聊后的系统通知 ==========
        # 1. 验证群聊内收到系统消息：“xxx 创建了群聊”
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            owner_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_owner_id)
            group_creation_msg = await conn.fetchrow(
                """
                SELECT m.msg_body->>'content' as message_content,
                       m.msg_body->'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                      AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                """,
                conversation_id
            )
            assert group_creation_msg is not None, "群聊中没有收到群创建系统通知"
            msg_text = group_creation_msg["message_content"]
            assert owner_name in msg_text, f"通知中未包含创建者 {owner_name}"
            assert "创建了群聊" in msg_text

            extra_raw = group_creation_msg["extra_data"]
            if extra_raw:
                # 将字符串解析为 Python 字典（兼容可能已经被 asyncpg 解析的情况）
                extra = json.loads(extra_raw) if isinstance(
                    extra_raw, str) else extra_raw
                assert extra.get("action") == "group_created"
                assert extra.get("creator_id") == user_owner_id
            break

        # 2. 验证被邀请成员（admin 和 member）都收到私聊系统通知
        invited_users = [user_admin_id, user_member_id]
        for invited_id in invited_users:
            async for proxy_conn in get_db_conn():
                conn = cast(asyncpg.Connection, proxy_conn)
                # 查找该成员与系统助手 -2 的私聊会话
                system_conv = await conn.fetchval(
                    """
                    SELECT c.conversation_id
                    FROM conversation c
                    JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                    JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                    WHERE c.type = 'private'
                      AND cm1.member_user_id = $1
                      AND cm2.member_user_id = -2
                    """,
                    invited_id
                )
                assert system_conv is not None, f"用户 {invited_id} 没有与系统助手的私聊会话"

                invite_msg = await conn.fetchrow(
                    """
                    SELECT m.msg_body->>'content' as message_content,
                           m.msg_body->'extra' as extra_data
                    FROM message m
                    WHERE m.msg_id = (
                        SELECT cm.msg_id
                        FROM conversation_message cm
                        WHERE cm.conversation_id = $1
                          AND cm.sender_id = -2
                        ORDER BY cm.msg_id DESC
                        LIMIT 1
                    )
                    AND m.msg_body->>'type' = 'notify'
                    """,
                    system_conv
                )
                assert invite_msg is not None, f"用户 {invited_id} 未收到被加入群聊的系统通知"
                msg_text = invite_msg["message_content"]
                assert "被邀请加入群聊" in msg_text or "将你加入了群聊" in msg_text
                extra_raw = invite_msg["extra_data"]
                if extra_raw:
                    extra = json.loads(extra_raw) if isinstance(
                        extra_raw, str) else extra_raw
                    assert extra.get("action") == "added_to_group"
                    assert extra.get("conversation_id") == conversation_id
                    assert extra.get("creator_id") == user_owner_id
                break
        # ========== 新增验证结束 ==========

        # 测试 Validation 异常 (422)：没传必填参数 user_ids
        res_invalid = await client.post(
            "/api/group/create",
            json={"name": "Bad Group"},
            headers=headers_owner,
        )
        assert res_invalid.status_code == 422
        # ---------------------------------------------------------
        # 2. 获取群聊信息 (POST /api/group/info)
        # ---------------------------------------------------------
        # 群主查看自己创建的群
        res = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res.status_code == 200
        info_data = res.json()["data"]
        assert info_data["conversation_name"] == "Test Avengers"
        # owner 自己加上邀请的 2 人
        assert info_data["member_count"] == 3
        assert info_data["my_role"] == "owner"
        assert len(info_data["top_members"]) == 3
        # 测试权限异常 (403): Stranger 不能查看不属于自己的群聊信息
        res_forbidden = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_stranger,
        )
        assert res_forbidden.status_code == 403
        # ---------------------------------------------------------
        # 3. 成员分页与查询 (POST /api/group/members)
        # ---------------------------------------------------------
        res = await client.post(
            "/api/group/members",
            json={"conversation_id": conversation_id,
                  "page": 1, "page_size": 2},
            headers=headers_owner,
        )
        assert res.status_code == 200
        members_data = res.json()["data"]
        assert members_data["total"] == 3
        # 分页验证：限制大小为 2，那么这页只返回 2 个
        assert len(members_data["list"]) == 2
        # ---------------------------------------------------------
        # 4. 发布群公告 (POST /api/group/announcement)
        # ---------------------------------------------------------
        # Member 尝试发公告 -> 报错 403 PermissionDenied
        res = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id, "msg": "I am king"},
            headers=headers_member,
        )
        assert res.status_code == 403
        # Owner 发布合法公告
        res = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id, "msg": "Assemble!"},
            headers=headers_owner,
        )
        assert res.status_code == 200
        assert "announcement_id" in res.json()["data"]

        # ========== 新增：获取群公告列表测试 ==========
        # 4.1 再发布几条公告，用于测试分页
        res_anno2 = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id, "msg": "Second Announcement"},
            headers=headers_owner,
        )
        assert res_anno2.status_code == 200
        
        res_anno3 = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id, "msg": "Third Announcement"},
            headers=headers_owner,
        )
        assert res_anno3.status_code == 200  # admin 有权限发公告
        
        # 4.2 测试获取公告列表 - 默认分页
        res_list = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_list.status_code == 200
        list_data = res_list.json()["data"]
        assert list_data["total"] == 3  # 共3条公告
        assert list_data["page"] == 1
        assert list_data["page_size"] == 20  # 默认值
        assert len(list_data["items"]) == 3
        # 验证返回的公告内容（按时间倒序，最新的在前面）
        assert list_data["items"][0]["content"] == "Third Announcement"
        assert list_data["items"][1]["content"] == "Second Announcement"
        assert list_data["items"][2]["content"] == "Assemble!"
        # 验证字段完整性
        for item in list_data["items"]:
            assert "announcement_id" in item
            assert "content" in item
            assert "create_time" in item
            assert "sender_name" in item
            assert isinstance(item["create_time"], int)
        
        # 4.3 测试分页功能 - page=1, page_size=2
        res_page1 = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id, "page": 1, "page_size": 2},
            headers=headers_owner,
        )
        assert res_page1.status_code == 200
        page1_data = res_page1.json()["data"]
        assert page1_data["total"] == 3
        assert page1_data["page"] == 1
        assert page1_data["page_size"] == 2
        assert len(page1_data["items"]) == 2
        assert page1_data["items"][0]["content"] == "Third Announcement"
        assert page1_data["items"][1]["content"] == "Second Announcement"
        
        # 4.4 测试分页功能 - page=2, page_size=2（应该只有1条）
        res_page2 = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id, "page": 2, "page_size": 2},
            headers=headers_owner,
        )
        assert res_page2.status_code == 200
        page2_data = res_page2.json()["data"]
        assert page2_data["total"] == 3
        assert page2_data["page"] == 2
        assert page2_data["page_size"] == 2
        assert len(page2_data["items"]) == 1
        assert page2_data["items"][0]["content"] == "Assemble!"
        
        # 4.5 测试权限：非群成员无法获取公告列表
        res_forbidden_list = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id},
            headers=headers_stranger,  # stranger 不在群里
        )
        assert res_forbidden_list.status_code == 403
        
        # 4.6 测试参数校验 - conversation_id 必须大于0
        res_invalid_conv = await client.post(
            "/api/group/announcements",
            json={"conversation_id": 0},
            headers=headers_owner,
        )
        assert res_invalid_conv.status_code == 422
        
        # 4.7 测试参数校验 - page_size 不能超过100
        res_invalid_size = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id, "page_size": 101},
            headers=headers_owner,
        )
        assert res_invalid_size.status_code == 422
        
        # 4.8 普通成员也能查看公告列表（只读权限）
        res_member_list = await client.post(
            "/api/group/announcements",
            json={"conversation_id": conversation_id, "page_size": 10},
            headers=headers_member,
        )
        assert res_member_list.status_code == 200
        member_list_data = res_member_list.json()["data"]
        assert member_list_data["total"] == 3
        assert len(member_list_data["items"]) == 3
        # ========== 新增测试结束 ==========

        
        # 获取群信息，验证公告存在
        res_info = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_info.status_code == 200
        assert res_info.json()[
            "data"]["latest_announcement"]["content"] == "Third Announcement"
        # ---------------------------------------------------------
        # 5. 管理员设置 (PUT /api/group/admin)
        # ---------------------------------------------------------
        # 给 user_admin 设置 admin 角色
        res = await client.put(
            "/api/group/admin",
            json={
                "conversation_id": conversation_id,
                "user_id": user_admin_id,
                "role": "admin",
            },
            headers=headers_owner,
        )
        assert res.status_code == 200
        # Owner 尝试把陌生人设置 admin (403 对方不在群里 / NotInGroup)
        res_admin_fail = await client.put(
            "/api/group/admin",
            json={
                "conversation_id": conversation_id,
                "user_id": user_stranger_id,
                "role": "admin",
            },
            headers=headers_owner,
        )
        assert res_admin_fail.status_code == 403
        # Admin 发公告 (验证他已经被正确提升了权限)
        res_admin_anno = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id, "msg": "I am admin"},
            headers=headers_admin,
        )
        assert res_admin_anno.status_code == 200

        # ========== 新增：验证设置管理员后的系统通知 ==========
        # 验证群内收到角色变更通知（admin 被设为管理员）
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            owner_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_owner_id)
            admin_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_admin_id)

            role_change_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                JOIN conversation_message cm ON m.msg_id = cm.msg_id
                WHERE cm.conversation_id = $1
                AND cm.sender_id = -2
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%group_admin_set%'
                ORDER BY cm.msg_id DESC
                LIMIT 1
                """,
                conversation_id
            )
            assert role_change_msg is not None, "群聊内未收到设置管理员的通知"
            msg_text = role_change_msg["message_content"]
            assert owner_name in msg_text and admin_name in msg_text
            assert "设置" in msg_text and "管理员" in msg_text
            extra_str = role_change_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            if extra:
                assert extra.get("action") == "group_admin_set"
                assert extra.get("operator_id") == user_owner_id
                assert extra.get("target_user_id") == user_admin_id
            break

        # 2. 验证被操作者 (admin) 收到私聊通知
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            system_conv = await conn.fetchval(
                """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                AND cm1.member_user_id = $1
                AND cm2.member_user_id = -2
                """,
                user_admin_id
            )
            assert system_conv is not None, "被设置管理员没有与系统助手的私聊会话"
            private_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                    AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%group_admin_set_to_you%'
                """,
                system_conv
            )
            assert private_msg is not None, "被设置管理员未收到私聊通知"
            extra_str = private_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            if extra:
                assert extra.get("action") == "group_admin_set_to_you"
                assert extra.get("conversation_id") == conversation_id
                assert extra.get("operator_id") == user_owner_id
            break
        # ========== 设置管理员通知验证结束 ==========

        # ========== 新增：撤销管理员并验证通知及待处理列表清理 ==========
        # 先让 member 再创建一个新的入群申请，确保待处理列表中有数据可供后续验证清理
        res_invite2 = await client.post(
            "/api/group/invite",
            json={"conversation_id": conversation_id,
                  "user_id": user_outsider_id},
            headers=headers_member,
        )
        assert res_invite2.status_code == 200
        apply_id2 = res_invite2.json()["data"]["apply_id"]

        # 确认 admin 的待处理列表包含该申请
        res_pending_admin_before = await client.get(
            "/api/group/invites/pending",
            headers=headers_admin,
        )
        assert res_pending_admin_before.status_code == 200
        cards_before = res_pending_admin_before.json()["data"]
        assert any(card["apply_id"] ==
                   apply_id2 for card in cards_before), "撤销前 admin 应能看到新申请"

        # Owner 撤销 admin 的管理员角色
        res_revoke = await client.put(
            "/api/group/admin",
            json={
                "conversation_id": conversation_id,
                "user_id": user_admin_id,
                "role": "member",
            },
            headers=headers_owner,
        )
        assert res_revoke.status_code == 200

        # 验证群内收到撤销管理员的通知
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            owner_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_owner_id)
            admin_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_admin_id)

            revoke_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                    AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%group_admin_unset%'
                """,
                conversation_id
            )
            assert revoke_msg is not None, "群聊内未收到撤销管理员的通知"
            msg_text = revoke_msg["message_content"]
            assert owner_name in msg_text and admin_name in msg_text
            assert "撤销" in msg_text and "管理员" in msg_text
            extra_str = revoke_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            if extra:
                assert extra.get("action") == "group_admin_unset"
                assert extra.get("operator_id") == user_owner_id
                assert extra.get("target_user_id") == user_admin_id
            break

        # 验证被撤销者收到私聊通知
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            system_conv = await conn.fetchval(
                """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                AND cm1.member_user_id = $1
                AND cm2.member_user_id = -2
                """,
                user_admin_id
            )
            assert system_conv is not None
            private_revoke_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                    AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%group_admin_unset_from_you%'
                """,
                system_conv
            )
            assert private_revoke_msg is not None, "被撤销管理员未收到私聊通知"
            extra_str = private_revoke_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            if extra:
                assert extra.get("action") == "group_admin_unset_from_you"
                assert extra.get("conversation_id") == conversation_id
                assert extra.get("operator_id") == user_owner_id
            break

        # 验证被撤销者的待处理入群申请已被清理（数据库中无 pending 记录）
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            count = await conn.fetchval(
                """
                SELECT COUNT(1)
                FROM group_invite_admin_state
                WHERE admin_id = $1
                AND state = 'pending'
                AND invite_id IN (
                    SELECT invite_id FROM group_invite
                    WHERE conversation_id = $2 AND status = 'pending'
                )
                """,
                user_admin_id, conversation_id
            )
            assert count == 0, "被撤销管理员后，其待处理入群申请状态记录未被清理"
            break

        # 撤销后 admin 调用待处理列表应返回空（或不再包含本群申请）
        res_pending_admin_after = await client.get(
            "/api/group/invites/pending",
            headers=headers_admin,
        )
        assert res_pending_admin_after.status_code == 200
        cards_after = res_pending_admin_after.json()["data"]
        assert not any(card["conversation_id"] ==
                       conversation_id for card in cards_after), "撤销管理员后，其待处理列表中不应再看到该群的任何申请"
        # ========== 撤销管理员测试结束 ==========

        res = await client.put(
            "/api/group/admin",
            json={
                "conversation_id": conversation_id,
                "user_id": user_admin_id,
                "role": "admin",
            },
            headers=headers_owner,
        )
        assert res.status_code == 200

        # ---------------------------------------------------------
        # 6. 群邀请与审核 (POST /api/group/invite & POST /api/group/invite/review)
        # ---------------------------------------------------------
        # Member 邀请 Stranger (合法的 member 邀请流程)
        res_invite = await client.post(
            "/api/group/invite",
            json={"conversation_id": conversation_id,
                  "user_id": user_stranger_id},
            headers=headers_member,
        )
        assert res_invite.status_code == 200
        apply_id = res_invite.json()["data"]["apply_id"]

        # 验证数据库：group_invite_admin_state 中已有两个管理员（owner + admin）的 pending 记录
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            admin_states = await conn.fetch(
                "SELECT admin_id, state FROM group_invite_admin_state WHERE invite_id = $1",
                apply_id
            )
            assert len(admin_states) == 2
            for record in admin_states:
                assert record["state"] == "pending"
            break

        # ========== 新增 API 测试：GET /api/group/invites/pending ==========
        # 1. Admin 获取待处理入群申请列表（应包含刚刚创建的申请）
        res_pending_admin = await client.get(
            "/api/group/invites/pending",
            headers=headers_admin,
        )
        assert res_pending_admin.status_code == 200
        pending_cards_admin = res_pending_admin.json()["data"]
        assert len(pending_cards_admin) >= 1
        found_admin = False
        for card in pending_cards_admin:
            if card["apply_id"] == apply_id:
                found_admin = True
                assert card["card_type"] == "group_apply"
                assert card["conversation_id"] == conversation_id
                assert card["applicant_id"] == user_stranger_id
                assert card["status"] == "pending"
                assert "create_time" in card
                break
        assert found_admin, "Admin 的待处理列表中未找到新创建的入群申请"

        # 2. Owner 获取待处理列表（也应包含该申请）
        res_pending_owner = await client.get(
            "/api/group/invites/pending",
            headers=headers_owner,
        )
        assert res_pending_owner.status_code == 200
        pending_cards_owner = res_pending_owner.json()["data"]
        found_owner = any(card["apply_id"] ==
                          apply_id for card in pending_cards_owner)
        assert found_owner, "Owner 的待处理列表中未找到新创建的入群申请"

        # 3. 普通成员获取待处理列表（应返回空列表，因为成员无审批权限）
        res_pending_member = await client.get(
            "/api/group/invites/pending",
            headers=headers_member,
        )
        assert res_pending_member.status_code == 200
        pending_cards_member = res_pending_member.json()["data"]
        assert len(pending_cards_member) == 0, "普通成员不应看到任何待处理入群申请"
        # ========== 新增测试结束 ==========

        # 场景1：Admin 先执行忽略（仅修改自己的状态，全局不变）
        res_ignore = await client.post(
            "/api/group/invite/review",
            # 假设 IGNORED 对应 ignored
            json={"apply_id": apply_id, "status": "IGNORED"},
            headers=headers_admin,
        )
        assert res_ignore.status_code == 200

        # 验证：admin 的个人状态变为 ignored，owner 仍为 pending，全局 status 仍为 pending
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            rows = await conn.fetch(
                "SELECT admin_id, state FROM group_invite_admin_state WHERE invite_id = $1 ORDER BY admin_id",
                apply_id
            )
            states = {row["admin_id"]: row["state"] for row in rows}
            assert states[user_admin_id] == "ignored"
            assert states[user_owner_id] == "pending"
            global_status = await conn.fetchval("SELECT status FROM group_invite WHERE invite_id = $1", apply_id)
            assert global_status == "pending"
            # 被邀请人尚未入群
            is_member = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2)",
                conversation_id, user_stranger_id
            )
            assert is_member is False
            break

        # ========== 验证忽略后 Admin 的待处理列表不再包含该申请 ==========
        res_pending_admin_after_ignore = await client.get(
            "/api/group/invites/pending",
            headers=headers_admin,
        )
        assert res_pending_admin_after_ignore.status_code == 200
        admin_cards_after_ignore = res_pending_admin_after_ignore.json()[
            "data"]
        assert not any(card["apply_id"] == apply_id for card in admin_cards_after_ignore), \
            "Admin 执行忽略后，其待处理列表中不应再出现该入群申请"

        # Owner 依然能看到（因为 Owner 的状态还是 pending）
        res_pending_owner_after_ignore = await client.get(
            "/api/group/invites/pending",
            headers=headers_owner,
        )
        assert res_pending_owner_after_ignore.status_code == 200
        owner_cards_after_ignore = res_pending_owner_after_ignore.json()[
            "data"]
        assert any(card["apply_id"] == apply_id for card in owner_cards_after_ignore), \
            "Owner 未执行操作，待处理列表中应仍包含该入群申请"
        # ========== 结束 ==========

        # 场景2：Owner 批准（一票通过，覆盖所有管理员状态，拉人入群，发送通知）
        res_approve = await client.post(
            "/api/group/invite/review",
            json={"apply_id": apply_id, "status": "APPROVED"},
            headers=headers_owner,
        )
        assert res_approve.status_code == 200

        # 验证：全局状态变为 approved，所有管理员个人状态变为 approved
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            rows = await conn.fetch(
                "SELECT admin_id, state FROM group_invite_admin_state WHERE invite_id = $1",
                apply_id
            )
            for row in rows:
                assert row["state"] == "approved"
            global_status = await conn.fetchval("SELECT status FROM group_invite WHERE invite_id = $1", apply_id)
            assert global_status == "approved"
            # 被邀请人已入群
            is_member = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2)",
                conversation_id, user_stranger_id
            )
            assert is_member is True
            break

        # ========== 验证批准后待处理列表不再包含该申请 ==========
        res_pending_after_approve = await client.get(
            "/api/group/invites/pending",
            headers=headers_owner,
        )
        assert res_pending_after_approve.status_code == 200
        after_cards = res_pending_after_approve.json()["data"]
        assert not any(card["apply_id"] == apply_id for card in after_cards), \
            "批准后待处理列表中不应再出现该入群申请"
        # ========== 结束 ==========

        # ========== 新增验证：系统消息 ==========
        # 1. 验证群聊内收到系统消息（-2 发送的 NOTIFY，内容包含邀请人和被邀请人）
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            inviter_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_member_id)
            invitee_name = await conn.fetchval("SELECT username FROM user_account WHERE user_id = $1", user_stranger_id)

            group_msg = await conn.fetchrow(
                """
                SELECT m.msg_body->>'content' as message_content,
                    m.msg_body->'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                    AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                """,
                conversation_id
            )
            assert group_msg is not None, "群聊中没有收到系统通知"
            msg_text = group_msg["message_content"]
            assert inviter_name in msg_text, f"通知中未包含邀请人 {inviter_name}"
            assert invitee_name in msg_text, f"通知中未包含被邀请人 {invitee_name}"
            assert "拉入了群聊" in msg_text
            extra_raw = group_msg["extra_data"]
            if extra_raw:
                extra = json.loads(extra_raw) if isinstance(
                    extra_raw, str) else extra_raw
                assert extra.get("action") == "group_member_invited"
            break

        # 2. 验证被邀请人收到系统私聊通知
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            system_conv = await conn.fetchval(
                """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                AND cm1.member_user_id = $1
                AND cm2.member_user_id = -2
                """,
                user_stranger_id
            )
            assert system_conv is not None, "被邀请人没有与系统助手的私聊会话"
            invitee_msg = await conn.fetchrow(
                """
                SELECT m.msg_body->>'content' as message_content,
                    m.msg_body->'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                    AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                """,
                system_conv
            )
            assert invitee_msg is not None, "被邀请人未收到系统私聊通知"
            msg_text = invitee_msg["message_content"]
            assert "入群申请已通过" in msg_text or "批准" in msg_text
            extra_raw = invitee_msg["extra_data"]
            if extra_raw:
                extra = json.loads(extra_raw) if isinstance(
                    extra_raw, str) else extra_raw
                assert extra.get(
                    "action") == "group_invite_approved_for_invitee"
                assert extra.get("conversation_id") == conversation_id
            break

        # Stranger 现在已经是群员了，尝试看群信息
        res_stranger_info = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_stranger,
        )
        assert res_stranger_info.status_code == 200
        # ---------------------------------------------------------
        # 7. 踢人 (DELETE /api/group/member)
        # ---------------------------------------------------------
        # Admin 尝试踢 Owner -> 报错 (等级压制) CannotKickHigherRole
        res_kick_fail = await client.request(
            "DELETE",
            "/api/group/member",
            json={"conversation_id": conversation_id, "user_id": user_owner_id},
            headers=headers_admin,
        )
        assert res_kick_fail.status_code == 403
        # Admin 尝试踢 Member -> 成功 (Admin > Member)
        res_kick = await client.request(
            "DELETE",
            "/api/group/member",
            json={"conversation_id": conversation_id,
                  "user_id": user_member_id},
            headers=headers_admin,
        )
        assert res_kick.status_code == 200

        # ========== 验证被踢成员收到私聊通知 ==========
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            # 查找 member 与系统助手 -2 的私聊会话
            system_conv = await conn.fetchval(
                """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                  AND cm1.member_user_id = $1
                  AND cm2.member_user_id = -2
                """,
                user_member_id
            )
            assert system_conv is not None, "被踢成员没有与系统助手的私聊会话"

            kick_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                      AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%kicked_from_group%'
                """,
                system_conv
            )
            assert kick_msg is not None, "被踢成员未收到私聊通知"
            msg_text = kick_msg["message_content"]
            assert "你已被" in msg_text or "移出群聊" in msg_text
            extra_str = kick_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            assert extra.get("action") == "kicked_from_group"
            assert extra.get("conversation_id") == conversation_id
            assert extra.get("operator_id") == user_admin_id  # 踢人者是 admin
            break
        # ========== 踢人通知验证结束 ==========

        # ---------------------------------------------------------
        # 8. 退出群聊 (POST /api/group/quit)
        # ---------------------------------------------------------
        # Owner 尝试退出 -> 报错 OwnerCannotQuit (400)
        res_owner_quit = await client.post(
            "/api/group/quit",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_owner_quit.status_code == 400
        # Stranger (现在已经是普通会员) 退出 -> 成功
        res_stranger_quit = await client.post(
            "/api/group/quit",
            json={"conversation_id": conversation_id},
            headers=headers_stranger,
        )
        assert res_stranger_quit.status_code == 200
        # ========== 验证退群成员收到私聊通知 ==========
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            system_conv = await conn.fetchval(
                """
                SELECT c.conversation_id
                FROM conversation c
                JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                WHERE c.type = 'private'
                  AND cm1.member_user_id = $1
                  AND cm2.member_user_id = -2
                """,
                user_stranger_id
            )
            assert system_conv is not None, "退群成员没有与系统助手的私聊会话"

            quit_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                WHERE m.msg_id = (
                    SELECT cm.msg_id
                    FROM conversation_message cm
                    WHERE cm.conversation_id = $1
                      AND cm.sender_id = -2
                    ORDER BY cm.msg_id DESC
                    LIMIT 1
                )
                AND m.msg_body->>'type' = 'notify'
                AND m.msg_body->>'extra' LIKE '%left_group%'
                """,
                system_conv
            )
            assert quit_msg is not None, "退群成员未收到私聊通知"
            msg_text = quit_msg["message_content"]
            assert "你已退出" in msg_text or "退出群聊" in msg_text
            extra_str = quit_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            assert extra.get("action") == "left_group"
            assert extra.get("conversation_id") == conversation_id
            break
        # ========== 退群通知验证结束 ==========
        # ---------------------------------------------------------
        # 9. 解散群聊 (POST /api/group/bomb)
        # ---------------------------------------------------------
        # Admin 尝试解散 -> 报错 (只有 Owner 能解散)
        res_admin_bomb = await client.post(
            "/api/group/bomb",
            json={"conversation_id": conversation_id},
            headers=headers_admin,
        )
        assert res_admin_bomb.status_code == 403
        # Owner 解散群聊
        res_bomb = await client.post(
            "/api/group/bomb",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_bomb.status_code == 200
        # 验证解散成功，群已经 404 (或者报不在群里的 403)
        res_after_bomb = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        # 根据 group_repo，如果全被删除了会找不到，也就是 role 获取失败返回 NotInGroup (403)
        assert res_after_bomb.status_code == 403

        # ========== 验证群内收到解散通知 ==========
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            disband_msg = await conn.fetchrow(
                """
                SELECT
                    m.msg_body->>'content' as message_content,
                    m.msg_body->>'extra' as extra_data
                FROM message m
                JOIN conversation_message cm ON m.msg_id = cm.msg_id
                WHERE cm.conversation_id = $1
                  AND cm.sender_id = -2
                  AND m.msg_body->>'type' = 'notify'
                  AND m.msg_body->>'extra' LIKE '%group_disbanded%'
                ORDER BY cm.msg_id DESC
                LIMIT 1
                """,
                conversation_id
            )
            assert disband_msg is not None, "群聊内未收到解散通知"
            msg_text = disband_msg["message_content"]
            assert "已解散" in msg_text or "解散" in msg_text
            extra_str = disband_msg["extra_data"]
            extra = json.loads(extra_str) if extra_str else {}
            assert extra.get("action") == "group_disbanded"
            assert extra.get("operator_id") == user_owner_id
            break
        # ========== 解散通知验证结束 ==========

# ==========================================
# 测试用例：邀请入群触发群聊助手(-2)私聊卡片通知
# ==========================================


@pytest.mark.asyncio(loop_scope="session")
@patch("core.ws_manager.manager.send_personal_message")
async def test_group_invite_triggers_assistant_card(mock_ws_send):
    """
    测试邀请入群时，群聊助手(-2)会向所有管理员（含群主）的私聊发送卡片消息。
    验证卡片消息内容、格式以及接收人。
    """
    user_owner_id = None
    user_admin_id = None
    user_member_id = None
    user_invitee_id = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # 1. 准备测试数据：创建群主、管理员、普通成员、被邀请人，并建立私聊会话
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)

            # 确保系统账号 -2 存在
            await conn.execute("""
                INSERT INTO user_account (user_id, username, password, email)
                VALUES (-2, '群聊助手', 'system_fake_password', 'group_assistant@ur-im.com')
                ON CONFLICT (user_id) DO NOTHING
            """)

            hashed_pw = get_password_hash("test123")

            user_owner_id = await conn.fetchval(
                "INSERT INTO user_account (username, password, email) VALUES ($1, $2, $3) RETURNING user_id",
                "GroupOwner", hashed_pw, "owner_invite@test.com"
            )
            user_admin_id = await conn.fetchval(
                "INSERT INTO user_account (username, password, email) VALUES ($1, $2, $3) RETURNING user_id",
                "GroupAdmin", hashed_pw, "admin_invite@test.com"
            )
            user_member_id = await conn.fetchval(
                "INSERT INTO user_account (username, password, email) VALUES ($1, $2, $3) RETURNING user_id",
                "GroupMember", hashed_pw, "member_invite@test.com"
            )
            user_invitee_id = await conn.fetchval(
                "INSERT INTO user_account (username, password, email) VALUES ($1, $2, $3) RETURNING user_id",
                "Invitee", hashed_pw, "invitee@test.com"
            )

            # 为群主和管理员创建与 -2 助手的私聊会话（模拟注册时的行为）
            for uid in [user_owner_id, user_admin_id]:
                conv_id = await conn.fetchval("""
                    SELECT c.conversation_id
                    FROM conversation c
                    JOIN conversation_member cm1 ON c.conversation_id = cm1.conversation_id
                    JOIN conversation_member cm2 ON c.conversation_id = cm2.conversation_id
                    WHERE c.type = 'private'
                      AND cm1.member_user_id = $1
                      AND cm2.member_user_id = -2
                """, uid)
                if conv_id is None:
                    conv_id = await conn.fetchval(
                        "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id"
                    )
                    await conn.execute(
                        "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, -2)",
                        conv_id, uid
                    )

            # 创建群聊：群主 + 管理员 + 成员
            group_conv_id = await conn.fetchval(
                "INSERT INTO conversation (type) VALUES ('group') RETURNING conversation_id"
            )
            await conn.execute("""
                INSERT INTO conversation_member (conversation_id, member_user_id, role)
                VALUES ($1, $2, 'owner'),
                       ($1, $3, 'admin'),
                       ($1, $4, 'member')
            """, group_conv_id, user_owner_id, user_admin_id, user_member_id)

            break

        # 2. 普通成员登录
        token_member = create_access_token(data={"sub": str(user_member_id)})
        headers_member = get_auth_headers(token_member)

        # 3. 成员邀请新人入群
        res = await client.post(
            "/api/group/invite",
            json={
                "conversation_id": group_conv_id,
                "user_id": user_invitee_id
            },
            headers=headers_member,
        )
        assert res.status_code == 200
        apply_id = res.json()["data"]["apply_id"]
        assert apply_id is not None

        # 4. 验证 WebSocket 推送（Mock）
        assert mock_ws_send.called, "系统卡片未能发出！"

        # 收集推送给群主和管理员的消息
        received_payloads = []
        for call in mock_ws_send.call_args_list:
            payload, target_user_id = call[0][0], call[0][1]
            if target_user_id in (user_owner_id, user_admin_id):
                received_payloads.append((target_user_id, payload))

        # 应该有两个管理员收到消息
        assert len(received_payloads) == 2, f"预期推送给2个管理员，实际收到 {
            len(received_payloads)} 个"

        # 验证每条消息的内容
        for target_user_id, payload in received_payloads:
            assert payload["type"] == "NEW_CHAT_MESSAGE"
            message_data = payload["data"]
            assert message_data["sender_id"] == -2
            assert message_data["msg_type"] == "card"
            assert message_data["content"] == "[收到一条入群申请]"

            extra = message_data.get("extra", {})
            assert extra.get("card_type") == "group_apply"
            assert extra.get("apply_id") == apply_id
            assert extra.get("applicant_id") == user_invitee_id
            assert extra.get("inviter_id") == user_member_id
            assert extra.get("conversation_id") == group_conv_id
            assert extra.get("status") == "pending"
            assert extra.get("inviter_name") == "GroupMember"
