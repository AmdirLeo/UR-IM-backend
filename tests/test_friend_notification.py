import jwt
import time
import pytest
from httpx import AsyncClient, ASGITransport
from main import app
from core.config import settings
from db.database import get_db_conn # 导入获取数据库连接的方法

@pytest.mark.asyncio
async def test_friend_request_realtime_notification():
    # 1. 准备数据
    target_user_id = 2
    sender_user_id = 1

    async for conn in get_db_conn():
        # 探测列名
        columns = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_account'")
        print(f"\n[DEBUG] user_account 表的列名有: {[col['column_name'] for col in columns]}")
        # ... 后面的代码
    
    # --- 【新增】造人逻辑：先在数据库里创建这两个用户 ---
    # 否则外键约束会报错
    async for conn in get_db_conn():
        # 往用户表塞入两个测试账号（根据你的 user_account 表结构调整字段）
        await conn.execute(
            "INSERT INTO user_account (user_id, username, password, email) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
            sender_user_id, "sender_test", "hashed_pwd", "sender@test.com"
        )
        await conn.execute(
            "INSERT INTO user_account (user_id, username, password, email) VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
            target_user_id, "target_test", "hashed_pwd", "target@test.com"
        )
        break # 拿到连接并操作完就退出循环
    # ------------------------------------------------

    token_b = jwt.encode({"sub": str(target_user_id), "exp": time.time() + 600}, settings.JWT_SECRET_KEY, algorithm="HS256")
    token_a = jwt.encode({"sub": str(sender_user_id), "exp": time.time() + 600}, settings.JWT_SECRET_KEY, algorithm="HS256")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        from starlette.testclient import TestClient
        with TestClient(app).websocket_connect(f"/chat/ws?token={token_b}") as websocket:
            
            # 这里的路径请确保是你在 Swagger 里看到的那个（带不带双重 friend）
            response = await ac.post(
                "/api/friend/friend/apply", 
                json={"target_user_id": target_user_id, "message": "你好，我是测试用户"},
                headers={"Authorization": f"Bearer {token_a}"}
            )

            # 4. 检查结果
            assert response.status_code == 200, f"申请失败原因: {response.text}"
            
            # 5. 【修正点】由于一上线会收到系统广播，我们需要过滤掉它，直到拿到好友申请
            # 方案：循环接收，直到收到 type 为 FRIEND_REQUEST 的消息
            friend_notice = None
            for _ in range(5): # 最多尝试收5次，防止死循环
                data = websocket.receive_json()
                if data.get("type") == "FRIEND_REQUEST_RECEIVED":
                    friend_notice = data
                    break
                else:
                    print(f"收到并忽略了一条非目标消息: {data.get('type')}")

            # 6. 最终验证
            assert friend_notice is not None, "未能收到好友申请通知"
            assert friend_notice["type"] == "FRIEND_REQUEST_RECEIVED"
            assert friend_notice["data"]["from_user_id"] == sender_user_id

    print("\n✨ 完美！全异步链路测试【真正】通过！")
    print("系统上线通知已成功过滤，好友申请通知已准确送达。")