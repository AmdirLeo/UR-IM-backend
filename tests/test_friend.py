import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict

# 引入项目核心依赖
from main import app
from api.routes.friend import router
from core.exceptions import setup_exception_handlers

from core.config import settings
from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
# 注意：之前的 mock 测试里 prefix 用的就是 "/api"，保持一致！
app.include_router(router, prefix="/api")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
async def test_friend_journey_and_edge_cases():
    """
    全量好友功能的 E2E 测试。
    不使用任何 Mock，完全基于真实的测试数据库和数据流转！
    """
    # ==========================================
    # 0. 准备测试数据：在数据库中创建 3 个真实用户
    # ==========================================
    user_a_id = None
    user_b_id = None
    user_c_id = None

    async for conn in get_db_conn():
        hashed_pw = get_password_hash("password123")
        # 直接利用底层函数快速创建用户，避免走 HTTP 注册需要验证码的麻烦
        # 假设每次测试前 conftest.py 都会清理数据库，邮箱不会冲突
        # type: ignore
        user_a_id = await db_create_user(conn, "friend_user_A", hashed_pw, "friend_a@test.com")
        # type: ignore
        user_b_id = await db_create_user(conn, "friend_user_B", hashed_pw, "friend_b@test.com")
        # type: ignore
        user_c_id = await db_create_user(conn, "friend_user_C", hashed_pw, "friend_c@test.com")
        break  # 取一次连接执行完毕即可

    # 为用户生成真实的 JWT Token，完美通过路由的鉴权依赖
    token_a = create_access_token(data={"sub": str(user_a_id)})
    token_b = create_access_token(data={"sub": str(user_b_id)})

    headers_a = get_auth_headers(token_a)
    headers_b = get_auth_headers(token_b)

    # 开始端到端 HTTP 测试
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:

        # ---------------------------------------------------------
        # 1. 搜索用户 (Search)
        # ---------------------------------------------------------
        # A 搜索 B
        res = await client.get("/api/friend/search?keyword=friend_user_B", headers=headers_a)
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) >= 1
        assert data[0]["username"] == "friend_user_B"

        # A 搜索不存在的用户
        res = await client.get("/api/friend/search?keyword=nobody_exists", headers=headers_a)
        assert res.status_code == 200
        assert len(res.json()["data"]) == 0

        # 参数校验异常 (关键字为空)
        res = await client.get("/api/friend/search?keyword=", headers=headers_a)
        assert res.status_code == 422

        # ---------------------------------------------------------
        # 2. 发送好友申请 (Apply)
        # ---------------------------------------------------------
        # A 申请加 B 为好友
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
        assert res.json()["msg"] == "不能添加自己为好友"

        # ---------------------------------------------------------
        # 3. 处理好友申请 (Handle)
        # ---------------------------------------------------------
        # 【难点攻克】因为业务里没有写“查询申请列表”的 HTTP 接口，
        # 所以我们需要当一回“内鬼”，直接去数据库里把刚才 A 发给 B 的 request_id 查出来。
        request_id = None
        async for conn in get_db_conn():
            req_record = await conn.fetchrow(
                "SELECT request_id FROM friend_request WHERE sender_id=$1 AND receiver_id=$2",
                user_a_id,
                user_b_id,
            )
            request_id = req_record["request_id"]
            break

        # B 同意 A 的申请 (注意这里换成了 headers_b，代表 B 在操作)
        res = await client.put(
            "/api/friend/handle",
            json={"request_id": request_id, "action": "accepted"},
            headers=headers_b,
        )
        assert res.status_code == 200
        assert res.json()["msg"] == "已同意好友申请"

        # ---------------------------------------------------------
        # 4. 获取好友列表 (Get List)
        # ---------------------------------------------------------
        # 此时 A 的列表里应该有 B
        res = await client.get("/api/friend", headers=headers_a)
        assert res.status_code == 200
        friends = res.json()["data"]
        assert len(friends) >= 1
        # 验证返回的数据里包含 B 的 ID
        assert any(f["user_id"] == user_b_id for f in friends)

        # ---------------------------------------------------------
        # 5. 好友分组标签流转 (Tag Journey)
        # ---------------------------------------------------------
        tag_name = "BestFriends"

        # 1. 新建标签
        res = await client.post("/api/friend/tag/new", json={"tag_name": tag_name}, headers=headers_a)
        assert res.status_code == 200

        # 2. 模拟重名标签冲突 (409)
        res = await client.post("/api/friend/tag/new", json={"tag_name": tag_name}, headers=headers_a)
        assert res.status_code == 409

        # 3. 把 B 加入标签
        res = await client.post(
            "/api/friend/tag/add",
            json={"tag_name": tag_name, "friend_ids": [user_b_id]},
            headers=headers_a,
        )
        assert res.status_code == 200

        # 4. 查询标签里的好友，应该只有 B
        res = await client.post("/api/friend/tag/query", json={"tag_name": tag_name}, headers=headers_a)
        assert res.status_code == 200
        assert len(res.json()["data"]) == 1
        assert res.json()["data"][0]["user_id"] == user_b_id

        # 5. 把 B 移出标签
        res = await client.post(
            "/api/friend/tag/remove",
            json={"tag_name": tag_name, "friend_id": user_b_id},
            headers=headers_a,
        )
        assert res.status_code == 200

        # 6. 彻底删除标签
        res = await client.post("/api/friend/tag/delete", json={"tag_name": tag_name}, headers=headers_a)
        assert res.status_code == 200

        # ---------------------------------------------------------
        # 6. 删除好友 (Remove)
        # ---------------------------------------------------------
        # A 翻脸无情，删除了 B
        res = await client.delete(f"/api/friend/remove/{user_b_id}", headers=headers_a)
        assert res.status_code == 200

        # 再次查 A 的列表，应该已经没有 B 了
        res = await client.get("/api/friend", headers=headers_a)
        friends = res.json()["data"]
        assert not any(f["user_id"] == user_b_id for f in friends)

        # 尝试删除一个根本不是好友的 C (404)
        res = await client.delete(f"/api/friend/remove/{user_c_id}", headers=headers_a)
        assert res.status_code == 404

        # ---------------------------------------------------------
        # 7. 补充边界测试：处理异常好友请求与删除自己 (覆盖 400 异常)
        # ---------------------------------------------------------
        # A 试图删除自己
        res = await client.delete(f"/api/friend/remove/{user_a_id}", headers=headers_a)
        assert res.status_code == 400
        assert res.json()["msg"] == "不能删除自己"

        # B 试图处理一个根本不存在的好友申请 (假设 99999 这个 ID 绝对不存在)
        res = await client.put(
            "/api/friend/handle",
            json={"request_id": 999999, "action": "accepted"},
            headers=headers_b,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "好友申请不存在或已被处理"

        # ---------------------------------------------------------
        # 8. 补充边界测试：好友分组的异常流转 (覆盖 404 异常)
        # ---------------------------------------------------------
        fake_tag_name = "GhostTag"

        # 删除一个不存在的分组
        res = await client.post("/api/friend/tag/delete", json={"tag_name": fake_tag_name}, headers=headers_a)
        assert res.status_code == 404
        assert res.json()["msg"] == "分组不存在"

        # 将好友加入一个不存在的分组
        res = await client.post(
            "/api/friend/tag/add",
            json={"tag_name": fake_tag_name, "friend_ids": [user_b_id]},
            headers=headers_a,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "分组不存在"

        # 从不存在的分组（或该好友压根不在该分组中）移出好友
        res = await client.post(
            "/api/friend/tag/remove",
            json={"tag_name": fake_tag_name, "friend_id": user_b_id},
            headers=headers_a,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "该好友不在当前分组中"
