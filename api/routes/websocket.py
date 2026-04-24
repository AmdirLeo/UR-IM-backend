import jwt
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from core.ws_manager import manager
from core.config import settings
from typing import Optional, Dict, Any

router = APIRouter()


async def authenticate_websocket(websocket: WebSocket, token: str) -> Optional[int]:
    """提取鉴权逻辑：返回 user_id 或 None"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[getattr(settings, "ALGORITHM", "HS256")]
        )
        user_id_str = payload.get("sub")
        if user_id_str:
            return int(user_id_str)
    except (jwt.InvalidTokenError, ValueError):
        pass

    return None


async def handle_message_logic(user_id: int, data: dict):
    """提取消息分发逻辑"""
    # 处理心跳
    if data.get("type") == "ping":
        manager.update_heartbeat(user_id)
        await manager.send_personal_message({"type": "pong"}, user_id)
        return

    # 聊天路由逻辑 (适配你之前的 polymorphic 架构)
    target_id_raw = data.get("target_id")
    content = data.get("content")

    # 安全转换 target_id
    target_id = (
        int(target_id_raw)
        if target_id_raw and str(target_id_raw).isdigit()
        else None
    )

    if target_id:
        await manager.send_personal_message(
            {"type": "private", "from": user_id, "content": content},
            target_id
        )
    else:
        await manager.broadcast(
            {"type": "broadcast", "from": user_id, "content": content}
        )


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    # 要求前端通过 ?token=xxx 传入 JWT
    token: str = Query(..., description="JWT Token"),
):
    await websocket.accept()
    # 1. 鉴权阶段
    user_id = await authenticate_websocket(websocket, token)
    if user_id is None:
        # 鉴权失败：告知客户端后关闭
        await websocket.send_json({"type": "error", "message": "鉴权失败，无效的 Token"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. 鉴权通过，正式建立长连接
    await manager.connect(websocket, user_id)
    await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已上线"})

    try:
        while True:
            # 【修改点 1】：加一层 try-except 防止前端发错数据导致你的后端直接崩溃断开
            try:
                data = await websocket.receive_json()
            # 3. 分发逻辑
                await handle_message_logic(user_id, data)
            except json.JSONDecodeError:
                await manager.send_personal_message(
                    {"type": "error", "message": "请发送 JSON 格式"},
                    user_id
                )
            except WebSocketDisconnect:
                raise
            except Exception as e:
                print(f"WebSocket 接收异常: {e}")
                break

    except WebSocketDisconnect:
        await manager.disconnect(user_id)
        await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已离线"})
