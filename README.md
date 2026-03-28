backend/
├── main.py              # 程序总入口：挂载路由、初始化生命周期、配置中间件、启动 WebSocket 心跳检测
├── requirements.txt     # 依赖清单：记录所有精确版本的 Python 包
├── .env.example         # 环境变量模板：本地开发请拷贝为 .env 并填写配置
├── api/                 # 【接口层】处理请求进入与响应返回
│   ├── dependencies.py  # 依赖注入：全局 Token 身份验证 (get_current_user_id)、数据库连接获取等
│   └── routes/          # 具体的路由定义：仅负责解析 Request 和调用 Service，不编写业务逻辑
│       └── chat.py
│       └── message.py
│       └── user.py      # 用户相关接口：注册、登录、登出、注销、个人信息修改、头像上传
├── core/                # 【核心层】全站共享的单例与配置
│   ├── config.py        # 配置管理：使用 Pydantic Settings 读取 .env 环境变量，定义全局 Settings 对象
│   └── security.py      # 安全机制：bcrypt 密码哈希验证、JWT Token 签发与解析
│   └── exceptions.py    # 异常体系：BusinessException 自定义业务异常 + 全局异常处理器注册函数
│   └── ws_manager.py    # WebSocket 连接管理：ConnectionManager 单例，负责连接池、心跳检测、消息广播
├── db/                  # 【持久层】数据存取核心
│   ├── database.py      # 连接池管理：asyncpg pool 的初始化与生命周期（待补充）
│   └── repositories/    # 原生 SQL 操作：唯一允许编写 SQL 语句的地方，每个表对应一个 repository 文件（待补充）
├── schemas/             # 【验证层】数据契约定义 (Pydantic)
│   ├── user.py          # 用户相关 Schema：注册 / 登录 / 修改信息的请求体与响应结构
│   └── message.py       # 消息相关 Schema：发送消息的请求体、历史消息的返回结构
├── services/            # 【业务层】核心业务逻辑处理
|   └── user_service.py  # 用户业务逻辑：验证码校验 → 用户创建 → 发送欢迎邮件；登录验证 → Token 签发；信息修改等
└── tests/               # 单元测试与集成测试