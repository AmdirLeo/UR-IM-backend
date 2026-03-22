import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from main import app

# 关闭服务器内部异常抛出
client = TestClient(app, raise_server_exceptions=False)

def test_websocket_connect_and_broadcast():
    """测试用户上线时的系统广播"""
    with client.websocket_connect("/chat/ws/1") as websocket:
        data = websocket.receive_json()
        assert data["type"] == "system"
        assert "用户 1" in data["message"]
        assert "已上线" in data["message"]

def test_websocket_private_message():
    """测试点对点私聊功能"""
    with client.websocket_connect("/chat/ws/1") as ws1:
        ws1.receive_json() 
        
        with client.websocket_connect("/chat/ws/2") as ws2:
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
    with client.websocket_connect("/chat/ws/1") as ws1:
        ws1.receive_json() 
        
        with client.websocket_connect("/chat/ws/2") as ws2:
            ws2.receive_json() 
            ws1.receive_json() 
            
            ws2.send_json({"content": "宝塔镇河妖"})
            
            broadcast_msg = ws1.receive_json()
            assert broadcast_msg["type"] == "broadcast"
            assert broadcast_msg["from"] == 2
            
            self_msg = ws2.receive_json()
            assert self_msg["type"] == "broadcast"

# ==========================================
# 🔽 以下是为 V2.0 新增的测试用例
# ==========================================

def test_websocket_ping_pong_heartbeat():
    """测试心跳包机制：发 ping 必须回 pong"""
    with client.websocket_connect("/chat/ws/1") as ws:
        ws.receive_json() # 消耗掉上线广播
        
        # 模拟前端发送心跳保活包
        ws.send_json({"type": "ping"})
        
        # 后端应该立即回复 pong
        response = ws.receive_json()
        assert response["type"] == "pong"

def test_websocket_single_sign_on_kick():
    """测试单点登录（顶号）机制：同 ID 异地登录，旧连接被断开"""
    # 1. 设备 A 登录
    with client.websocket_connect("/chat/ws/1") as ws_device_a:
        ws_device_a.receive_json() # 消耗上线广播
        
        # 2. 设备 B 用同样的 user_id=1 登录
        with client.websocket_connect("/chat/ws/1") as ws_device_b:
            ws_device_b.receive_json() # 消耗上线广播
            
            # 3. 此时设备 A 的旧连接应该已经被服务器主动断开
            # 我们尝试用设备 A 接收消息，应该会抛出 WebSocketDisconnect 异常
            with pytest.raises(WebSocketDisconnect):
                ws_device_a.receive_json()