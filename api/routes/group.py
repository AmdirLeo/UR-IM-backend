from fastapi import APIRouter
from api.dependencies import CurrentUserId, DBConnection
from schemas.group import (
    GroupGenericResponse,
    GroupCreateRequest,
    GroupCreateData,
    GroupInfoRequest,
    GroupInfoData,
    GroupMembersRequest,
    GroupMembersData,
    GroupAdminRequest,
)
from services.group_service import (
    create_group_service,
    get_group_info_service,
    get_group_members_service,
    manage_group_admin_service,
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


@router.post(
    "/members",
    summary="获取群聊成员列表",
    response_model=GroupGenericResponse[GroupMembersData],
)
async def get_group_members(
    req: GroupMembersRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await get_group_members_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)


@router.put("/admin", summary="群权限管理", response_model=GroupGenericResponse[None])
async def manage_group_admin(
    req: GroupAdminRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await manage_group_admin_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)
