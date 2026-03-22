from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.ws_manager import manager

router = APIRouter()

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await manager.connect(websocket, user_id)
    await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已上线"})
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # 拦截心跳包
            if data.get("type") == "ping":
                manager.update_heartbeat(user_id)
                # 必须回复 pong，让前端知道服务器还活着
                await manager.send_personal_message({"type": "pong"}, user_id)
                continue # 跳过后续聊天分发逻辑
            
            target_id = data.get("target_id")
            content = data.get("content")
            
            if target_id:
                await manager.send_personal_message({
                    "type": "private", "from": user_id, "content": content
                }, target_id)
            else:
                await manager.broadcast({
                    "type": "broadcast", "from": user_id, "content": content
                })
                
    except WebSocketDisconnect:
        await manager.disconnect(user_id)
        await manager.broadcast({"type": "system", "message": f"用户 {user_id} 已离线"})