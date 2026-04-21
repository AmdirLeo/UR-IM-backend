import pytest
import asyncpg
from httpx import AsyncClient, ASGITransport
from typing import Dict, cast

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
        # 获取群信息，验证公告存在
        res_info = await client.post(
            "/api/group/info",
            json={"conversation_id": conversation_id},
            headers=headers_owner,
        )
        assert res_info.status_code == 200
        assert res_info.json()[
            "data"]["latest_announcement"]["content"] == "Assemble!"
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
        # ---------------------------------------------------------
        # 6. 群邀请与审核 (POST /api/group/invite & PUT /api/group/invite/review)
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
        # Admin 拒绝一次试试 (或者通过)
        res_review = await client.put(
            "/api/group/invite/review",
            json={"apply_id": apply_id, "status": "APPROVED"},
            headers=headers_admin,
        )
        assert res_review.status_code == 200
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
