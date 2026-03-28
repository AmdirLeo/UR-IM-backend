from pydantic import BaseModel, Field
from typing import Optional

class FriendApplyRequest(BaseModel):
    target_user_id: int = Field(..., description="目标用户ID")
    message: Optional[str] = Field(None, max_length=200, description="申请附言")

class FriendApplyResponse(BaseModel):
    code: int = 200
    msg: str = "好友申请已发送"