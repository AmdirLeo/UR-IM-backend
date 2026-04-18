from datetime import datetime
from typing import Optional, TypeVar, Generic, List
from pydantic import BaseModel, Field
from schemas.message import MessageType

T = TypeVar("T")


class ConversationGenericResponse(BaseModel, Generic[T]):
    code: int = 200
    msg: str = "操作成功"
    data: Optional[T] = Field(default=None, description="具体的业务数据")


class ConversationSyncItem(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话 ID")
    type: str = Field(
        ...,
        pattern="^(private|group)$",
        description="会话类型，分为单聊 (private) 和群聊 (group)",
    )
    # --- 核心状态控制 (Sprint 1 升级重点) ---
    status: str = Field(
        ...,
        pattern="^(normal|abnormal)$",
        description="用户在该会话中的状态：normal(正常交流), abnormal(关系解除/只读)"
    )
    unread_count: int = Field(..., ge=0, description="该会话当前的未读消息总数")
    last_ack_msg_id: Optional[int] = Field(
        None, gt=0, description="当前用户在该会话中最后一次确认（已读）的消息 ID")
    last_msg_id: Optional[int] = Field(
        None, gt=0, description="该会话中最新一条消息的全局 ID")
    last_msg_type: MessageType = Field(
        default=MessageType.TEXT, description="最新一条消息的类型")
    last_msg_sender_id: Optional[int] = Field(
        None, description="最新一条消息的发送者 ID")
    last_msg_content: Optional[str] = Field(None, description="最新一条消息的内容")
    last_msg_send_time: Optional[datetime] = Field(
        None, description="最新一条消息的服务端时间")


class SyncAggregatedResponse(BaseModel):
    conversations: List[ConversationSyncItem] = Field(default_factory=list, description="同步的会话列表")
    pending_friend_requests: int = Field(0, ge=0, description="未处理的好友申请数量")
    pending_group_requests: int = Field(0, ge=0, description="未处理的入群申请数量")


class ReadAckRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    msg_id: int = Field(
        ...,
        gt=0,
        description="已读确认的最新消息 ID",
    )


class ConversationMuteRequest(BaseModel):
    conversation_id: int = Field(..., gt=0)
    is_muted: bool = Field(False, description="免打扰状态，默认为 false")


class ConversationPinRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话 ID")
    is_pinned: bool = Field(False, description="置顶状态，默认为 false")


class DirectConversationData(BaseModel):
    conversation_id: int


class DirectConversationResponse(BaseModel):
    code: int
    msg: str
    data: DirectConversationData
