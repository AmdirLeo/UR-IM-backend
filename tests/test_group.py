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
import os
from unittest.mock import patch, AsyncMock

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
        # 👇 新增：创建一个完全没有好友关系的纯路人
        user_non_friend_id = await db_create_user(
            conn, "pure_stranger", hashed_pw, "pure_stranger@test.com"
        )
        # 👇 新增：为每个用户创建与群聊助手(-2)的私聊会话
        for uid in [
                user_owner_id,
                user_admin_id,
                user_member_id,
                user_stranger_id,
                user_outsider_id,
                user_non_friend_id]:
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
            data={
                "user_ids": [user_admin_id, user_member_id],
                "name": "Test Avengers"
            },
            headers=headers_owner,
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["name"] == "Test Avengers"
        assert "conversation_id" in data
        assert data.get("avatar") is None
        conversation_id = data["conversation_id"]

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

        # 新增测试 B：测试创建群聊的同时上传群头像
        with patch("services.group_service.db_update_group_profile", new_callable=AsyncMock) as mock_db:
            mock_db.return_value = True  # 模拟底层更新头像数据库成功

            files_create = {
                "file": (
                    "new_group_avatar.png",
                    b"fake_image_data",
                    "image/png")}
            res_with_avatar = await client.post(
                "/api/group/create",
                data={
                    "user_ids": [user_admin_id],
                    "name": "Avengers With Avatar"
                },
                files=files_create,
                headers=headers_owner,
            )
            assert res_with_avatar.status_code == 200
            data_with_avatar = res_with_avatar.json()["data"]
            assert data_with_avatar["name"] == "Avengers With Avatar"
            assert data_with_avatar.get("avatar") is not None
            assert "filekey" in data_with_avatar["avatar"] or str(
                data_with_avatar["avatar"]).startswith("http")

            # 清理刚刚测试产生的脏图片
            avatar_url = data_with_avatar.get("avatar", "")
            if isinstance(avatar_url, str):
                saved_path = avatar_url.lstrip("/")
                if os.path.exists(saved_path):
                    os.remove(saved_path)

        # 测试 Validation 异常 (422)：没传必填参数 user_ids
        res_invalid = await client.post(
            "/api/group/create",
            data={"name": "Bad Group"},
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
        # 2.5 获取群聊列表 (GET /api/group)
        # ---------------------------------------------------------
        # Owner 获取自己的群聊列表
        res_list_owner = await client.get(
            "/api/group",
            headers=headers_owner,
        )
        assert res_list_owner.status_code == 200
        owner_groups = res_list_owner.json()["data"]
        assert len(owner_groups) >= 1

        # 验证返回的群信息与 Owner 角色
        target_group = next(
            (g for g in owner_groups if g["conversation_id"] == conversation_id), None)
        assert target_group is not None, "Owner 的群聊列表中未找到刚刚创建的群"
        assert target_group["conversation_name"] == "Test Avengers"
        assert target_group["role"] == "owner"
        assert "join_time" in target_group

        # Member 获取自己的群聊列表，验证身份降级显示正常
        res_list_member = await client.get(
            "/api/group",
            headers=headers_member,
        )
        assert res_list_member.status_code == 200
        member_groups = res_list_member.json()["data"]
        target_group_member = next(
            (g for g in member_groups if g["conversation_id"] == conversation_id), None)
        assert target_group_member is not None, "Member 的群聊列表中未找到该群"
        assert target_group_member["role"] == "member"

        # Stranger (尚未入群的局外人) 获取群聊列表，必须严格不包含该群
        res_list_stranger = await client.get(
            "/api/group",
            headers=headers_stranger,
        )
        assert res_list_stranger.status_code == 200
        stranger_groups = res_list_stranger.json()["data"]
        assert not any(g["conversation_id"] ==
                       conversation_id for g in stranger_groups), "Stranger 不应该在群聊列表中看到不属于自己的群"
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
            json={"conversation_id": conversation_id,
                  "msg": "Second Announcement"},
            headers=headers_owner,
        )
        assert res_anno2.status_code == 200

        res_anno3 = await client.post(
            "/api/group/announcement",
            json={"conversation_id": conversation_id,
                  "msg": "Third Announcement"},
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
            json={"conversation_id": conversation_id,
                  "page": 1, "page_size": 2},
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
            json={"conversation_id": conversation_id,
                  "page": 2, "page_size": 2},
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
        # 👇👇👇 新增：确立 member 和 outsider 的好友关系，通过鉴权 👇👇👇
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            await conn.execute(
                """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1) ON CONFLICT DO NOTHING;
                """,
                user_member_id, user_outsider_id
            )
            break
        # 👆👆👆 新增结束 👆👆👆
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
        # 5.5 修改群聊名称 (PUT /api/group/name)
        # ---------------------------------------------------------
        new_group_name = "Avengers: Infinity War"

        # 1. 普通 Member 尝试修改 -> 报错 403 PermissionDenied
        res_rename_fail = await client.put(
            "/api/group/name",
            json={"conversation_id": conversation_id, "new_name": "Member Hub"},
            headers=headers_member,
        )
        assert res_rename_fail.status_code == 403

        # 2. Admin 合法修改群名称
        res_rename = await client.put(
            "/api/group/name",
            json={"conversation_id": conversation_id,
                  "new_name": new_group_name},
            headers=headers_admin,
        )
        assert res_rename.status_code == 200
        assert res_rename.json()["data"]["new_name"] == new_group_name

        # 3. 重新获取群信息，确认名称已真正更新
        res_info_after_rename = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_info_after_rename.status_code == 200
        assert res_info_after_rename.json(
        )["data"]["conversation_name"] == new_group_name

        # ========== 新增：手动缔结好友关系（为了通过单人邀请的鉴权） ==========
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            await conn.execute(
                """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1) ON CONFLICT DO NOTHING;
                """,
                user_member_id, user_stranger_id
            )
            break
        # =================================================================

        # ---------------------------------------------------------
        # 5.6 修改群头像 (PUT /api/group/edit/portrait)
        # ---------------------------------------------------------
        # ⚠️ 注意：FastAPI 中同时上传文件和参数，需使用 multipart/form-data
        # 参数传递必须放在 data=... 中，且转为字符串，文件放在 files=... 中
        form_data = {"conversation_id": str(conversation_id)}

        # 1. 越权测试：普通 Member 尝试修改群头像 -> 报错 403 PermissionDenied
        files_normal = {"file": ("test_group.png", b"fake_data", "image/png")}
        res_avatar_fail = await client.put(
            "/api/group/edit/portrait",  # 请确保这与你 router 里定义的真实路径一致
            data=form_data,
            files=files_normal,
            headers=headers_member,
        )
        assert res_avatar_fail.status_code == 403

        # 2. 合法修改：Admin 成功修改群头像
        # 使用 AsyncMock 拦截数据库真实写入，保护测试数据库的数据一致性
        with patch("services.group_service.db_update_group_profile", new_callable=AsyncMock) as mock_db:
            mock_db.return_value = True  # 模拟数据库更新成功

            res_avatar_success = await client.put(
                "/api/group/edit/portrait",
                data=form_data,
                files=files_normal,
                headers=headers_admin,
            )
            assert res_avatar_success.status_code == 200

            # 验证返回结构
            avatar_data = res_avatar_success.json()
            assert "filekey" in avatar_data

            # 清理测试期间向 MinIO / 本地磁盘 写入的垃圾图片 (看齐 test_user)
            saved_path = avatar_data["filekey"].lstrip("/")
            if os.path.exists(saved_path):
                os.remove(saved_path)

        # 3. 边界测试：上传超过 2MB 的超大文件 -> 报错 400
        large_file_content = b"0" * (2 * 1024 * 1024 + 1024)  # 2MB + 1KB
        files_large = {
            "file": (
                "huge_group_avatar.png",
                large_file_content,
                "image/png")}

        res_avatar_large = await client.put(
            "/api/group/edit/portrait",
            data=form_data,
            files=files_large,
            headers=headers_owner,  # 群主亲自上传大文件，也得被拦
        )
        assert res_avatar_large.status_code == 400
        assert "不能超过 2MB" in res_avatar_large.text

        # 4. 边界测试：上传不支持的文件格式 (如 .txt) -> 报错 400
        files_txt = {
            "file": (
                "bad_avatar.txt",
                b"I am a text file",
                "text/plain")}

        res_avatar_txt = await client.put(
            "/api/group/edit/portrait",
            data=form_data,
            files=files_txt,
            headers=headers_owner,
        )
        assert res_avatar_txt.status_code == 400
        assert "不支持的图片格式" in res_avatar_txt.text

        # ---------------------------------------------------------
        # 6. 群邀请与审核 (POST /api/group/invite & POST /api/group/invite/review)
        # ---------------------------------------------------------
        # ========== 👇 新增测试：验证非好友单人邀请被拦截 ==========
        res_invite_forbidden = await client.post(
            "/api/group/invite",
            json={"conversation_id": conversation_id, "user_id": user_non_friend_id},
            headers=headers_member,
        )
        assert res_invite_forbidden.status_code == 403, "安全漏洞：竟然可以邀请非好友！"
        # =========================================================
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
        # 6.5 批量邀请成员 (POST /api/group/invite/batch)
        # ---------------------------------------------------------
        # 准备两个新的陌生人用于批量邀请测试
        batch_invitee_1_id = None
        batch_invitee_2_id = None
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            hashed_pw = get_password_hash("password123")
            batch_invitee_1_id = await db_create_user(
                conn, "batch_user_1", hashed_pw, "batch1@test.com"
            )
            batch_invitee_2_id = await db_create_user(
                conn, "batch_user_2", hashed_pw, "batch2@test.com"
            )
            # 新增：手动缔结批量好友关系
            await conn.execute(
                """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1), ($1, $3), ($3, $1) ON CONFLICT DO NOTHING;
                """,
                user_member_id, batch_invitee_1_id, batch_invitee_2_id
            )
            # 新增结束
            break

        # Member 尝试批量邀请这两个新人，外加他自己（测试过滤逻辑）
        batch_payload = {
            "conversation_id": conversation_id,
            "user_ids": [
                batch_invitee_1_id,
                batch_invitee_2_id,
                user_member_id,
                user_non_friend_id]}

        res_batch_invite = await client.post(
            "/api/group/invite/batch",
            json=batch_payload,
            headers=headers_member,
        )
        assert res_batch_invite.status_code == 200, "批量邀请接口应返回成功"

        batch_data = res_batch_invite.json().get("data", {})
        applies = batch_data.get("applies", [])

        # 验证返回数据：应只生成2条申请记录（过滤掉了自己）
        assert len(applies) == 2, "批量邀请应过滤掉自己，生成两条记录"

        # 验证生成的申请记录里绝对没有那个非好友
        assert not any(
            item["user_id"] == user_non_friend_id for item in applies), "安全漏洞：非好友被成功批量邀请了"

        apply_id_1 = next(
            item["apply_id"] for item in applies if item["user_id"] == batch_invitee_1_id)
        apply_id_2 = next(
            item["apply_id"] for item in applies if item["user_id"] == batch_invitee_2_id)

        assert apply_id_1 > 0 and apply_id_2 > 0

        # ========== 验证：管理员/群主待处理列表包含这两条新申请 ==========
        res_pending_admin_batch = await client.get(
            "/api/group/invites/pending",
            headers=headers_admin,
        )
        assert res_pending_admin_batch.status_code == 200
        pending_cards_batch = res_pending_admin_batch.json()["data"]

        found_apply_1 = any(
            card["apply_id"] == apply_id_1 for card in pending_cards_batch)
        found_apply_2 = any(
            card["apply_id"] == apply_id_2 for card in pending_cards_batch)

        assert found_apply_1 and found_apply_2, "Admin的待处理列表中应包含批量邀请生成的两条申请"

        # ========== 验证：这两条申请可以在后续通过相同的 review 接口审批 ==========
        # Admin 批准第一个申请
        res_approve_batch_1 = await client.post(
            "/api/group/invite/review",
            json={"apply_id": apply_id_1, "status": "APPROVED"},
            headers=headers_admin,
        )
        assert res_approve_batch_1.status_code == 200

        # 验证批量邀请的第一个人已入群
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            is_member_1 = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM conversation_member WHERE conversation_id = $1 AND member_user_id = $2)",
                conversation_id, batch_invitee_1_id
            )
            assert is_member_1 is True, "批量邀请被批准后，用户应加入群聊"
            break

        # =================================================================
        # 🌟 新增：6.6 测试状态机自动结算机制（边缘案例与核心动态分母）
        # =================================================================

        # 【当前状态快照】：
        # 群里有权审批的人（委员会）：Owner(user_owner_id) 和 Admin(user_admin_id)
        # 待处理申请：apply_id_2 (由 batch_invitee_2_id 触发)

        # ---- 触发器测试 A：管理员变动/降级导致申请自动拒绝 ----

        # 1. 首先让群主 (Owner) 忽略这个 apply_id_2
        res_owner_ignore = await client.post(
            "/api/group/invite/review",
            json={"apply_id": apply_id_2, "status": "IGNORED"},
            headers=headers_owner,
        )
        assert res_owner_ignore.status_code == 200

        # 2. 检查数据库：此时 Owner 忽略了，但 Admin 还没操作，全局状态应该还是 pending
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            g_status = await conn.fetchval("SELECT status FROM group_invite WHERE invite_id = $1", apply_id_2)
            assert g_status == "pending", "仅一人忽略时，申请不应该死掉"
            break

        # 3. 核心大招：Owner 突然撤销了 Admin 的管理员职位（降级为普通成员）
        # 这会触发 db_remove_admin_invite_states，清除 Admin 的待办，并重新计算分母！
        res_demote_admin = await client.put(
            "/api/group/admin",
            json={
                "conversation_id": conversation_id,
                "user_id": user_admin_id,
                "role": "member",
            },
            headers=headers_owner,
        )
        assert res_demote_admin.status_code == 200

        # 4. 强力断言：此时群里唯一的“有效在职管理员”只剩下 Owner 自己了，而 Owner 之前已经点了忽略。
        # 也就是说，“有效忽略率”达到了 100%，申请应该被系统默默置为 'rejected'！
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            g_status = await conn.fetchval("SELECT status FROM group_invite WHERE invite_id = $1", apply_id_2)
            assert g_status == "rejected", "🌟 状态机故障：管理员降级后，老申请未被自动拒绝！"
            break

        # ---- 触发器测试 B：全员忽略导致申请自动拒绝 ----

        # 为了测试纯粹的“全员忽略”，我们先恢复 Admin 身份，并由 Member 触发一笔崭新的加群申请
        res_restore_admin = await client.put(
            "/api/group/admin",
            json={"conversation_id": conversation_id, "user_id": user_admin_id, "role": "admin"},
            headers=headers_owner,
        )
        assert res_restore_admin.status_code == 200

        # Member 再次邀请 batch_invitee_2_id 入群，产生全新 pending 申请
        res_new_invite = await client.post(
            "/api/group/invite",
            json={"conversation_id": conversation_id, "user_id": batch_invitee_2_id},
            headers=headers_member,
        )
        apply_id_3 = res_new_invite.json()["data"]["apply_id"]

        # 1. 现任管理员之一 Admin 点忽略
        await client.post(
            "/api/group/invite/review",
            json={"apply_id": apply_id_3, "status": "IGNORED"},
            headers=headers_admin,
        )
        # 2. 现任管理员之二 Owner 也点忽略
        await client.post(
            "/api/group/invite/review",
            json={"apply_id": apply_id_3, "status": "IGNORED"},
            headers=headers_owner,
        )

        # 3. 强力断言：所有人都点忽略了，数据库全局状态必须自动变成 'rejected'
        async for proxy_conn in get_db_conn():
            conn = cast(asyncpg.Connection, proxy_conn)
            g_status = await conn.fetchval("SELECT status FROM group_invite WHERE invite_id = $1", apply_id_3)
            assert g_status == "rejected", "🌟 状态机故障：所有管理员选择忽略后，全局状态未转为 rejected！"
            break

        # =================================================================
        # 🌟 新增测试结束，顺畅接入后续的踢人流程
        # =================================================================
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

            # 👇👇👇 新增：建立邀请人(member)和被邀请人(invitee)的好友关系 👇👇👇
            await conn.execute(
                """
                INSERT INTO friend_relationship (user_id, friend_user_id)
                VALUES ($1, $2), ($2, $1) ON CONFLICT DO NOTHING;
                """,
                user_member_id, user_invitee_id
            )
            # 👆👆👆 新增结束 👆👆👆

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
