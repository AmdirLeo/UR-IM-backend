import asyncio
import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from contextlib import asynccontextmanager

from core.config import settings
from core.exceptions import setup_exception_handlers
from core.ws_manager import manager
from api.routes import friend, message, user, websocket
from api.dependencies import RateLimiter
from api.middleware import MultiLayerRateLimitMiddleware

# 导入数据库连接池生命周期函数
from db.database import init_db_pool, close_db_pool, init_system_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- 启动阶段 ----------
    # 1. 初始化数据库连接池（若失败则应用无法启动）
    await init_db_pool()
    await init_system_data()

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
app.add_middleware(MultiLayerRateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static/avatars", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# 全局异常处理器
setup_exception_handlers(app)

# 路由注册
app.include_router(user.router, prefix="/api/user", tags=["User Management"])
app.include_router(websocket.router, prefix="/websocket",
                   tags=["IM WebSocket"])
app.include_router(message.router, prefix="/api/message", tags=["Message API"])
app.include_router(friend.router, prefix="/api/friend",
                   tags=["Manage friendship"])


@app.get("/health")
async def health_check():
    return {"status": "online"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
