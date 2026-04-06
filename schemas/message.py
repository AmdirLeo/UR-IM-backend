from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


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
    msg_type: str = Field(
        ..., pattern="^(text|image)$", description="消息类型，目前支持 text 或 image"
    )


# 后端返回历史消息给前端时的格式
class MessageResponse(BaseModel):
    id: int = Field(..., description="消息的唯一全局ID")
    sender_id: int = Field(..., description="发送者的用户ID")
    target_id: Optional[int] = Field(None, description="接收者的用户ID")
    content: str = Field(..., description="消息正文")
    msg_type: str = Field(..., description="消息类型，例如: private 或 broadcast")
    created_at: datetime = Field(..., description="消息发送的服务器时间")
