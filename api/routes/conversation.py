from fastapi import APIRouter, Depends
from typing import Annotated
from api.dependencies import CurrentUserId, DBConnection
from services.conversation_service import (
    sync_conversations,
    read_ack,
    set_conversation_mute,
    set_conversation_pin,
)
from schemas.conversation import (
    ConversationGenericResponse,
    ConversationSyncItem,
    ReadAckRequest,
    ConversationMuteRequest,
    ConversationPinRequest,
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


@router.post("/read_ack", summary="已读回执",
             response_model=ConversationGenericResponse[None])
async def read_acknowledgement(
    req: ReadAckRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await read_ack(db_session, current_user_id, req.conversation_id)
    return ConversationGenericResponse(data=None)


@router.put("/mute", summary="消息免打扰",
            response_model=ConversationGenericResponse[None])
async def mute_conversation(
    req: ConversationMuteRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await set_conversation_mute(db_session, current_user_id, req.conversation_id, req.is_muted)
    return ConversationGenericResponse(data=None)


@router.put("/pin", summary="置顶会话",
            response_model=ConversationGenericResponse[None])
async def pin_conversation(
    req: ConversationPinRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await set_conversation_pin(db_session, current_user_id, req.conversation_id, req.is_pinned)
    return ConversationGenericResponse(data=None)
