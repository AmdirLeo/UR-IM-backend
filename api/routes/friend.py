from fastapi import APIRouter, Depends, Query, Path
from api.dependencies import get_current_user_id
from services.user_service import search_users
from services.friend_service import (
    apply_friend,
    handle_friend_request,
    remove_friend,
    get_friend_list,
)
from schemas.user import SearchUserResponse, BaseResponse
from schemas.friend import (
    FriendApplyRequest,
    FriendHandleRequest,
    FriendListResponse,
    FriendInfo,
)
from db.database import get_db_conn  # 假设你的数据库连接依赖注入函数
from core.exceptions import BusinessException

router = APIRouter(prefix="/friend", tags=["好友"])


@router.get("/search", response_model=SearchUserResponse, summary="搜索用户")
async def search_user(
    keyword: str = Query(..., min_length=1, max_length=50, description="搜索关键词"),
    # 可选分页参数
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user_id: int = Depends(get_current_user_id),
    db_session=Depends(get_db_conn),  # 数据库会话依赖
):
    """
    根据用户名模糊搜索其他用户，返回用户ID、用户名、头像URL。
    自动排除当前登录用户。
    """
    offset = (page - 1) * size

    users = await search_users(
        db_session=db_session,
        keyword=keyword,
        limit=size,
        offset=offset,
    )

    # 返回统一格式
    return SearchUserResponse(code=200, msg="查询成功", data=users)


@router.post("/apply", response_model=BaseResponse, summary="发送好友申请")
async def send_friend_apply(
    request: FriendApplyRequest,
    current_user_id: int = Depends(get_current_user_id),
    db_session=Depends(get_db_conn),
):
    """
    向目标用户发送好友申请。
    - **target_user_id**: 目标用户ID
    - **message**: 可选附言，最多200字符
    """
    await apply_friend(
        db_session=db_session,
        from_user_id=current_user_id,
        target_user_id=request.target_user_id,
        message=request.message,
    )
    return BaseResponse(code=200, msg="好友申请已发送")


@router.put("/handle", response_model=BaseResponse, summary="处理好友申请")
async def friend_handle(
    request: FriendHandleRequest,
    current_user_id: int = Depends(get_current_user_id),
    db_session=Depends(get_db_conn),
):
    """
    同意或拒绝好友申请。
    - **request_id**: 申请ID
    - **action**: `accepted`（同意）或 `rejected`（拒绝）
    """
    await handle_friend_request(
        db_session=db_session,
        current_user_id=current_user_id,
        request_id=request.request_id,
        action=request.action,
    )
    msg = "已同意好友申请" if request.action == "accepted" else "已拒绝好友申请"
    return BaseResponse(code=200, msg=msg)


@router.delete(
    "/remove/{friend_user_id}", response_model=BaseResponse, summary="删除好友"
)
async def delete_friend(
    friend_user_id: int = Path(..., description="要删除的好友用户ID"),
    current_user_id: int = Depends(get_current_user_id),
    db_session=Depends(get_db_conn),
):
    """
    删除好友，同时解除双向关系。
    """
    await remove_friend(
        db_session=db_session,
        current_user_id=current_user_id,
        friend_user_id=friend_user_id,
    )
    return BaseResponse(code=200, msg="好友删除成功")


@router.get("", response_model=FriendListResponse, summary="获取好友列表")
async def list_friends(
    current_user_id: int = Depends(get_current_user_id), db_session=Depends(get_db_conn)
):
    """
    获取当前用户的所有好友列表。
    包含好友基本信息、分组标签、成为好友的时间。
    """
    friends = await get_friend_list(db_session, current_user_id)

    # 将数据库返回的字典转换为 Pydantic 模型
    data = [FriendInfo(**f) for f in friends]
    return FriendListResponse(code=200, msg="获取成功", data=data)
