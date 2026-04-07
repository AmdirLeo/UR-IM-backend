import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from db.repositories.group_repo import (
    db_get_conversation_list,
    db_create_group,
    db_get_group_info,
    db_quit_group,
    db_disband_group,
    db_remove_group_member,
    db_manage_group_role,
    db_post_group_announcement,
    db_invite_to_group,
    db_review_group_invite,
    QUERY_GET_MEMBER_ROLE,
    QUERY_GET_CONVERSATION_LIST,
)
from core.exceptions import GroupException, GroupErrors
from datetime import datetime, timezone
import json


# ==========================================
# Custom Mock Classes
# ==========================================

class AsyncContextManagerMock(AsyncMock):
    """异步上下文管理器 Mock"""
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class TransactionMock:
    """事务 Mock，支持异步上下文管理器"""
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


# ==========================================
# Fixtures
# ==========================================

@pytest.fixture
def mock_conn():
    """创建一个模拟的 asyncpg.Connection"""
    conn = MagicMock()
    # 为 transaction 配置返回事务对象
    conn.transaction = MagicMock(return_value=TransactionMock())
    # 为其他异步方法配置
    conn.fetch = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()
    return conn


# ==========================================
# Test: db_get_conversation_list
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_get_conversation_list_success(mock_conn):
    """测试成功获取群聊列表"""
    user_id = 1
    mock_rows = [
        {
            'conversation_id': 101,
            'conversation_name': '测试群1',
            'last_msg_time': datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc),
            'msg_body': json.dumps({'text': '最后一条消息'})
        },
        {
            'conversation_id': 102,
            'conversation_name': '测试群2',
            'last_msg_time': datetime(2026, 4, 7, 9, 0, 0, tzinfo=timezone.utc),
            'msg_body': json.dumps({'text': '前一条消息'})
        }
    ]
    mock_conn.fetch.return_value = mock_rows

    result = await db_get_conversation_list(mock_conn, user_id)

    assert len(result) == 2
    assert result[0]['conversation_id'] == 101
    assert result[0]['conversation_name'] == '测试群1'
    assert result[0]['last_msg_preview'] == '最后一条消息'
    assert result[1]['last_msg_preview'] == '前一条消息'
    mock_conn.fetch.assert_called_once_with(QUERY_GET_CONVERSATION_LIST, user_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_conversation_list_with_none_msg_body(mock_conn):
    """测试获取列表时 msg_body 为 None"""
    user_id = 1
    mock_rows = [
        {
            'conversation_id': 101,
            'conversation_name': '新群',
            'last_msg_time': None,
            'msg_body': None
        }
    ]
    mock_conn.fetch.return_value = mock_rows

    result = await db_get_conversation_list(mock_conn, user_id)

    assert len(result) == 1
    assert result[0]['last_msg_preview'] == ""
    assert result[0]['last_msg_time'] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_conversation_list_with_invalid_json(mock_conn):
    """测试获取列表时 msg_body 包含无效的 JSON"""
    user_id = 1
    mock_rows = [
        {
            'conversation_id': 101,
            'conversation_name': '测试群',
            'last_msg_time': datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc),
            'msg_body': 'invalid json'
        }
    ]
    mock_conn.fetch.return_value = mock_rows

    result = await db_get_conversation_list(mock_conn, user_id)

    assert result[0]['last_msg_preview'] == "[解析错误]"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_conversation_list_with_no_text_field(mock_conn):
    """测试获取列表时 msg_body 没有 text 字段"""
    user_id = 1
    mock_rows = [
        {
            'conversation_id': 101,
            'conversation_name': '测试群',
            'last_msg_time': datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc),
            'msg_body': json.dumps({'other_field': 'value'})
        }
    ]
    mock_conn.fetch.return_value = mock_rows

    result = await db_get_conversation_list(mock_conn, user_id)

    assert result[0]['last_msg_preview'] == '[非文本消息]'


@pytest.mark.asyncio(loop_scope="session")
async def test_get_conversation_list_empty(mock_conn):
    """测试获取空的群聊列表"""
    user_id = 1
    mock_conn.fetch.return_value = []

    result = await db_get_conversation_list(mock_conn, user_id)

    assert result == []


