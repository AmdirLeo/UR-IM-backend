from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query, Path

from api.dependencies import get_current_user_id
from db.database import get_db_conn
from services.user_service import search_users
from services import friend_service
from schemas.user import SearchUserResponse
from schemas.friend import (
    FriendGenericResponse,
    FriendApplyRequest,
    FriendHandleRequest,
    FriendListResponse,
    FriendInfo,
    TagCreateRequest,
    TagDeleteRequest,
    TagAddFriendRequest,
    TagQueryRequest,
    FriendTagQueryResponse,
    TagRemoveFriendRequest,
)

router = APIRouter(prefix="/friend", tags=["好友"])

# ==========================================
# 定义 Annotated 依赖别名 (最优雅的做法)
# ==========================================
CurrentUserId = Annotated[int, Depends(get_current_user_id)]
DBSession = Annotated[Any, Depends(get_db_conn)]


@router.get("/search", response_model=SearchUserResponse, summary="搜索用户")
async def search_user(
    # ⚠️ 注意：没有默认值的 Annotated 依赖必须放在最前面
    current_user_id: CurrentUserId,
    db_session: DBSession,
    # 下面是有默认值的参数
    keyword: Annotated[str, Query(min_length=1, max_length=50, description="搜索关键词")],
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 20,
):
    """
    根据用户名模糊搜索其他用户，返回用户ID、用户名、头像URL。
    自动排除当前登录用户。
    """
    users = await search_users(
        db_session=db_session,
        keyword=keyword,
        page=page,
        page_size=size,
    )
    return SearchUserResponse(code=200, msg="查询成功", data=users)


@router.post("/apply", response_model=FriendGenericResponse, summary="发送好友申请")
async def send_friend_apply(
    request: FriendApplyRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    """
    向目标用户发送好友申请。
    - **target_user_id**: 目标用户ID
    - **message**: 可选附言，最多200字符
    """
    await friend_service.apply_friend(
        db_session=db_session,
        from_user_id=current_user_id,
        target_user_id=request.target_user_id,
        message=request.message,
    )
    return FriendGenericResponse(code=200, msg="好友申请已发送")


@router.put("/handle", response_model=FriendGenericResponse, summary="处理好友申请")
async def friend_handle(
    request: FriendHandleRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    """
    同意或拒绝好友申请。
    - **request_id**: 申请ID
    - **action**: `accepted`（同意）或 `rejected`（拒绝）
    """
    await friend_service.handle_friend_request(
        db_session=db_session,
        current_user_id=current_user_id,
        request_id=request.request_id,
        action=request.action,
    )
    msg = "已同意好友申请" if request.action == "accepted" else "已拒绝好友申请"
    return FriendGenericResponse(code=200, msg=msg)


@router.delete("/remove/{friend_user_id}", response_model=FriendGenericResponse, summary="删除好友")
async def delete_friend(
    # ⚠️ 同样，依赖前置，Path 参数后置
    current_user_id: CurrentUserId,
    db_session: DBSession,
    friend_user_id: Annotated[int, Path(description="要删除的好友用户ID")],
):
    """
    删除好友，同时解除双向关系。
    """
    await friend_service.remove_friend(
        db_session=db_session,
        current_user_id=current_user_id,
        friend_user_id=friend_user_id,
    )
    return FriendGenericResponse(code=200, msg="好友删除成功")


@router.get("", response_model=FriendListResponse, summary="获取好友列表")
async def list_friends(
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    """
    获取当前用户的所有好友列表。
    包含好友基本信息、分组标签、成为好友的时间。
    """
    friends = await friend_service.get_friend_list(db_session, current_user_id)
    data = [FriendInfo(**f) for f in friends]
    return FriendListResponse(code=200, msg="获取成功", data=data)


@router.post("/tag/new", response_model=FriendGenericResponse, summary="新建好友标签")
async def create_friend_tag(
    request: TagCreateRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    await friend_service.create_friend_tag(
        db_session=db_session,
        user_id=current_user_id,
        tag_name=request.tag_name,
    )
    return FriendGenericResponse(code=200, msg="新建标签成功")


@router.post("/tag/delete", response_model=FriendGenericResponse, summary="删除好友标签")
async def delete_friend_tag(
    request: TagDeleteRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    await friend_service.delete_friend_tag(
        db_session=db_session,
        user_id=current_user_id,
        tag_name=request.tag_name,
    )
    return FriendGenericResponse(code=200, msg="删除标签成功")


@router.post("/tag/add", response_model=FriendGenericResponse, summary="将好友加入标签")
async def add_friends_to_tag(
    request: TagAddFriendRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    await friend_service.add_friends_to_tag(
        db_session=db_session,
        user_id=current_user_id,
        tag_name=request.tag_name,
        friend_ids=request.friend_ids,
    )
    return FriendGenericResponse(code=200, msg="添加好友到标签成功")


@router.post("/tag/query", response_model=FriendTagQueryResponse, summary="查询标签内好友")
async def query_friends_by_tag(
    request: TagQueryRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    friends = await friend_service.get_friends_by_tag(
        db_session=db_session,
        user_id=current_user_id,
        tag_name=request.tag_name,
    )
    return FriendTagQueryResponse(code=200, msg="查询成功", data=friends)


@router.post("/tag/remove", response_model=FriendGenericResponse, summary="删除tag中的好友")
async def remove_friend_from_tag(
    request: TagRemoveFriendRequest,
    current_user_id: CurrentUserId,
    db_session: DBSession,
):
    await friend_service.remove_friend_from_tag(
        db_session=db_session,
        user_id=current_user_id,
        friend_user_id=request.friend_id,
        tag_name=request.tag_name,
    )
    return FriendGenericResponse(code=200, msg="移出好友成功")
