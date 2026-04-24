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

# 定义 data 内部的结构


class FriendHandleData(BaseModel):
    conversation_id: Optional[int] = None

# 定义完整的响应结构


class FriendHandleResponse(BaseModel):
    code: int
    msg: str
    data: Optional[FriendHandleData] = None


class FriendInfo(BaseModel):
    user_id: int = Field(..., description="好友用户ID")
    username: str = Field(..., description="好友用户名")
    avatar_url: Optional[str] = Field(None, description="好友头像URL")
    tags: list[str] = Field(
        default_factory=list,
        description="好友分组标签列表（无标签时为空数组）")
    be_friend_time: datetime = Field(..., description="成为好友的时间")
    conversation_id: int = Field(None, description="与该好友的私聊会话ID（若不存在则为null）")


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


class RemoveFriendRequest(BaseModel):
    """删除好友的请求体"""
    friend_user_id: int = Field(..., description="要删除的好友的用户 ID")
    delete_history: bool = Field(False, description="是否同时清空与该好友的聊天记录")


class TagListResponse(BaseResponse):
    """
    获取好友分组/标签列表的响应
    对应 GET /api/friend/tag/list
    """
    data: list[str] = Field(default_factory=list, description="标签名称列表")
