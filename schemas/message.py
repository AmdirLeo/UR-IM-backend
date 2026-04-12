from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, TypeVar, Generic, Dict, Any
from enum import Enum

T = TypeVar("T")


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    CARD = "card"     # 互动卡片（如好友申请）
    NOTIFY = "notify"  # 系统指令（前端静默处理或显示小灰条）


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
        ...,
        min_length=1,
        max_length=5000,
        description="消息内容/摘要展示文案"
    )
    msg_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    # 💡 核心新增：用来装 JSON 参数的万能口袋
    extra_data: Optional[Dict[str, Any]] = Field(default=None, description="附加结构化数据")

    quote_message_id: Optional[int] = Field(None, description="当前信息所引用的信息的id")


class SendMessageData(BaseModel):
    msg_id: int = Field(..., gt=0, description="服务器生成的消息 ID")
    server_time: datetime = Field(..., description="服务器时间戳")
    local_id: str = Field(..., description="客户端发送时的本地消息 ID")


class MessageHistoryRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    start_msg_id: Optional[int] = Field(None, gt=0, description="起始消息 ID")
    limit: int = Field(
        50,
        ge=1,
        le=100,
        description="单次拉取的消息数量限制",
    )


class MessageHistoryItem(BaseModel):
    msg_id: int = Field(..., gt=0, description="全局唯一的消息 ID")
    msg_type: MessageType = Field(default=MessageType.TEXT, description="消息类型")
    sender_id: int = Field(..., gt=0, description="发送者的用户 ID")
    msg_content: str = Field(..., description="消息主体内容")
    create_time: datetime = Field(..., description="消息在服务端的落库时间")
    quote_msg_id: Optional[int] = Field(None, gt=0, description="引用的目标消息 ID")
    quote_num: int = Field(0, ge=0, description="该条消息被其他消息引用的次数")


class MessageSearchRequest(BaseModel):
    conversation_id: Optional[int] = Field(None, gt=0)
    user_id: Optional[int] = Field(None, gt=0, description="发送者 ID")
    start_time: Optional[datetime] = Field(None, description="起始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    keyword: Optional[str] = Field(None, description="搜索关键词")
    limit: int = Field(20, ge=1, le=100, description="限制返回数量")
    offset: Optional[int] = Field(None, ge=0, description="偏移量/游标消息ID")


class MessageSearchItem(BaseModel):
    user_id: int = Field(..., description="用户 ID")
    conversation_id: int = Field(...)
    msg_id: int = Field(..., description="消息 ID")
    msg: str = Field(..., description="消息内容")
    time: datetime = Field(..., description="发送时间")


class DeleteMessageRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    message_id: int = Field(..., gt=0, description="消息 ID")
