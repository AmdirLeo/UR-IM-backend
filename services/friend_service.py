from typing import Optional
from db.repositories import friend_repo
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
    success = await friend_repo.db_create_friend_request(
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
    # # 查询申请详情，获取 receiver_id，检查当前用户是否有权处理（只有接收者可以
    # query = """
    #     SELECT receiver_id, status
    #     FROM friend_request
    #     WHERE request_id = $1
    # """
    # row = await db_session.fetchrow(query, request_id)
    # if not row:
    #     raise BusinessException(status_code=404, detail="好友申请不存在")
    # receiver_id = row["receiver_id"]
    # if current_user_id != receiver_id:
    #     raise BusinessException(status_code=403, detail="无权处理此申请")

    # 调用 repo 层的事务函数执行更新（同意或拒绝）
    success = await friend_repo.db_handle_friend_request(
        db_session, request_id=request_id, action=action
    )
    if not success:
        # 如果失败（例如申请状态已变更或不存在），抛出异常
        raise BusinessException(status_code=400, detail="处理失败，请稍后重试")

    # 成功无返回（由路由层返回统一响应）
