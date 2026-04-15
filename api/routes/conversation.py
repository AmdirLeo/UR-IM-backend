from fastapi import APIRouter, Depends, Path
import asyncpg
from typing import Annotated
from api.dependencies import CurrentUserId, DBConnection
from services.conversation_service import (
    sync_conversations,
    read_ack,
    set_conversation_mute,
    set_conversation_pin,
    get_direct_conversation_id
)
from schemas.conversation import (
    ConversationGenericResponse,
    ConversationSyncItem,
    ReadAckRequest,
    ConversationMuteRequest,
    ConversationPinRequest,
    DirectConversationResponse,
    DirectConversationData,
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


@router.get(
    "/direct/{friend_user_id}",
    summary="获取私聊会话ID",
    response_model=DirectConversationResponse,
)
async def get_direct_conversation(
    # ⚠️ 保持你优雅的风格：依赖前置，Path 参数使用 Annotated 后置
    current_user_id: CurrentUserId,
    db_session: DBConnection,
    friend_user_id: Annotated[int, Path(description="目标好友的用户ID")],
):
    """
    通过好友的用户 ID，查询并返回专属的私聊 conversation_id。
    通常用于在通讯录中点击好友头像发消息时的路由跳转兜底。
    """
    conv_id = await get_direct_conversation_id(
        db_session=db_session,
        current_user_id=current_user_id,
        friend_user_id=friend_user_id
    )

    return DirectConversationResponse(
        code=200,
        msg="获取成功",
        data=DirectConversationData(conversation_id=conv_id)
    )
