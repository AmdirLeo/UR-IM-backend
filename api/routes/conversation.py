from fastapi import APIRouter, Depends
from typing import Annotated
from api.dependencies import CurrentUserId, DBConnection
from services.conversation_service import sync_conversations

router = APIRouter()


@router.get("/sync", summary="同步会话列表")
async def sync(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    conversations = await sync_conversations(db_session, current_user_id)
    return {"code": 200, "data": {"conversations": conversations}}
