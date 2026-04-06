from fastapi import APIRouter, Depends
from typing import List, Annotated
from schemas.message import SendMessageRequest
from api.dependencies import get_current_user_id, CurrentUserId, DBConnection
from db.database import get_db_conn
import asyncpg
from services.message_service import send_message_service

router = APIRouter()


@router.post("/send", summary="发送消息")
async def send_message(
    req: SendMessageRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await send_message_service(db_session, current_user_id, req)
    return {"code": 200, "data": data}


@router.get(
    "/history",
    response_model=List[MessageResponse],
    summary="获取历史漫游消息",
    description="（Mock阶段）前端通过此接口拉取最近的聊天记录。必须在 Header 中携带合法的 JWT Token。",
)
async def get_message_history(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    target_id: int = None,
    # 挂载保安：只有带着合法 Token 的人才能调用这个接口！
):
    # 模拟从数据库返回的数据
    mock_data = [
        MessageResponse(
            id=1,
            sender_id=target_id or 2,
            target_id=current_user_id,
            content="你好呀，这是来自后端的历史消息！",
            msg_type="private",
            created_at=datetime.now(timezone.utc),
        )
    ]
    return mock_data
