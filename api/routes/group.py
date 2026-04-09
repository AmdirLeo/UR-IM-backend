from fastapi import APIRouter
from api.dependencies import CurrentUserId, DBConnection
from schemas.group import (
    GroupGenericResponse,
    GroupCreateRequest,
    GroupCreateData,
    GroupInfoRequest,
    GroupInfoData,
)
from services.group_service import (
    create_group_service,
    get_group_info_service,
)


router = APIRouter()


@router.post(
    "/create", summary="创建群聊", response_model=GroupGenericResponse[GroupCreateData]
)
async def create_group(
    req: GroupCreateRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await create_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)


@router.post(
    "/info", summary="获取群聊信息", response_model=GroupGenericResponse[GroupInfoData]
)
async def get_group_info(
    req: GroupInfoRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await get_group_info_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)
