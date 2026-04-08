import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from core.ws_manager import manager
from core.config import settings

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    # 要求前端通过 ?token=xxx 传入 JWT
    token: str = Query(..., description="JWT Token"),
):
    # 1. 握手阶段：Token 鉴权
    try:
        # 使用与 HTTP 接口相同的规则解密 Token
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[
                             getattr(settings, "ALGORITHM", "HS256")])
        user_id_str = payload.get("sub")

        if user_id_str is None:
            # 载荷无效，拒绝连接 (1008 代表违反策略)
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user_id = int(user_id_str)

    except jwt.InvalidTokenError:
        # Token 签名错误或已过期，拒绝连接
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. 鉴权通过，正式建立长连接
    await manager.connect(websocket, user_id)
    await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已上线"})

    try:
        while True:
            data = await websocket.receive_json()

            # 拦截心跳包
            if data.get("type") == "ping":
                manager.update_heartbeat(user_id)
                await manager.send_personal_message({"type": "pong"}, user_id)
                continue

            # 聊天分发逻辑
            target_id = data.get("target_id")
            content = data.get("content")

            if target_id:
                await manager.send_personal_message({"type": "private", "from": user_id, "content": content}, target_id)
            else:
                await manager.broadcast({"type": "broadcast", "from": user_id, "content": content})

    except WebSocketDisconnect:
        await manager.disconnect(user_id)
        await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已离线"})
