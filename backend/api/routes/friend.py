from fastapi import APIRouter, Depends, Query
from api.dependencies import get_current_user_id
from services.user_service import search_users
from services.friend_service import apply_friend
from schemas.user import SearchUserResponse
from schemas.friend import FriendApplyRequest, FriendApplyResponse
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
        current_user_id=current_user_id,
        limit=size,
        offset=offset,
    )

    # 返回统一格式
    return SearchUserResponse(code=200, msg="查询成功", data=users)


@router.post("/apply", response_model=FriendApplyResponse, summary="发送好友申请")
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
    return FriendApplyResponse(code=200, msg="好友申请已发送")
