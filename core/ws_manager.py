import asyncio
import time
from fastapi import WebSocket
from typing import Dict, Any


class ConnectionManager:
    def __init__(self):
        # 升级后的数据结构: {user_id: {"ws": WebSocket, "last_active": timestamp}}
        self.active_connections: Dict[int, Dict[str, Any]] = {}
        # 心跳超时阈值：如果 60 秒没收到 ping，就认为该用户已掉线
        self.HEARTBEAT_TIMEOUT = 60

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        # 如果该用户已经在其他设备登录，先踢掉旧的连接（单点登录逻辑）
        if user_id in self.active_connections:
            await self.disconnect(user_id)

        self.active_connections[user_id] = {
            "ws": websocket,
            "last_active": time.time()
        }

    async def disconnect(self, user_id: int):
        """主动断开并清理内存"""
        if user_id in self.active_connections:
            ws = self.active_connections[user_id]["ws"]
            try:
                await ws.close()
            except Exception:
                pass
            del self.active_connections[user_id]

            # 【预留给数据库同学 TODO】: 在这里异步更新数据库，将用户的在线状态设为 False，更新最后离线时间

    def update_heartbeat(self, user_id: int):
        """刷新用户的最后活跃时间"""
        if user_id in self.active_connections:
            self.active_connections[user_id]["last_active"] = time.time()

    async def send_personal_message(self, message: dict, user_id: int):
        if user_id in self.active_connections:
            ws = self.active_connections[user_id]["ws"]
            try:
                await ws.send_json(message)
            except Exception:
                # 发送失败说明连接已断，直接清理
                await self.disconnect(user_id)

    async def broadcast(self, message: dict):
        # 为了避免在遍历字典时修改字典引发报错，先拷贝一份 user_id 列表
        for user_id in self.active_connections.keys():
            await self.send_personal_message(message, user_id)

    async def purge_timeouts(self):
        current_time = time.time()
        timeout_users = [
            uid for uid, conn in self.active_connections.items()
            if current_time - conn["last_active"] > self.HEARTBEAT_TIMEOUT
        ]

        for uid in timeout_users:
            print(f"[Heartbeat] 发现僵尸连接，强制踢出用户 {uid}")
            await self.disconnect(uid)
            await self.broadcast({
                "type": "system",
                "message": f"用户 {uid} 连接超时已离线"
            })

    async def check_heartbeats(self):
        while True:
            await asyncio.sleep(10)
            await self.purge_timeouts()


# 全局单例
manager = ConnectionManager()
