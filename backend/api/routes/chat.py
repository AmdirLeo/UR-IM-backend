from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.ws_manager import manager

router = APIRouter()

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    # 建立连接并存入内存
    await manager.connect(websocket, user_id)
    
    # 广播上线消息
    await manager.broadcast({
        "type": "system", 
        "message": f"用户 {user_id} 已上线"
    })
    
    try:
        while True:
            # 持续阻塞接收前端数据
            data = await websocket.receive_json()
            
            target_id = data.get("target_id")
            content = data.get("content")
            
            if target_id:
                # 存在 target_id 则为私聊
                await manager.send_personal_message({
                    "type": "private",
                    "from": user_id,
                    "content": content
                }, target_id)
            else:
                # 否则作为世界大厅广播
                await manager.broadcast({
                    "type": "broadcast",
                    "from": user_id,
                    "content": content
                })
                
    except WebSocketDisconnect:
        # 捕获正常断开异常，清理内存
        manager.disconnect(user_id)
        await manager.broadcast({
            "type": "system", 
            "message": f"用户 {user_id} 已离线"
        })