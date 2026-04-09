from pydantic import BaseModel, Field
from typing import Generic, List, Optional, TypeVar


T = TypeVar("T")

class GroupGenericResponse(BaseModel, Generic[T]):
    code: int = 200
    msg: str = "操作成功"
    data: Optional[T] = Field(default=None, description="业务数据")


class GroupCreateRequest(BaseModel):
    user_ids: List[int] = Field(..., max_length=50, description="被邀请的好友ID列表")
    name: str = Field(..., min_length=1, max_length=100, description="群名称")
    avatar: Optional[str] = Field(None, description="群头像")


class GroupCreateData(BaseModel):
    conversation_id: int = Field(..., description="会话ID")
    name: str = Field(..., description="群名称")
    avatar: Optional[str] = Field(None, description="群头像")
