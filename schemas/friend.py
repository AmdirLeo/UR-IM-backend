from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from schemas.user import BaseResponse


class FriendGenericResponse(BaseModel):
    code: int = 200
    msg: str = "操作成功"


class FriendApplyRequest(BaseModel):
    target_user_id: int = Field(..., description="目标用户ID")
    message: Optional[str] = Field(None, max_length=200, description="申请附言")


class FriendHandleRequest(BaseModel):
    request_id: int = Field(..., description="好友申请ID")
    action: Literal["accepted", "rejected"] = Field(
        ..., description="操作类型：accepted（同意）或 rejected（拒绝）")


class FriendInfo(BaseModel):
    user_id: int = Field(..., description="好友用户ID")
    username: str = Field(..., description="好友用户名")
    avatar_url: Optional[str] = Field(None, description="好友头像URL")
    tag: Optional[str] = Field(None, description="好友分组标签（如'同学','同事'）")
    be_friend_time: datetime = Field(..., description="成为好友的时间")


class FriendListResponse(BaseResponse):
    data: list[FriendInfo] = Field(default_factory=list)


class TagCreateRequest(BaseModel):
    tag_name: str = Field(..., description="标签名称")


class TagDeleteRequest(BaseModel):
    tag_name: str = Field(..., description="标签名称")


class TagAddFriendRequest(BaseModel):
    tag_name: str = Field(..., description="标签名称")
    friend_ids: list[int] = Field(..., description="好友ID列表")


class TagQueryRequest(BaseModel):
    tag_name: str = Field(..., description="标签名称")


class FriendTagQueryResponse(BaseModel):
    code: int = 200
    msg: str = "查询成功"
    data: list[dict] = Field(..., description="好友信息列表")


class TagRemoveFriendRequest(BaseModel):
    tag_name: str = Field(..., description="标签名称")
    friend_id: int = Field(..., description="好友ID")
