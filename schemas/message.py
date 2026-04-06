from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, TypeVar, Generic


T = TypeVar("T")


class MessageGenericResponse(BaseModel, Generic[T]):
    code: int = 200
    msg: str = "操作成功"
    data: Optional[T] = Field(default=None, description="具体的业务数据")


# 前端发消息给后端时的格式
class SendMessageRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话 ID")
    local_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="客户端生成的本地消息 ID",
    )
    message_content: str = Field(
        ..., min_length=1, max_length=1000, description="消息内容"
    )
    msg_type: str = Field(..., pattern="^(text|image)$", description="消息类型")


class SendMessageData(BaseModel):
    msg_id: int = Field(..., gt=0, description="服务器生成的消息 ID")
    server_time: datetime = Field(..., description="服务器时间戳")
    local_id: str = Field(..., description="客户端发送时的本地消息 ID")


class MessageHistoryRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话 ID")
    start_msg_id: Optional[int] = Field(None, gt=0, description="起始消息 ID")
    limit: int = Field(
        50,
        ge=1,
        le=100,
        description="单次拉取的消息数量限制",
    )


class MessageHistoryItem(BaseModel):
    msg_id: int = Field(..., gt=0, description="全局唯一的消息 ID")
    msg_type: str = Field(..., pattern="^(text|image)$", description="消息类型")
    sender_id: int = Field(..., gt=0, description="发送者的用户 ID")
    msg_content: str = Field(..., description="消息主体内容")
    create_time: datetime = Field(..., description="消息在服务端的落库时间")
    quote_msg_id: Optional[int] = Field(None, gt=0, description="引用的目标消息 ID")
    quote_num: int = Field(0, ge=0, description="该条消息被其他消息引用的次数")
