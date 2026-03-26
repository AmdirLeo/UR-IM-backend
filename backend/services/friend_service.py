from typing import Optional
from db.repositories import friend_repo
from core.exceptions import BusinessException
import asyncpg

async def apply_friend(
    db_session: asyncpg.Connection,
    from_user_id: int,
    target_user_id: int,
    message: Optional[str] = None
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
        message=message or ""  # 确保 message 为字符串，防止 None 导致类型问题
    )
    if not success:
        # 理论上外层已做所有检查，此处为兜底
        raise BusinessException(status_code=500, detail="好友申请发送失败")
    
    # 3. 可选：通过 WebSocket 实时通知目标用户（暂未实现）