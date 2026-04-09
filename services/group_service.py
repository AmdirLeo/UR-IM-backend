import asyncpg
from schemas.group import GroupCreateRequest
from core.exceptions import GroupException, GroupErrors
from db.repositories.group_repo import (
    db_create_group,
)


async def create_group_service(
    db_session: asyncpg.Connection, current_user_id: int, req: GroupCreateRequest
) -> dict:
    if not req.user_ids:
        raise GroupException(GroupErrors.InvalidRequest, "好友列表不能为空")
    # 假设如果被邀请的人不存在或者其他问题，在底层的 db 层（没有提及详细错误，但通常由外键抛出或忽略）处理
    conv_id = await db_create_group(
        conn=db_session,
        creator_id=current_user_id,
        member_ids=req.user_ids,
        group_name=req.name,
    )
    return {"conversation_id": conv_id, "name": req.name, "avatar": req.avatar}
