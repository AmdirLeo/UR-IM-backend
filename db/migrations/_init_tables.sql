DROP TABLE IF EXISTS group_invite CASCADE;
DROP TABLE IF EXISTS group_announcement CASCADE;
DROP TABLE IF EXISTS friend_tag_mapping CASCADE;
DROP TABLE IF EXISTS user_friend_tag CASCADE;
DROP TABLE IF EXISTS user_inbox CASCADE;
DROP TABLE IF EXISTS conversation_message CASCADE;
DROP TABLE IF EXISTS message CASCADE;
DROP TABLE IF EXISTS conversation_member CASCADE;
DROP TABLE IF EXISTS conversation CASCADE;
DROP TABLE IF EXISTS friend_request CASCADE;
DROP TABLE IF EXISTS friend_relationship CASCADE;
DROP TABLE IF EXISTS user_account CASCADE;
-- 1. 用户表
CREATE TABLE user_account (
    user_id BIGSERIAL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    avatar_url TEXT,                       -- 头像链接/filekey
    register_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    login_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 2. 好友关系表
CREATE TABLE friend_relationship (
    user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    friend_user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, friend_user_id)
);

-- 3. 好友申请表
CREATE TABLE friend_request (
    request_id BIGSERIAL PRIMARY KEY,
    sender_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    receiver_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    message TEXT,                          -- 申请留言
    status VARCHAR(20) DEFAULT 'pending',  -- 状态: pending, accepted, rejected
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. 会话基础表
CREATE TABLE conversation (
    conversation_id BIGSERIAL PRIMARY KEY,
    type VARCHAR(20) NOT NULL,             -- 会话类型：'private' (单聊) 或 'group' (群聊)
    conversation_name VARCHAR(255),        -- 群名称（私聊可为空）
    avatar_url VARCHAR(1024),
    announcement TEXT,                     -- 群公告
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_msg_id BIGINT,                    -- 全局id
    last_msg_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. 会话成员表
CREATE TABLE conversation_member (
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    member_user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    role VARCHAR(20) DEFAULT 'member',     -- 角色：'owner' (群主), 'admin' (管理员), 'member' (普通成员)
    is_muted BOOLEAN DEFAULT false,        -- 消息免打扰
    is_pinned BOOLEAN DEFAULT false,       -- 置顶会话
    read_index BIGINT DEFAULT 0,           -- 已读到的最大 msg_id (用于算未读数)
    join_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, member_user_id)
);

-- 6. 消息内容本体表
CREATE TABLE message (
    msg_id BIGSERIAL PRIMARY KEY,
    msg_body JSONB NOT NULL, 
    quote_id BIGINT,
    quote_count BIGINT DEFAULT 0
);

-- 7. 会话消息关联表 (处理引用关系)
CREATE TABLE conversation_message (
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    msg_id BIGINT NOT NULL REFERENCES message(msg_id) ON DELETE CASCADE,
    seq_id BIGINT,
    sender_id BIGINT NOT NULL REFERENCES user_account(user_id),
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, msg_id),
    UNIQUE (conversation_id, seq_id)
);

-- 8. 用户收件箱 (写扩散模型核心)
CREATE TABLE user_inbox (
    user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    msg_id BIGINT NOT NULL REFERENCES message(msg_id) ON DELETE CASCADE,
    is_read BOOLEAN DEFAULT false,         -- 可选：单条消息级别的已读状态
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, conversation_id, msg_id)
);

-- 9. 好友分组表
CREATE TABLE user_friend_tag (
    user_id BIGINT REFERENCES user_account(user_id) ON DELETE CASCADE,
    tag_name VARCHAR(50) NOT NULL,
    PRIMARY KEY (user_id, tag_name)
);

CREATE TABLE friend_tag_mapping (
    user_id BIGINT,
    friend_user_id BIGINT,
    tag_name VARCHAR(50),
    PRIMARY KEY (user_id, friend_user_id, tag_name),
    -- 级联删除
    FOREIGN KEY (user_id, friend_user_id) REFERENCES friend_relationship(user_id, friend_user_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, tag_name) REFERENCES user_friend_tag(user_id, tag_name) ON DELETE CASCADE
);

-- 10. 独立的群公告表
CREATE TABLE group_announcement (
    announcement_id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_pinned BOOLEAN DEFAULT false,       -- 是否置顶
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 11. 群成员邀请审核表
CREATE TABLE group_invite (
    invite_id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    inviter_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE, 
    invitee_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    status VARCHAR(20) DEFAULT 'pending',  -- 状态: pending, approved, rejected
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 会话置顶排序
CREATE INDEX idx_conv_last_time ON conversation(last_msg_time DESC);

-- 消息筛选
CREATE INDEX idx_conv_msg_sender ON conversation_message(sender_id);