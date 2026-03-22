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
    return f"/chat/ws?token={token}"

# ==========================================
# 基础鉴权与通信测试
# ==========================================

def test_websocket_auth_failure():
    """测试安全机制：携带无效 Token 应该被服务器拒绝连接"""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/chat/ws?token=invalid_fake_token"):
            pass
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
    """测试单点登录机制：同 ID 异地登录，旧连接被断开"""
    url = get_ws_url(1)
    with client.websocket_connect(url) as ws_device_a:
        ws_device_a.receive_json() 
        
        with client.websocket_connect(url) as ws_device_b:
            ws_device_b.receive_json() 
            
            with pytest.raises(WebSocketDisconnect):
                ws_device_a.receive_json()

def test_heartbeat_timeout_purge():
    """测试后台任务：清理超时连接并向其他人广播"""
    class MockWebSocket:
        def __init__(self):
            self.messages = []
        async def close(self): 
            pass
        async def send_json(self, data):
            self.messages.append(data)
            
    manager.active_connections.clear()
    
    manager.active_connections[1] = {
        "ws": MockWebSocket(),
        "last_active": time.time() - 100 
    }
    
    ws2 = MockWebSocket()
    manager.active_connections[2] = {
        "ws": ws2,
        "last_active": time.time() 
    }
    
    asyncio.run(manager.purge_timeouts())
    
    assert 1 not in manager.active_connections 
    assert 2 in manager.active_connections     
    assert len(ws2.messages) == 1              
    assert "已离线" in ws2.messages[0]["message"]

def test_websocket_missing_sub_in_token():
    """测试 WebSocket 鉴权层：如果 Token 签名合法，但缺少 sub 字段，应拒绝连接"""
    payload_without_sub = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10)
    }
    malformed_token = jwt.encode(payload_without_sub, settings.JWT_SECRET_KEY, algorithm="HS256")
    
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/chat/ws?token={malformed_token}"):
            pass
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