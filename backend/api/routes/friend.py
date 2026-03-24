from fastapi import APIRouter, Depends, Query
from typing import List
from api.dependencies import get_current_user_id
from services.user_service import search_users
from schemas.user import SearchUserResponse
from db.database import get_db  # 假设你的数据库连接依赖注入函数

router = APIRouter(prefix="/friend", tags=["好友"])


@router.get("/search", response_model=SearchUserResponse, summary="搜索用户")
async def search_user(
    keyword: str = Query(..., min_length=1, max_length=50, description="搜索关键词"),
    # 可选分页参数
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user_id: int = Depends(get_current_user_id),
    db_session=Depends(get_db),  # 数据库会话依赖
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
