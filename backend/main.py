import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from core.config import settings
from core.exceptions import setup_exception_handlers
from api.routes import chat
from core.ws_manager import manager
from api.routes import chat, message

# 使用 lifespan 管理后台任务
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行：创建后台心跳巡检任务
    task = asyncio.create_task(manager.check_heartbeats())
    yield
    # 关闭时执行：取消任务
    task.cancel()

# 将 lifespan 传给 app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True, 
    allow_methods=["*"],  
    allow_headers=["*"],  
)

setup_exception_handlers(app)

app.include_router(chat.router, prefix="/chat", tags=["IM WebSocket"])

app.include_router(message.router, prefix="/api/messages", tags=["Message API"])

@app.get("/health")
async def health_check():
    return {"status": "online"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)