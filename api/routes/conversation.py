from fastapi import APIRouter, Depends
from typing import Annotated
from api.dependencies import CurrentUserId, DBConnection
from services.conversation_service import sync_conversations, read_ack
from schemas.conversation import (
    ConversationGenericResponse,
    ConversationSyncItem,
    ReadAckRequest,
)

router = APIRouter()


@router.get(
    "/sync",
    summary="同步会话列表",
    response_model=ConversationGenericResponse[list[ConversationSyncItem]],
)
async def sync(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    conversations = await sync_conversations(db_session, current_user_id)
    return ConversationGenericResponse(data=conversations)


@router.post(
    "/read_ack", summary="已读回执", response_model=ConversationGenericResponse[None]
)
async def read_acknowledgement(
    req: ReadAckRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await read_ack(db_session, current_user_id, req.conversation_id, req.msg_id)
    return ConversationGenericResponse(data=None)
