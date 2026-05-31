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
        # 如果该用户已经在其他设备登录，先踢掉旧的连接（单点登录逻辑）
        if user_id in self.active_connections:
            old_ws = self.active_connections[user_id]["ws"]

            # 👇 --- 新增的核心逻辑：在断开前发通知 ---
            try:
                # 1. 抢在断开前，给旧设备发一条专属的“被踢”消息
                await old_ws.send_json({
                    "type": "system",
                    "msg_type": "kicked_out",
                    "message": "您的账号已在其他设备登录，您已被强制下线。"
                })
            except Exception as e:
                print(f"发送踢出通知时出现异常: {e}")
            # 👆 --------------------------------------

            await self.disconnect(user_id, code=1008)

        self.active_connections[user_id] = {
            "ws": websocket, "last_active": time.time()}

    async def disconnect(self, user_id: int, code: int = 1000):
        """主动断开并清理内存"""
        connection = self.active_connections.pop(user_id, None)
        if connection:
            ws = connection["ws"]
            try:
                await ws.close(code=code)
            except Exception:
                pass

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
        # 1. 必须套上 list()，拷贝静态列表，防止 RuntimeError
        user_ids = list(self.active_connections.keys())

        # 2. 收集所有的发送任务，使用 gather 并发发送，速度提升10倍
        tasks = [self.send_personal_message(message, uid) for uid in user_ids]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def purge_timeouts(self):
        current_time = time.time()
        timeout_users = [
            uid
            for uid, conn in self.active_connections.items()
            if current_time - conn["last_active"] > self.HEARTBEAT_TIMEOUT
        ]

        for uid in timeout_users:
            print(f"[Heartbeat] 发现僵尸连接，强制踢出用户 {uid}")
            await self.disconnect(uid)
            await self.broadcast({"type": "system", "message": f"用户 {uid} 连接超时已离线"})

    async def check_heartbeats(self):
        while True:
            await asyncio.sleep(10)
            await self.purge_timeouts()


# 全局单例
manager = ConnectionManager()
