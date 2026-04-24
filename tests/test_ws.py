import pytest
import asyncio
import time
import jwt
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app
from core.security import create_access_token
from core.ws_manager import manager
from core.config import settings

# 1. 初始化测试客户端（关闭服务器内部异常抛出）
client = TestClient(app, raise_server_exceptions=False)


# 2. 辅助函数：快速生成带有有效 Token 的 WebSocket URL
def get_ws_url(user_id: int) -> str:
    token = create_access_token(data={"sub": str(user_id)})
    return f"/websocket/ws?token={token}"


# ==========================================
# 基础鉴权与通信测试
# ==========================================


def test_websocket_auth_failure():
    """测试安全机制：携带无效 Token 应该被服务器拒绝连接"""
    with client.websocket_connect("/websocket/ws?token=invalid_fake_token") as ws:
        # 接收服务器返回的错误提醒
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert "鉴权失败" in error_msg["message"]
        # 随后服务器会主动关闭连接，下一次接收应抛出 WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


def test_websocket_connect_and_broadcast():
    """测试携带合法 Token 的用户上线广播"""
    with client.websocket_connect(get_ws_url(1)) as websocket:
        data = websocket.receive_json()
        assert data["type"] == "system"
        assert "用户 1" in data["message"]
        assert "已上线" in data["message"]


def test_websocket_private_message():
    """测试点对点私聊功能"""
    with client.websocket_connect(get_ws_url(1)) as ws1:
        ws1.receive_json()

        with client.websocket_connect(get_ws_url(2)) as ws2:
            ws2.receive_json()
            ws1_broadcast = ws1.receive_json()
            assert ws1_broadcast["type"] == "system"

            ws1.send_json({"target_id": 2, "content": "天王盖地虎"})

            private_msg = ws2.receive_json()
            assert private_msg["type"] == "private"
            assert private_msg["from"] == 1
            assert private_msg["content"] == "天王盖地虎"


def test_websocket_group_broadcast():
    """测试世界频道的群发功能"""
    with client.websocket_connect(get_ws_url(1)) as ws1:
        ws1.receive_json()

        with client.websocket_connect(get_ws_url(2)) as ws2:
            ws2.receive_json()
            ws1.receive_json()

            ws2.send_json({"content": "宝塔镇河妖"})

            broadcast_msg = ws1.receive_json()
            assert broadcast_msg["type"] == "broadcast"
            assert broadcast_msg["from"] == 2


# ==========================================
# 高级机制与极限边缘测试
# ==========================================


def test_websocket_ping_pong_heartbeat():
    """测试心跳包机制：发 ping 必须回 pong"""
    with client.websocket_connect(get_ws_url(1)) as ws:
        ws.receive_json()
        ws.send_json({"type": "ping"})

        response = ws.receive_json()
        assert response["type"] == "pong"


def test_websocket_single_sign_on_kick():
    """测试单点登录机制：同 ID 异地登录，旧连接先收到踢出通知，随后被断开"""
    # 为了防止其他测试用例的残留数据干扰，先清空连接池
    manager.active_connections.clear()

    url = get_ws_url(1)

    with client.websocket_connect(url) as ws_device_a:
        # 消耗掉设备 A 自己的上线系统广播
        ws_device_a.receive_json()

        # 此时设备 B 携带相同的 Token (相同的 user_id) 尝试连接
        with client.websocket_connect(url) as ws_device_b:
            # 消耗掉设备 B 的上线系统广播
            ws_device_b.receive_json()

            # --- 核心测试逻辑开始 ---

            # 1. 验证设备 A 是否收到了特定的踢出 JSON 通知
            kick_msg = ws_device_a.receive_json()
            assert kick_msg["type"] == "system"
            assert kick_msg["msg_type"] == "kicked_out"
            assert "其他设备" in kick_msg["message"]

            # 2. 验证发送完通知后，设备 A 的底层连接是否被后端以 1008 状态码强行关闭
            with pytest.raises(WebSocketDisconnect) as exc:
                ws_device_a.receive_json()  # 再次尝试接收会触发断开异常
            assert exc.value.code == 1008

            # --- 核心测试逻辑结束 ---


@pytest.mark.asyncio
async def test_heartbeat_timeout_purge():
    """测试后台任务：清理超时连接并向其他人广播"""

    # 1. 定义一个完整的 Mock 对象，必须包含 send_json，否则广播会报错
    class MockWebSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            # 记录收到的广播消息
            self.messages.append(data)
            # 加上这句：既满足 Manager 的 await 调用，又消除 SonarLint 的警告
            import asyncio

            await asyncio.sleep(0)

        async def close(self, code=1000):
            """模拟关闭连接。"""
            pass

        async def receive_json(self):
            import asyncio

            await asyncio.sleep(0)  # 模拟异步切换
            if not self.messages:
                return None
            return self.messages.pop(0)

    # 2. 彻底清空当前环境中的连接
    manager.active_connections.clear()

    # 3. 准备数据
    now = time.time()

    # 用户 1：已超时（100秒前活跃）
    ws1 = MockWebSocket()
    manager.active_connections[1] = {"ws": ws1, "last_active": now - 100}

    # 用户 2：刚刚活跃
    ws2 = MockWebSocket()
    manager.active_connections[2] = {"ws": ws2, "last_active": now}

    # 4. 执行清理任务（直接 await，不要用 asyncio.run，避免创建新循环导致单例失效）
    await manager.purge_timeouts()

    # 5. 最终断言
    # 用户 1 应该被移除
    assert 1 not in manager.active_connections
    # 用户 2 应该还在
    assert 2 in manager.active_connections
    # 用户 2 应该收到了关于用户 1 离线的系统通知
    assert len(ws2.messages) > 0
    assert "离线" in ws2.messages[0]["message"]


def test_websocket_missing_sub_in_token():
    """测试 WebSocket 鉴权层：如果 Token 签名合法，但缺少 sub 字段，应拒绝连接"""
    payload_without_sub = {"exp": datetime.now(
        timezone.utc) + timedelta(minutes=10)}
    malformed_token = jwt.encode(
        payload_without_sub, settings.JWT_SECRET_KEY, algorithm="HS256")

    with client.websocket_connect(f"/websocket/ws?token={malformed_token}") as ws:
        error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        # 连接随后被关闭
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


def test_websocket_explicit_client_disconnect():
    """测试 WebSocket 路由层：客户端主动断开连接时，后端的捕获与离线广播逻辑"""
    manager.active_connections.clear()

    with client.websocket_connect(get_ws_url(99)) as ws_observer:
        ws_observer.receive_json()

        with client.websocket_connect(get_ws_url(88)) as ws_actor:
            ws_observer.receive_json()
            ws_actor.close()

        offline_broadcast = ws_observer.receive_json()
        assert offline_broadcast["type"] == "system"
        assert "用户 88" in offline_broadcast["message"]
        assert "已离线" in offline_broadcast["message"]
        assert 88 not in manager.active_connections
