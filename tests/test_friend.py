import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict, cast
import asyncpg
from starlette.testclient import TestClient  # <--- 引入 TestClient 用于 WebSocket

# 引入项目核心依赖
from main import app
from api.routes.friend import router
from core.exceptions import setup_exception_handlers
from core.security import get_password_hash, create_access_token
from db.database import get_db_conn
from db.repositories.user_repo import db_create_user

# ==========================================
# 1. Setup FastAPI App
# ==========================================
setup_exception_handlers(app)
app.include_router(router, prefix="/api")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# 强制将测试函数绑定到 session 级别的事件循环
@pytest.mark.asyncio(loop_scope="session")
async def test_friend_journey_and_edge_cases():
    """
    全量好友功能的 E2E 测试 (包含 WebSocket 实时通知验证)。
    不使用任何 Mock，完全基于真实的测试数据库和数据流转！
    """
    # ==========================================
    # 0. 准备测试数据：在数据库中创建 3 个真实用户
    # ==========================================
    user_a_id = None
    user_b_id = None
    user_c_id = None

    async for proxy_conn in get_db_conn():
        conn = cast(asyncpg.Connection, proxy_conn)
        hashed_pw = get_password_hash("password123")

        # type: ignore
        user_a_id = await db_create_user(
            conn, "friend_user_A", hashed_pw, "friend_a@test.com"
        )
        # type: ignore
        user_b_id = await db_create_user(
            conn, "friend_user_B", hashed_pw, "friend_b@test.com"
        )
        # type: ignore
        user_c_id = await db_create_user(
            conn, "friend_user_C", hashed_pw, "friend_c@test.com"
        )
        break

    # 为用户生成真实的 JWT Token
    token_a = create_access_token(data={"sub": str(user_a_id)})
    token_b = create_access_token(data={"sub": str(user_b_id)})
    token_c = create_access_token(data={"sub": str(user_c_id)})

    headers_a = get_auth_headers(token_a)
    headers_b = get_auth_headers(token_b)
    headers_c = get_auth_headers(token_c)

    # 开始端到端 HTTP 测试
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:

        # ---------------------------------------------------------
        # 1. 搜索用户 (Search)
        # ---------------------------------------------------------
        res = await client.get(
            "/api/friend/search?keyword=friend_user_B", headers=headers_a
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) >= 1
        assert data[0]["username"] == "friend_user_B"

        res = await client.get(
            "/api/friend/search?keyword=nobody_exists", headers=headers_a
        )
        assert res.status_code == 200
        assert len(res.json()["data"]) == 0

        res = await client.get("/api/friend/search?keyword=", headers=headers_a)
        assert res.status_code == 422

        # ---------------------------------------------------------
        # 2. 发送好友申请 (Apply) + 验证 WebSocket 实时通知
        # ---------------------------------------------------------
        # 让 B 提前连上 WebSocket，等待接收通知
        with TestClient(app).websocket_connect(
            f"/websocket/ws?token={token_b}"
        ) as websocket:

            # A 申请加 B 为好友
            res = await client.post(
                "/api/friend/apply",
                json={"target_user_id": user_b_id, "message": "hello B"},
                headers=headers_a,
            )
            assert res.status_code == 200

            # 验证 B 是否在 WebSocket 瞬间收到了实时推送
            friend_notice = None
            for _ in range(5):  # 过滤系统广播等杂音
                ws_data = websocket.receive_json()
                if ws_data.get("type") == "FRIEND_REQUEST_RECEIVED":
                    friend_notice = ws_data
                    break
                else:
                    print(f"收到并忽略了一条非目标消息: {ws_data.get('type')}")

            # 断言通知的准确性
            assert friend_notice is not None, "未能收到好友申请通知"
            assert friend_notice["type"] == "FRIEND_REQUEST_RECEIVED"
            assert friend_notice["data"]["from_user_id"] == user_a_id

        # [异常流测试] A 再次申请，触发 409
        res = await client.post(
            "/api/friend/apply",
            json={"target_user_id": user_b_id, "message": "hello again"},
            headers=headers_a,
        )
        assert res.status_code == 409
        assert "待处理" in res.json()["msg"]

        # [异常流测试] A 不能申请加自己
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
        request_id = None
        async for conn in get_db_conn():
            req_record = await conn.fetchrow(
                "SELECT request_id FROM friend_request WHERE sender_id=$1 AND receiver_id=$2",
                user_a_id,
                user_b_id,
            )
            request_id = req_record["request_id"]
            break

        # B 同意 A 的申请
        res = await client.put(
            "/api/friend/handle",
            json={"request_id": request_id, "action": "accepted"},
            headers=headers_b,
        )
        assert res.status_code == 200
        assert res.json()["msg"] == "已同意好友申请"

        # [异常流测试] A 尝试加已经是好友的 B
        res = await client.post(
            "/api/friend/apply",
            json={"target_user_id": user_b_id, "message": "add me plz"},
            headers=headers_a,
        )
        assert res.status_code == 409
        assert "已经是好友" in res.json()["msg"]

        # ---------------------------------------------------------
        # 3.5 补充测试：拒绝好友申请 (覆盖 repo 层的 action="rejected" 返回)
        # ---------------------------------------------------------
        # C 申请加 A 为好友
        res = await client.post(
            "/api/friend/apply",
            json={"target_user_id": user_a_id, "message": "Let's be friends!"},
            headers=headers_c,
        )
        assert res.status_code == 200

        # 去数据库捞取刚才 C 发给 A 的这条申请记录的 ID
        reject_request_id = None
        async for conn in get_db_conn():
            req_record = await conn.fetchrow(
                "SELECT request_id FROM friend_request WHERE sender_id=$1 AND receiver_id=$2 AND status='pending'",
                user_c_id,
                user_a_id,
            )
            reject_request_id = req_record["request_id"]
            break

        # A 残忍拒绝 C 的申请 (触发 action="rejected" 分支)
        res = await client.put(
            "/api/friend/handle",
            json={"request_id": reject_request_id, "action": "rejected"},
            headers=headers_a,
        )
        assert res.status_code == 200
        # 断言具体取决于你的 Router 返回格式，一般会是：
        # assert "拒绝" in res.json()["msg"] 或 assert res.json()["code"] == 200

        # 验证 A 的好友列表里确实没有 C
        res = await client.get("/api/friend", headers=headers_a)
        friends = res.json()["data"]
        assert not any(f["user_id"] == user_c_id for f in friends)

        # ---------------------------------------------------------
        # 4. 获取好友列表 (Get List)
        # ---------------------------------------------------------
        res = await client.get("/api/friend", headers=headers_a)
        assert res.status_code == 200
        friends = res.json()["data"]
        assert len(friends) >= 1
        assert any(f["user_id"] == user_b_id for f in friends)

        # ---------------------------------------------------------
        # 5. 好友分组标签流转 (Tag Journey)
        # ---------------------------------------------------------
        tag_name = "BestFriends"

        res = await client.post(
            "/api/friend/tag/new", json={"tag_name": tag_name}, headers=headers_a
        )
        assert res.status_code == 200

        res = await client.post(
            "/api/friend/tag/new", json={"tag_name": tag_name}, headers=headers_a
        )
        assert res.status_code == 409

        res = await client.post(
            "/api/friend/tag/add",
            json={"tag_name": tag_name, "friend_ids": [user_b_id]},
            headers=headers_a,
        )
        assert res.status_code == 200

        res = await client.post(
            "/api/friend/tag/query", json={"tag_name": tag_name}, headers=headers_a
        )
        assert res.status_code == 200
        assert len(res.json()["data"]) == 1
        assert res.json()["data"][0]["user_id"] == user_b_id

        res = await client.post(
            "/api/friend/tag/remove",
            json={"tag_name": tag_name, "friend_id": user_b_id},
            headers=headers_a,
        )
        assert res.status_code == 200

        res = await client.post(
            "/api/friend/tag/delete", json={"tag_name": tag_name}, headers=headers_a
        )
        assert res.status_code == 200

        # ---------------------------------------------------------
        # 6. 删除好友 (Remove)
        # ---------------------------------------------------------
        res = await client.delete(f"/api/friend/remove/{user_b_id}", headers=headers_a)
        assert res.status_code == 200

        res = await client.get("/api/friend", headers=headers_a)
        friends = res.json()["data"]
        assert not any(f["user_id"] == user_b_id for f in friends)

        res = await client.delete(f"/api/friend/remove/{user_c_id}", headers=headers_a)
        assert res.status_code == 404

        # ---------------------------------------------------------
        # 7. 补充边界测试：处理异常好友请求与删除自己 (覆盖 400 异常)
        # ---------------------------------------------------------
        res = await client.delete(f"/api/friend/remove/{user_a_id}", headers=headers_a)
        assert res.status_code == 400
        assert res.json()["msg"] == "不能删除自己"

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

        res = await client.post(
            "/api/friend/tag/delete",
            json={"tag_name": fake_tag_name},
            headers=headers_a,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "分组不存在"

        res = await client.post(
            "/api/friend/tag/add",
            json={"tag_name": fake_tag_name, "friend_ids": [user_b_id]},
            headers=headers_a,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "分组不存在"

        res = await client.post(
            "/api/friend/tag/remove",
            json={"tag_name": fake_tag_name, "friend_id": user_b_id},
            headers=headers_a,
        )
        assert res.status_code == 404
        assert res.json()["msg"] == "该好友不在当前分组中"
