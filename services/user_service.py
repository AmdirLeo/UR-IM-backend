from db.repositories.user_repo import db_search_users  # 假设函数名是这个
from schemas.user import UserSearchResult
from typing import List

async def search_users(
    db_session,
    keyword: str,
    limit: int = 20,
    offset: int = 0
) -> List[UserSearchResult]:
    # 直接调用 repository 层已实现的函数
    users = await db_search_users(
        db_session,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    # 如果 repository 返回的是字典列表，直接转换
    return [
        UserSearchResult(
            user_id=u["user_id"],
            username=u["username"],
            avatar_url=u.get("avatar_url")
        )
        for u in users
    ]