# ==========================================
# Test: db_create_group
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_create_group_success(mock_conn):
    """测试成功创建群聊"""
    creator_id = 1
    member_ids = [2, 3, 4]
    group_name = "新群聊"
    conv_id = 101

    mock_conn.fetchval.return_value = conv_id

    result = await db_create_group(mock_conn, creator_id, member_ids, group_name)

    assert result == conv_id
    # 验证事务开启
    mock_conn.transaction.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_create_group_with_creator_in_members(mock_conn):
    """测试创建群聊时创建者也在成员列表中（应该被去重）"""
    creator_id = 1
    member_ids = [1, 2, 3]  # 包含创建者本身
    group_name = "测试群"
    conv_id = 101

    mock_conn.fetchval.return_value = conv_id

    result = await db_create_group(mock_conn, creator_id, member_ids, group_name)

    assert result == conv_id
    mock_conn.transaction.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_create_group_with_duplicate_members(mock_conn):
    """测试创建群聊时成员列表有重复（应该被去重）"""
    creator_id = 1
    member_ids = [2, 3, 2, 4, 3]  # 有重复
    group_name = "测试群"
    conv_id = 101

    mock_conn.fetchval.return_value = conv_id

    result = await db_create_group(mock_conn, creator_id, member_ids, group_name)

    assert result == conv_id


@pytest.mark.asyncio(loop_scope="session")
async def test_create_group_with_default_name(mock_conn):
    """测试创建群聊使用默认名称"""
    creator_id = 1
    member_ids = [2, 3]
    conv_id = 101

    mock_conn.fetchval.return_value = conv_id

    result = await db_create_group(mock_conn, creator_id, member_ids)

    assert result == conv_id


@pytest.mark.asyncio(loop_scope="session")
async def test_create_group_empty_members(mock_conn):
    """测试创建只有创建者的群聊（无其他成员）"""
    creator_id = 1
    member_ids = []
    group_name = "独自群聊"
    conv_id = 101

    mock_conn.fetchval.return_value = conv_id

    result = await db_create_group(mock_conn, creator_id, member_ids, group_name)

    assert result == conv_id


# ==========================================
# Test: db_get_group_info
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_get_group_info_success(mock_conn):
    """测试成功获取群信息"""
    user_id = 1
    conversation_id = 101

    mock_conn.fetchval.return_value = 'member'
    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'conversation_name': '测试群',
        'create_time': datetime(2026, 4, 1, tzinfo=timezone.utc)
    }
    mock_conn.fetch.side_effect = [
        [  # members
            {
                'member_user_id': 1,
                'username': 'user1',
                'avatar_url': 'url1',
                'role': 'owner',
                'join_time': datetime(2026, 4, 1, tzinfo=timezone.utc)
            }
        ],
        [  # announcements
            {
                'announcement_id': 1,
                'content': '公告内容',
                'is_pinned': True,
                'create_time': datetime(2026, 4, 7, tzinfo=timezone.utc),
                'sender_name': 'user1'
            }
        ]
    ]

    result = await db_get_group_info(mock_conn, user_id, conversation_id)

    assert result['conversation_id'] == 101
    assert result['conversation_name'] == '测试群'
    assert len(result['members']) == 1
    assert result['members'][0]['username'] == 'user1'
    assert len(result['announcements']) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_group_info_not_in_group(mock_conn):
    """测试获取群信息时用户不在群里"""
    user_id = 1
    conversation_id = 101

    mock_conn.fetchval.return_value = None

    with pytest.raises(GroupException) as exc_info:
        await db_get_group_info(mock_conn, user_id, conversation_id)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_get_group_info_admin_role(mock_conn):
    """测试获取群信息时用户为管理员"""
    user_id = 2
    conversation_id = 101

    mock_conn.fetchval.return_value = 'admin'
    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'conversation_name': '测试群',
        'create_time': datetime(2026, 4, 1, tzinfo=timezone.utc)
    }
    mock_conn.fetch.side_effect = [[], []]

    result = await db_get_group_info(mock_conn, user_id, conversation_id)

    assert result['conversation_id'] == 101


# ==========================================
# Test: db_quit_group
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_quit_group_success(mock_conn):
    """测试成功退出群聊"""
    user_id = 2
    conversation_id = 101

    mock_conn.fetchval.return_value = 'member'

    await db_quit_group(mock_conn, user_id, conversation_id)

    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0]
    assert 'DELETE FROM conversation_member' in call_args[0]
    assert call_args[1] == conversation_id
    assert call_args[2] == user_id


