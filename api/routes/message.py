from fastapi import APIRouter, Depends
from typing import List, Annotated
from schemas.message import (
    SendMessageRequest,
    MessageHistoryRequest,
)
from api.dependencies import CurrentUserId, DBConnection
from services.message_service import send_message_service, get_message_history_service

router = APIRouter()


@router.post("/send", summary="发送消息")
async def send_message(
    req: SendMessageRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await send_message_service(db_session, current_user_id, req)
    return {"code": 200, "data": data}


@router.post("/history", summary="获取历史漫游消息")
async def get_message_history(
    req: MessageHistoryRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    history = await get_message_history_service(
        db_session, current_user_id, req.conversation_id, req.start_msg_id, req.limit
    )
    return {"code": 200, "data": history}
