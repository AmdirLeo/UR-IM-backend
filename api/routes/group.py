from fastapi import APIRouter
from api.dependencies import CurrentUserId, DBConnection
from fastapi import File, Form, UploadFile
from typing import List, Optional
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
    GroupInviteReviewRequest,
    GroupAnnouncementListData,
    GroupAnnouncementsRequest,
    GroupInfo,
    GroupListResponse,
    GroupUpdateNameRequest,
    GroupBatchInviteRequest,
    GroupBatchInviteData,
    GroupPortraitResponse,
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
    review_group_invite_service,
    get_pending_group_invites_as_cards,
    get_group_announcements_service,
    get_group_list,
    update_group_name_service,
    invite_to_group_batch_service,
    edit_group_portrait_service,
)


router = APIRouter()


@router.post("/create", summary="创建群聊",
             response_model=GroupGenericResponse[GroupCreateData])
async def create_group(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
    name: str = Form(..., min_length=1, max_length=50, description="群名称"),
    user_ids: List[int] = Form(..., description="被邀请的好友ID列表"),
    file: Optional[UploadFile] = File(None, description="可选的群头像文件"),
):
    req = GroupCreateRequest(user_ids=user_ids, name=name, avatar=None)
    data = await create_group_service(db_session, current_user_id, req, file)
    return GroupGenericResponse(data=data)


@router.post("/info", summary="获取群聊信息",
             response_model=GroupGenericResponse[GroupInfoData])
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


@router.put("/admin", summary="群权限管理",
            response_model=GroupGenericResponse[None])
async def manage_group_admin(
    req: GroupAdminRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await manage_group_admin_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.delete("/member", summary="移除群员",
               response_model=GroupGenericResponse[None])
async def remove_group_member(
    req: GroupRemoveMemberRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await remove_group_member_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.post("/quit", summary="成员退出群聊",
             response_model=GroupGenericResponse[None])
async def quit_group(
    req: GroupGenericRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await quit_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.post("/bomb", summary="解散群聊",
             response_model=GroupGenericResponse[None])
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


@router.post("/invite", summary="成员邀请",
             response_model=GroupGenericResponse[GroupInviteData])
async def invite_to_group(
    req: GroupInviteRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await invite_to_group_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)


@router.post("/invite/batch", summary="批量邀请成员",
             response_model=GroupGenericResponse[GroupBatchInviteData])
async def invite_to_group_batch(
    req: GroupBatchInviteRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await invite_to_group_batch_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=data)


@router.post(
    "/invite/review", summary="审核邀请", response_model=GroupGenericResponse[None]
)
async def review_group_invite(
    req: GroupInviteReviewRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    await review_group_invite_service(db_session, current_user_id, req)
    return GroupGenericResponse(data=None)


@router.get("/invites/pending")
async def list_pending_group_invites(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    """
    获取当前用户未处理的入群申请列表（卡片格式）
    """
    cards = await get_pending_group_invites_as_cards(db_session, current_user_id)
    return {
        "code": 200,
        "data": cards,
        "total": len(cards)
    }


@router.post(
    "/announcements",  # 注意路径用了复数，避免与已有的 POST /announcement 冲突
    summary="获取群公告列表",
    response_model=GroupGenericResponse[GroupAnnouncementListData],
)
async def get_group_announcements(  # 函数名也区分一下
    req: GroupAnnouncementsRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    data = await get_group_announcements_service(
        db_session=db_session,
        current_user_id=current_user_id,
        conversation_id=req.conversation_id,
        page=req.page,
        page_size=req.page_size,
    )
    return GroupGenericResponse(data=data)


@router.get("", response_model=GroupListResponse, summary="获取群聊列表")
async def list_groups(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    """
    获取当前用户加入的所有群聊列表。
    包含群聊基本信息、用户在群内的角色以及加入时间。
    """
    groups = await get_group_list(db_session, current_user_id)
    data = [GroupInfo(**g) for g in groups]

    return GroupListResponse(code=200, msg="获取成功", data=data)


@router.put("/name", summary="修改群聊名称")
async def update_group_name(
    request: GroupUpdateNameRequest,
    current_user_id: CurrentUserId,
    db_session: DBConnection,
):
    res = await update_group_name_service(
        db_session=db_session,
        current_user_id=current_user_id,
        req=request,
    )
    # 假设你有类似 GenericResponse 的统一返回模型
    return {"code": 200, "msg": "群名称修改成功", "data": res}


@router.put("/edit/portrait", summary="修改群头像",
            response_model=GroupPortraitResponse)  # 假设你复用了个人头像的返回结构
async def edit_group_portrait(
    current_user_id: CurrentUserId,
    db_session: DBConnection,
    conversation_id: int = Form(..., description="群聊ID"),
    file: UploadFile = File(..., description="群头像图片文件"),
):
    """
    修改群头像接口。
    注意：包含文件上传，必须使用 Form 来接收其他普通参数。
    """
    # 这里的 service 需要包含 current_user_id，因为业务层需要鉴权（判断是不是群主/管理员）
    data = await edit_group_portrait_service(
        db_session=db_session,
        current_user_id=current_user_id,
        group_id=conversation_id,
        file=file
    )
    return data
