from fastapi import APIRouter
from api.dependencies import CurrentUserId, DBConnection
from schemas.group import (
    GroupGenericResponse,
    GroupCreateRequest,
    GroupCreateData,
    GroupInfoData,
    GroupMembersRequest,
    GroupMembersData,
    GroupAdminRequest,
    GroupRemoveMemberRequest,
    GroupGenericRequest,
    GroupAnnouncementRequest,
    GroupAnnouncementData,
    GroupInviteRequest,
    GroupInviteData,
)
from services.group_service import (
    create_group_service,
    get_group_info_service,
    get_group_members_service,
    manage_group_admin_service,
    remove_group_member_service,
    quit_group_service,
    disband_group_service,
    post_group_announcement_service,
    invite_to_group_service,
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
    req: GroupGenericRequest,
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


@router.delete("/member", summary="移除群员", response_model=GroupGenericResponse[None])
async def remove_group_member(
    req: GroupRemoveMemberRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await remove_group_member_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.post("/quit", summary="成员退出群聊", response_model=GroupGenericResponse[None])
async def quit_group(
    req: GroupGenericRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await quit_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.post("/bomb", summary="解散群聊", response_model=GroupGenericResponse[None])
async def bomb_group(
    req: GroupGenericRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await disband_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.post(
    "/announcement",
    summary="发布群公告",
    response_model=GroupGenericResponse[GroupAnnouncementData],
)
async def post_group_announcement(
    req: GroupAnnouncementRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await post_group_announcement_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)


@router.post(
    "/invite", summary="成员邀请", response_model=GroupGenericResponse[GroupInviteData]
)
async def invite_to_group(
    req: GroupInviteRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await invite_to_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)
