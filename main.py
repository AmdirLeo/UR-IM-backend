import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from contextlib import asynccontextmanager

from core.config import settings
from core.exceptions import setup_exception_handlers
from core.ws_manager import manager
from api.routes import chat, friend, message, user

# 导入数据库连接池生命周期函数
from db.database import init_db_pool, close_db_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- 启动阶段 ----------
    # 1. 初始化数据库连接池（若失败则应用无法启动）
    await init_db_pool()

    # 2. 启动 WebSocket 心跳巡检后台任务
    heartbeat_task = asyncio.create_task(manager.check_heartbeats())

    yield  # 应用运行中

    # ---------- 关闭阶段 ----------
    # 1. 取消心跳巡检任务
    heartbeat_task.cancel()

    # 2. 平滑关闭数据库连接池
    await close_db_pool()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# 跨域中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常处理器
setup_exception_handlers(app)

# 路由注册
app.include_router(user.router, prefix="/api/user", tags=["User Management"])
app.include_router(chat.router, prefix="/chat", tags=["IM WebSocket"])
app.include_router(message.router, prefix="/api/message", tags=["Message API"])
app.include_router(friend.router, prefix="/api/friend", tags=["Manage friendship"])


@app.get("/health")
async def health_check():
    return {"status": "online"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
