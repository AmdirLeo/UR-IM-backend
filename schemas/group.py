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


class GroupGenericRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")


class AnnouncementItem(BaseModel):
    announcement_id: int = Field(..., description="公告ID")
    content: str = Field(..., description="公告内容")
    create_time: int = Field(..., description="创建时间(时间戳)")
    sender_name: str = Field(..., description="发送者名称")


class GroupMemberItem(BaseModel):
    user_id: int = Field(..., description="成员ID")
    user_name: str = Field(..., description="成员名称")
    avatar_url: Optional[str] = Field(None, description="头像地址")
    role: str = Field(..., description="成员角色(如 owner, admin, member)")


class GroupInfoData(BaseModel):
    conversation_id: int = Field(..., description="会话ID")
    conversation_name: str = Field(..., description="群名称")
    conversation_avatar: Optional[str] = Field(None, description="群头像")
    member_count: int = Field(..., description="总人数")
    owner_id: int = Field(..., description="群主ID")
    my_role: str = Field(..., description="当前用户角色")
    latest_announcement: Optional[AnnouncementItem] = Field(
        None, description="最新公告"
    )
    top_members: List[GroupMemberItem] = Field(..., description="前9成员")


class GroupMembersRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    page: int = Field(1, ge=1, description="当前页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class GroupMembersData(BaseModel):
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    list: List[GroupMemberItem] = Field(..., description="成员列表")


class GroupAdminRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    user_id: int = Field(..., gt=0, description="被操作的用户ID")
    role: str = Field(...,
                      pattern="^(admin|member|owner)$",
                      description="设置的角色")


class GroupRemoveMemberRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    user_id: int = Field(..., gt=0, description="被操作的用户ID")


class GroupAnnouncementRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    msg: str = Field(..., min_length=1, description="公告内容")


class GroupAnnouncementData(BaseModel):
    time: str = Field(..., description="发布时间")
    announcement_id: int = Field(..., description="公告ID")


class GroupInviteRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    user_id: int = Field(..., gt=0, description="被邀请的好友ID")


class GroupInviteData(BaseModel):
    apply_id: int = Field(..., description="邀请记录ID")


class GroupInviteReviewRequest(BaseModel):
    apply_id: int = Field(..., gt=0, description="邀请记录ID")
    status: str = Field(...,
                        pattern="^(APPROVED|IGNORED)$",
                        description="审核操作")

class GroupAnnouncementListData(BaseModel):
    items: List[AnnouncementItem] = Field(..., description="公告列表")
    total: int = Field(..., description="符合条件的总记录数")
    page: int = Field(..., ge=1, description="当前页码")
    page_size: int = Field(..., ge=1, le=100, description="每页条数")

class GroupAnnouncementsRequest(BaseModel):
    conversation_id: int = Field(..., gt=0, description="会话ID")
    page: int = Field(1, ge=1, description="当前页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")