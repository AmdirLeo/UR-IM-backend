import jwt
import json
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
            # 【修改点 1】：加一层 try-except 防止前端发错数据导致你的后端直接崩溃断开
            try:
                data = await websocket.receive_json()
            except json.JSONDecodeError:
                await manager.send_personal_message({"type": "error", "message": "请发送 JSON 格式"}, user_id)
                continue
            except WebSocketDisconnect:    # 👈 新增：精准拦截断开连接的异常
                raise                      # 👈 新增：直接往上抛，交给外层的正常离线逻辑处理
            except Exception as e:
                print(f"WebSocket 接收异常: {e}")
                break

            # 拦截心跳包
            if data.get("type") == "ping":
                manager.update_heartbeat(user_id)
                await manager.send_personal_message({"type": "pong"}, user_id)
                continue

            # 聊天分发逻辑
            target_id_raw = data.get("target_id")
            content = data.get("content")

            # 【修改点 2】：安全强转 target_id 为 int，匹配字典的 Key
            target_id = int(target_id_raw) if target_id_raw is not None and str(
                target_id_raw).isdigit() else None

            if target_id:
                await manager.send_personal_message({"type": "private", "from": user_id, "content": content}, target_id)
            else:
                await manager.broadcast({"type": "broadcast", "from": user_id, "content": content})

    except WebSocketDisconnect:
        await manager.disconnect(user_id)
        await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已离线"})
