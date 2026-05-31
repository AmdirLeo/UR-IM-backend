# UR-IM (Instant Messaging System)

## 项目概览 (Project Overview)
**UR-IM** 是一个基于 **FastAPI** 和 **React** 构建的高性能即时通讯平台。旨在提供极速、稳定、安全的实时消息传输体验，支持高并发 WebSocket 连接和高效的数据持久化，适用于构建企业级通信系统及实时交互应用。

## 技术栈 (Tech Stack)

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/redis-%23DD0031.svg?style=for-the-badge&logo=redis&logoColor=white)
![JWT](https://img.shields.io/badge/JWT-black?style=for-the-badge&logo=JSON%20web%20tokens)
![WebSocket](https://img.shields.io/badge/WebSocket-010101?style=for-the-badge&logo=socket.io)
![Pytest](https://img.shields.io/badge/Pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)

- **后端**: FastAPI, asyncpg (PostgreSQL 异步驱动)
- **前端**: React
- **缓存 & 消息代理**: Redis
- **安全与认证**: JWT (JSON Web Tokens)
- **双向通信**: WebSocket
- **测试框架**: Pytest

## 核心特性 (Key Features)

- **异步非阻塞**: 基于 FastAPI 与 `asyncpg` 实现全链路异步操作，极大提升 I/O 并发处理能力。
- **实时消息通讯**: 采用原生 WebSocket 提供低延迟、双向的即时通讯能力，并实现智能心跳检测机制。
- **安全可靠**: 采用 JWT 进行无状态身份鉴权，并使用 bcrypt 进行密码安全哈希计算。
- **高性能缓存**: 集成 Redis 进行高速数据缓存、在线状态管理以及可能的消息路由。
- **清晰分层架构**: 将 API 路由、业务逻辑、数据存储、Pydantic 校验和配置管理等彻底解耦，易于维护与扩展。

## 目录结构 (Project Structure)

```text
.
.
├── main.py                  # 程序总入口：挂载路由、初始化生命周期、配置中间件、启动 WebSocket 心跳
├── requirements.txt         # 依赖清单：记录所有精确版本的 Python 包
├── .env.example             # 环境变量模板：本地开发请拷贝为 .env 并填写配置
├── Dockerfile               # Docker 镜像构建配置
├── docker-compose.yml       # 容器编排配置（用于快速启动 Postgres, Redis 等依赖）
├── .gitlab-ci.yml           # GitLab CI/CD 持续集成流水线配置
├── sonar-project.properties # SonarQube 代码质量与覆盖率扫描配置
├── pytest.ini               # Pytest 自动化测试框架全局配置
│
├── api/                     # 【接口层】处理请求进入与响应返回
│   ├── dependencies.py      # 依赖注入：全局 Token 鉴权 (get_current_user_id)、数据库连接等
│   ├── middleware.py        # 全局中间件：如跨域处理、耗时统计或日志记录
│   └── routes/              # 具体的路由定义：仅负责解析 Request 和调用 Service
│       ├── websocket.py     # WebSocket 握手与消息路由
│       ├── conversation.py  # 会话管理相关接口
│       ├── friend.py        # 好友系统相关接口
│       ├── group.py         # 群组管理相关接口
│       ├── message.py       # 消息拉取与发送接口
│       └── user.py          # 用户鉴权与个人信息接口
│
├── core/                    # 【核心层】全站共享的单例与配置
│   ├── config.py            # 配置管理：使用 Pydantic Settings 读取环境变量
│   ├── security.py          # 安全机制：密码哈希验证、JWT Token 签发与解析
│   ├── exceptions.py        # 异常体系：自定义业务异常与全局异常处理器
│   ├── s3_client.py         # S3/MinIO 对象存储客户端（用于头像、文件上传）
│   └── ws_manager.py        # WebSocket 连接管理：连接池、心跳检测、消息广播
│
├── db/                      # 【持久层】数据存取核心
│   ├── database.py          # PostgreSQL 连接池管理 (asyncpg)
│   ├── redis_client.py      # Redis 客户端配置 (用于缓存或限流)
│   ├── migrations/          # 数据库初始化与迁移脚本 (如 _init_tables.sql)
│   └── repositories/        # Repository 模式：唯一允许编写原生 SQL 语句的地方
│
├── schemas/                 # 【验证层】数据契约定义 (Pydantic Models)
│   └── user.py, friend.py, group.py, message.py, conversation.py
│
├── services/                # 【业务层】核心业务逻辑处理 (隔离路由与数据库)
│   └── user_service.py, friend_service.py, group_service.py, message_service.py
│
└── tests/                   # 【测试层】单元测试与集成测试
    ├── conftest.py          # Pytest 共享 fixture 配置 (提供 Mock 数据库连接等)
    └── test_*.py            # 覆盖各模块的自动化测试用例
```

## 快速开始 (Quick Start)

### 环境要求 (Prerequisites)

- Python 3.9+
- PostgreSQL 12+
- Redis 6.0+
- Node.js 16+ (针对前端应用)

### 安装依赖 (Install Dependencies)

```bash
# 建议使用虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/MacOS
# .\venv\Scripts\activate # Windows

# 安装后端依赖
pip install -r requirements.txt
```

### 配置环境变量 (Environment Configuration)

将提供的环境模板文件复制为 `.env` 并根据本地环境进行修改：

```bash
cp .env.example .env
```

请确保 `.env` 中的数据库连接 (PostgreSQL)、Redis 配置和 JWT Secret Key 填写正确。

### 启动服务 (Run the Application)

使用 Uvicorn 启动 FastAPI 后端服务：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

服务启动后，可以访问 `http://localhost:8000/docs` 查看由 Swagger UI 自动生成的 API 接口文档。

## 测试与质量 (Testing & Quality)

本项目高度重视代码质量与系统稳定性：

- **Pytest 测试**: 配置了 `pytest` 作为测试框架。覆盖了核心的 API 路由和服务逻辑。在 `tests/` 目录下运行 `pytest` 即可执行单元测试和集成测试，确保每次迭代不会破坏现有功能。
- **SonarQube 代码审查**: 项目集成了 SonarQube (`sonar-project.properties`) 进行持续的代码质量和安全性分析，规范代码异味，提高代码可读性和可维护性。

```bash
# 运行单元测试
pytest
```
