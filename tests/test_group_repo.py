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