@pytest.mark.asyncio(loop_scope="session")
async def test_quit_group_not_in_group(mock_conn):
    """测试退出未加入的群聊"""
    user_id = 1
    conversation_id = 101

    mock_conn.fetchval.return_value = None

    with pytest.raises(GroupException) as exc_info:
        await db_quit_group(mock_conn, user_id, conversation_id)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_quit_group_as_owner(mock_conn):
    """测试群主尝试退群"""
    user_id = 1
    conversation_id = 101

    mock_conn.fetchval.return_value = 'owner'

    with pytest.raises(GroupException) as exc_info:
        await db_quit_group(mock_conn, user_id, conversation_id)

    assert exc_info.value.error_code == GroupErrors.OwnerCannotQuit


@pytest.mark.asyncio(loop_scope="session")
async def test_quit_group_as_admin(mock_conn):
    """测试管理员成功退群"""
    user_id = 2
    conversation_id = 101

    mock_conn.fetchval.return_value = 'admin'

    await db_quit_group(mock_conn, user_id, conversation_id)

    mock_conn.execute.assert_called_once()


# ==========================================
# Test: db_disband_group
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_disband_group_success(mock_conn):
    """测试群主成功解散群聊"""
    user_id = 1
    conversation_id = 101

    mock_conn.fetchval.return_value = 'owner'

    await db_disband_group(mock_conn, user_id, conversation_id)

    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0]
    assert 'DELETE FROM conversation' in call_args[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_disband_group_not_owner(mock_conn):
    """测试非群主尝试解散群聊"""
    user_id = 2
    conversation_id = 101

    mock_conn.fetchval.return_value = 'member'

    with pytest.raises(GroupException) as exc_info:
        await db_disband_group(mock_conn, user_id, conversation_id)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_disband_group_admin(mock_conn):
    """测试管理员尝试解散群聊"""
    user_id = 2
    conversation_id = 101

    mock_conn.fetchval.return_value = 'admin'

    with pytest.raises(GroupException) as exc_info:
        await db_disband_group(mock_conn, user_id, conversation_id)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


# ==========================================
# Test: db_remove_group_member
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_success_owner_kick_member(mock_conn):
    """测试群主成功移除普通成员"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3

    mock_conn.fetch.return_value = [
        {'member_user_id': 1, 'role': 'owner'},
        {'member_user_id': 3, 'role': 'member'}
    ]

    await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_cannot_kick_self(mock_conn):
    """测试不能踢自己"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 1

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_operator_not_in_group(mock_conn):
    """测试操作者不在群里"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3

    mock_conn.fetch.return_value = [
        {'member_user_id': 3, 'role': 'member'}
    ]

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_target_not_in_group(mock_conn):
    """测试目标用户不在群里"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3

    mock_conn.fetch.return_value = [
        {'member_user_id': 1, 'role': 'owner'}
    ]

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_regular_member_cannot_kick(mock_conn):
    """测试普通成员无法踢人"""
    operator_id = 3
    conversation_id = 101
    target_user_id = 4

    mock_conn.fetch.return_value = [
        {'member_user_id': 3, 'role': 'member'},
        {'member_user_id': 4, 'role': 'member'}
    ]

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_admin_kick_member(mock_conn):
    """测试管理员成功踢普通成员"""
    operator_id = 2
    conversation_id = 101
    target_user_id = 4

    mock_conn.fetch.return_value = [
        {'member_user_id': 2, 'role': 'admin'},
        {'member_user_id': 4, 'role': 'member'}
    ]

    await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_admin_cannot_kick_owner(mock_conn):
    """测试管理员不能踢群主"""
    operator_id = 2
    conversation_id = 101
    target_user_id = 1

    mock_conn.fetch.return_value = [
        {'member_user_id': 2, 'role': 'admin'},
        {'member_user_id': 1, 'role': 'owner'}
    ]

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.CannotKickHigherRole


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_admin_cannot_kick_admin(mock_conn):
    """测试管理员不能踢另一个管理员"""
    operator_id = 2
    conversation_id = 101
    target_user_id = 5

    mock_conn.fetch.return_value = [
        {'member_user_id': 2, 'role': 'admin'},
        {'member_user_id': 5, 'role': 'admin'}
    ]

    with pytest.raises(GroupException) as exc_info:
        await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    assert exc_info.value.error_code == GroupErrors.CannotKickHigherRole


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_member_owner_can_kick_anyone(mock_conn):
    """测试群主可以踢任何人"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 2

    mock_conn.fetch.return_value = [
        {'member_user_id': 1, 'role': 'owner'},
        {'member_user_id': 2, 'role': 'admin'}
    ]

    await db_remove_group_member(mock_conn, operator_id, conversation_id, target_user_id)

    mock_conn.execute.assert_called_once()


# ==========================================
# Test: db_manage_group_role
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_promote_to_admin(mock_conn):
    """测试群主提拔普通成员为管理员"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3
    new_role = 'admin'

    mock_conn.fetchval.side_effect = ['owner', 'member']

    await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    mock_conn.transaction.assert_called_once()
    mock_conn.execute.assert_called()


@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_demote_admin(mock_conn):
    """测试群主撤销管理员权限"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 2
    new_role = 'member'

    mock_conn.fetchval.side_effect = ['owner', 'admin']

    await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    mock_conn.transaction.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_transfer_owner(mock_conn):
    """测试群主转让群主权限"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3
    new_role = 'owner'

    mock_conn.fetchval.side_effect = ['owner', 'member']

    await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    mock_conn.transaction.assert_called_once()
    # 验证调用了两个 UPDATE：降级自己 + 提升对方
    update_calls = mock_conn.execute.call_args_list
    assert len(update_calls) >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_invalid_role(mock_conn):
    """测试无效的角色类型"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3
    new_role = 'invalid_role'

    with pytest.raises(GroupException) as exc_info:
        await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    assert exc_info.value.error_code == GroupErrors.InvalidRole


@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_not_owner(mock_conn):
    """测试非群主尝试管理权限"""
    operator_id = 2
    conversation_id = 101
    target_user_id = 3
    new_role = 'admin'

    mock_conn.fetchval.return_value = 'admin'

    with pytest.raises(GroupException) as exc_info:
        await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_manage_role_target_not_in_group(mock_conn):
    """测试目标用户不在群里"""
    operator_id = 1
    conversation_id = 101
    target_user_id = 3
    new_role = 'admin'

    mock_conn.fetchval.side_effect = ['owner', None]

    with pytest.raises(GroupException) as exc_info:
        await db_manage_group_role(mock_conn, operator_id, conversation_id, target_user_id, new_role)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


# ==========================================
# Test: db_post_group_announcement
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_post_announcement_owner_success(mock_conn):
    """测试群主成功发布公告"""
    operator_id = 1
    conversation_id = 101
    content = "这是一条重要公告"
    announcement_id = 1001

    mock_conn.fetchval.return_value = 'owner'
    mock_conn.fetchval.side_effect = ['owner', announcement_id]

    result = await db_post_group_announcement(mock_conn, operator_id, conversation_id, content, False)

    assert result == announcement_id


@pytest.mark.asyncio(loop_scope="session")
async def test_post_announcement_admin_success(mock_conn):
    """测试管理员成功发布公告"""
    operator_id = 2
    conversation_id = 101
    content = "公告内容"
    announcement_id = 1002

    mock_conn.fetchval.side_effect = ['admin', announcement_id]

    result = await db_post_group_announcement(mock_conn, operator_id, conversation_id, content, True)

    assert result == announcement_id


@pytest.mark.asyncio(loop_scope="session")
async def test_post_announcement_member_denied(mock_conn):
    """测试普通成员无法发布公告"""
    operator_id = 3
    conversation_id = 101
    content = "公告内容"

    mock_conn.fetchval.return_value = 'member'

    with pytest.raises(GroupException) as exc_info:
        await db_post_group_announcement(mock_conn, operator_id, conversation_id, content)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_post_announcement_pinned(mock_conn):
    """测试发布置顶公告"""
    operator_id = 1
    conversation_id = 101
    content = "置顶公告"
    announcement_id = 1003

    mock_conn.fetchval.side_effect = ['owner', announcement_id]

    result = await db_post_group_announcement(
        mock_conn, operator_id, conversation_id, content, is_pinned=True
    )

    assert result == announcement_id


# ==========================================
# Test: db_invite_to_group
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_invite_to_group_success(mock_conn):
    """测试成功邀请好友加入群聊"""
    inviter_id = 1
    conversation_id = 101
    invitee_id = 5
    invite_id = 2001

    mock_conn.fetchval.side_effect = ['member', False, False, invite_id]

    result = await db_invite_to_group(mock_conn, inviter_id, conversation_id, invitee_id)

    assert result == invite_id


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_cannot_invite_self(mock_conn):
    """测试不能邀请自己"""
    inviter_id = 1
    conversation_id = 101
    invitee_id = 1

    with pytest.raises(GroupException) as exc_info:
        await db_invite_to_group(mock_conn, inviter_id, conversation_id, invitee_id)

    assert exc_info.value.error_code == GroupErrors.CannotInviteSelf


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_inviter_not_in_group(mock_conn):
    """测试邀请者不在群里"""
    inviter_id = 1
    conversation_id = 101
    invitee_id = 5

    mock_conn.fetchval.return_value = None

    with pytest.raises(GroupException) as exc_info:
        await db_invite_to_group(mock_conn, inviter_id, conversation_id, invitee_id)

    assert exc_info.value.error_code == GroupErrors.NotInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_invitee_already_in_group(mock_conn):
    """测试被邀请人已在群里"""
    inviter_id = 1
    conversation_id = 101
    invitee_id = 3

    mock_conn.fetchval.side_effect = ['member', True]

    with pytest.raises(GroupException) as exc_info:
        await db_invite_to_group(mock_conn, inviter_id, conversation_id, invitee_id)

    assert exc_info.value.error_code == GroupErrors.AlreadyInGroup


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_pending_invitation_exists(mock_conn):
    """测试已有待审核的邀请记录"""
    inviter_id = 1
    conversation_id = 101
    invitee_id = 5

    mock_conn.fetchval.side_effect = ['member', False, True]

    with pytest.raises(GroupException) as exc_info:
        await db_invite_to_group(mock_conn, inviter_id, conversation_id, invitee_id)

    assert exc_info.value.error_code == GroupErrors.InvitePending


# ==========================================
# Test: db_review_group_invite
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_approve_success(mock_conn):
    """测试成功批准邀请"""
    reviewer_id = 1
    invite_id = 2001
    action = 'approved'

    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'invitee_id': 5,
        'status': 'pending'
    }
    mock_conn.fetchval.return_value = 'owner'

    await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    mock_conn.transaction.assert_called_once()
    # 应该调用两个 execute：更新邀请状态 + 添加成员
    update_calls = mock_conn.execute.call_args_list
    assert len(update_calls) >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_reject_success(mock_conn):
    """测试成功拒绝邀请"""
    reviewer_id = 1
    invite_id = 2001
    action = 'rejected'

    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'invitee_id': 5,
        'status': 'pending'
    }
    mock_conn.fetchval.return_value = 'owner'

    await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    mock_conn.transaction.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_invalid_action(mock_conn):
    """测试无效的审核操作"""
    reviewer_id = 1
    invite_id = 2001
    action = 'invalid_action'

    with pytest.raises(GroupException) as exc_info:
        await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    assert exc_info.value.error_code == GroupErrors.InvalidReviewAction


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_not_found(mock_conn):
    """测试邀请记录不存在"""
    reviewer_id = 1
    invite_id = 9999
    action = 'approved'

    mock_conn.fetchrow.return_value = None

    with pytest.raises(GroupException) as exc_info:
        await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    assert exc_info.value.error_code == GroupErrors.InviteNotFound


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_already_processed(mock_conn):
    """测试已处理的邀请记录"""
    reviewer_id = 1
    invite_id = 2001
    action = 'approved'

    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'invitee_id': 5,
        'status': 'approved'  # 已处理
    }

    with pytest.raises(GroupException) as exc_info:
        await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    assert exc_info.value.error_code == GroupErrors.InviteNotFound


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_permission_denied(mock_conn):
    """测试非权限用户审核邀请"""
    reviewer_id = 4
    invite_id = 2001
    action = 'approved'

    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'invitee_id': 5,
        'status': 'pending'
    }
    mock_conn.fetchval.return_value = 'member'

    with pytest.raises(GroupException) as exc_info:
        await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    assert exc_info.value.error_code == GroupErrors.PermissionDenied


@pytest.mark.asyncio(loop_scope="session")
async def test_review_invite_admin_approval(mock_conn):
    """测试管理员可以审核邀请"""
    reviewer_id = 2
    invite_id = 2001
    action = 'approved'

    mock_conn.fetchrow.return_value = {
        'conversation_id': 101,
        'invitee_id': 5,
        'status': 'pending'
    }
    mock_conn.fetchval.return_value = 'admin'

    await db_review_group_invite(mock_conn, reviewer_id, invite_id, action)

    mock_conn.transaction.assert_called_once()
