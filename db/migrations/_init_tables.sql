-- 1. 用户表 (补充了 API 要求的 email 和 avatar)
CREATE TABLE user_account (
    user_id BIGSERIAL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,    -- 邮箱，登录/找回密码需要，需保证唯一
    avatar_url TEXT,                       -- 头像链接/filekey
    register_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    login_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 2. 好友关系表 (补充了 API 要求的 tag 分组功能)
CREATE TABLE friend_relationship (
    user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    friend_user_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    tag VARCHAR(100),                      -- 好友分组标签
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, friend_user_id)
);

-- 3. 好友申请表 (API 中提到“需要经过对方同意”，所以需要一个中间表存状态)
CREATE TABLE friend_request (
    request_id BIGSERIAL PRIMARY KEY,
    sender_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    receiver_id BIGINT NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    message TEXT,                          -- 申请留言
    status VARCHAR(20) DEFAULT 'pending',  -- 状态: pending, accepted, rejected
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. 会话基础表 (补充了类型和群公告)
CREATE TABLE conversation (
    conversation_id BIGSERIAL PRIMARY KEY,
    type VARCHAR(20) NOT NULL,             -- 会话类型：'private' (单聊) 或 'group' (群聊)
    conversation_name VARCHAR(255),        -- 群名称（私聊可为空）
    announcement TEXT,                     -- 群公告
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 5. 会话成员表 (补充了 API 要求的群主/管理员权限、免打扰、置顶)
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
    -- 使用 JSONB 支持富文本扩展，例如: {"type": "text", "content": "你好"} 或 {"type": "image", "url": "..."}
    msg_body JSONB NOT NULL 
);

-- 7. 会话消息关联表 (处理引用关系)
CREATE TABLE conversation_message (
    conversation_id BIGINT NOT NULL REFERENCES conversation(conversation_id) ON DELETE CASCADE,
    msg_id BIGINT NOT NULL REFERENCES message(msg_id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL REFERENCES user_account(user_id),
    quote_id BIGINT,                       -- 引用的被回复消息的 msg_id
    quote_count INT DEFAULT 0,             -- 被引用的次数
    create_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, msg_id)
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

