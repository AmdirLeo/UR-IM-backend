backend/
├── main.py              # 程序总入口：挂载路由、初始化生命周期、配置中间件
├── requirements.txt     # 依赖清单：记录所有精确版本的 Python 包
├── .env.example         # 环境变量模板：本地开发请拷贝为 .env 并填写配置
├── core/                # 【核心层】全站共享的单例与配置
│   ├── config.py        # 读取环境变量，定义全局 Settings 对象
│   └── security.py      # 安全机制：Bcrypt 哈希校验、JWT 签发与解析
├── api/                 # 【接口层】处理请求进入与响应返回
│   ├── dependencies.py  # 依赖注入：DB 连接获取、Token 身份验证中间件
│   └── routes/          # 具体的路由定义：仅负责解析 Request 和调用 Service
├── schemas/             # 【验证层】数据契约定义 (Pydantic)
│   ├── user.py          # 用户注册、登录等请求体 (Request Body) 与响应结构
│   └── token.py         # JWT Token 的返回格式定义
├── services/            # 【业务层】核心业务逻辑处理
│   └── user_service.py  # 逻辑封装：如“校验验证码->创建用户->下发欢迎邮件”的编排
└── db/                  # 【持久层】数据存取核心
    ├── database.py      # 连接池管理：asyncpg pool 的初始化与生命周期
    └── repositories/    # 原生 SQL 操作：唯一允许编写 SQL 语句的地方

frontemd/