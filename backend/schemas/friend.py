from pydantic import BaseModel, Field
from typing import Optional, Literal

class FriendApplyRequest(BaseModel):
    target_user_id: int = Field(..., description="目标用户ID")
    message: Optional[str] = Field(None, max_length=200, description="申请附言")

class FriendApplyResponse(BaseModel):
    code: int = 200
    msg: str = "好友申请已发送"

class FriendHandleRequest(BaseModel):
    request_id: int = Field(..., description="好友申请ID")
    action: Literal['accepted', 'rejected'] = Field(..., description="操作类型：accepted（同意）或 rejected（拒绝）")

class FriendHandleResponse(BaseModel):
    code: int = 200
    msg: str = "操作成功"