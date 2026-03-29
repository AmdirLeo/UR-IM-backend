from typing import Optional, List, Dict
from db.repositories.friend_repo import (
    db_create_friend_request,
    db_handle_friend_request,
    db_remove_friend,
    db_get_friend_list,
)
from core.exceptions import BusinessException
import asyncpg


async def apply_friend(
    db_session: asyncpg.Connection,
    from_user_id: int,
    target_user_id: int,
    message: Optional[str] = None,
) -> None:
    """
    处理好友申请的业务逻辑。
    """
    # 1. 不能添加自己为好友
    if from_user_id == target_user_id:
        raise BusinessException(status_code=400, detail="不能添加自己为好友")

    # 2. 创建申请记录（调用 repo 层提供的函数）
    success = await db_create_friend_request(
        db_session,
        sender_id=from_user_id,
        receiver_id=target_user_id,
        message=message or "",  # 确保 message 为字符串，防止 None 导致类型问题
    )
    if not success:
        # 理论上外层已做所有检查，此处为兜底
        raise BusinessException(status_code=500, detail="好友申请发送失败")

    # 3. 可选：通过 WebSocket 实时通知目标用户（暂未实现）


async def handle_friend_request(
    db_session: asyncpg.Connection, current_user_id: int, request_id: int, action: str
) -> None:
    """
    处理好友申请的业务逻辑。
    """

    # 调用 repo 层的事务函数执行更新（同意或拒绝）
    success = await db_handle_friend_request(
        db_session,
        request_id=request_id,
        current_user_id=current_user_id,
        action=action,
    )
    if not success:
        # 如果失败（例如申请状态已变更或不存在），抛出异常
        raise BusinessException(status_code=400, detail="处理失败，请稍后重试")


async def remove_friend(
    db_session: asyncpg.Connection, current_user_id: int, friend_user_id: int
) -> None:
    """
    删除好友的业务逻辑。
    - 不能删除自己
    - 检查是否为好友关系（可选，repo 层会返回删除行数，可据此判断）
    """
    # 1. 不能删除自己
    if current_user_id == friend_user_id:
        raise BusinessException(status_code=400, detail="不能删除自己")

    # 2. 调用 repo 层删除好友（同时删除双向记录）
    success = await db_remove_friend(db_session, current_user_id, friend_user_id)

    if not success:
        # 如果删除失败（如不是好友关系），返回友好错误
        raise BusinessException(status_code=404, detail="好友不存在或已删除")


async def get_friend_list(
    db_session: asyncpg.Connection, current_user_id: int
) -> List[Dict]:
    """
    获取当前用户的好友列表。
    """
    # 调用 repo 层已实现的好友列表查询
    friends = await db_get_friend_list(db_session, current_user_id)
    return friends
