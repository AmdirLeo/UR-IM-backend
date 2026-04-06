from fastapi import APIRouter, Depends
from typing import List, Annotated
from schemas.message import (
    MessageGenericResponse,
    SendMessageRequest,
    SendMessageData,
    MessageHistoryRequest,
    MessageHistoryItem,
    MessageSearchRequest,
    MessageSearchItem,
    DeleteMessageRequest,
)
from api.dependencies import CurrentUserId, DBConnection
from services.message_service import (
    send_message_service,
    get_message_history_service,
    search_message_service,
    delete_message_service,
)

router = APIRouter()


@router.post(
    "/send", summary="发送消息", response_model=MessageGenericResponse[SendMessageData]
)
async def send_message(
    req: SendMessageRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data_dict = await send_message_service(db_session, current_user_id, req)
    return MessageGenericResponse(data=data_dict)


@router.post(
    "/history",
    summary="获取历史漫游消息",
    response_model=MessageGenericResponse[List[MessageHistoryItem]],
)
async def get_message_history(
    req: MessageHistoryRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    history = await get_message_history_service(
        db_session, current_user_id, req.conversation_id, req.start_msg_id, req.limit
    )
    return MessageGenericResponse(data=history)


@router.post(
    "/search",
    summary="筛选消息记录",
    response_model=MessageGenericResponse[List[MessageSearchItem]],
)
async def search_message(
    req: MessageSearchRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    results = await search_message_service(db_session, current_user_id, req)
    return MessageGenericResponse(data=results)


@router.delete(
    "",
    summary="删除消息记录",
    response_model=MessageGenericResponse[None],
)
async def delete_message(
    req: DeleteMessageRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await delete_message_service(db_session, current_user_id, req)
    return MessageGenericResponse(data=None)
