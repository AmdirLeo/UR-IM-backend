from fastapi.testclient import TestClient
from main import app

# 关闭服务器内部异常抛出，与 test_exceptions.py 保持一致
client = TestClient(app, raise_server_exceptions=False)

def test_websocket_connect_and_broadcast():
    """测试用户上线时的系统广播"""
    # 模拟用户 1 连接
    with client.websocket_connect("/chat/ws/1") as websocket:
        # 刚连上应该立刻收到系统的上线广播
        data = websocket.receive_json()
        assert data["type"] == "system"
        assert "用户 1" in data["message"]
        assert "已上线" in data["message"]

def test_websocket_private_message():
    """测试点对点私聊功能"""
    # 保持用户 1 的连接开启
    with client.websocket_connect("/chat/ws/1") as ws1:
        # 消耗掉用户 1 自己的上线广播
        ws1.receive_json() 
        
        # 在用户 1 在线的情况下，模拟用户 2 连接
        with client.websocket_connect("/chat/ws/2") as ws2:
            # 用户 2 收到自己的上线广播
            ws2.receive_json()
            
            # 用户 1 此时应该也收到了用户 2 上线的广播
            ws1_broadcast = ws1.receive_json()
            assert ws1_broadcast["type"] == "system"
            assert "用户 2" in ws1_broadcast["message"]

            # 【核心测试】用户 1 向用户 2 发送私聊消息
            ws1.send_json({
                "target_id": 2, 
                "content": "天王盖地虎"
            })
            
            # 用户 2 应该能准确收到这条私聊
            private_msg = ws2.receive_json()
            assert private_msg["type"] == "private"
            assert private_msg["from"] == 1
            assert private_msg["content"] == "天王盖地虎"

def test_websocket_group_broadcast():
    """测试世界频道的群发功能"""
    with client.websocket_connect("/chat/ws/1") as ws1:
        ws1.receive_json() # 消耗上线通知
        
        with client.websocket_connect("/chat/ws/2") as ws2:
            ws2.receive_json() # 消耗上线通知
            ws1.receive_json() # 消耗用户2上线的广播
            
            # 用户 2 发送不带 target_id 的群发消息
            ws2.send_json({
                "content": "宝塔镇河妖"
            })
            
            # 用户 1 应该收到这条群广播
            broadcast_msg = ws1.receive_json()
            assert broadcast_msg["type"] == "broadcast"
            assert broadcast_msg["from"] == 2
            assert broadcast_msg["content"] == "宝塔镇河妖"
            
            # 用户 2 自己也会收到这条群广播（因为 V1.0 逻辑是发给 active_connections 里的所有人）
            self_msg = ws2.receive_json()
            assert self_msg["type"] == "broadcast"
            assert self_msg["content"] == "宝塔镇河妖"