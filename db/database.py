import asyncpg
from typing import AsyncGenerator
import os


db_pool: asyncpg.Pool | None = None


async def init_db_pool():
    """
    初始化全局数据库连接池。
    这个函数应该在 FastAPI 程序启动时被调用。
    """
    global db_pool

    # 这个 URL 应该从 core/config.py 或 .env 文件中读取
    # 先写死
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:123456@127.0.0.1:5432/im_db"
    )

    try:
        db_pool = await asyncpg.create_pool(
            dsn=db_url,
            min_size=5,    # 池子里最少保持 5 个常驻连接
            max_size=20,   # 最多允许同时建立 20 个连接
            command_timeout=60.0,  # 任何 SQL 执行超过 60 秒自动掐断，防止死锁拖垮整个系统
        )
        print("数据库连接池初始化成功")
    except Exception as e:
        print(f"数据库连接池初始化失败: {e}")
        raise e


async def close_db_pool():
    """
    平滑关闭数据库连接池。
    这个函数应该在 FastAPI 程序关闭时被调用。
    """
    global db_pool
    if db_pool is not None:
        await db_pool.close()
        print("数据库连接池关闭。")

def get_db_pool() -> asyncpg.Pool:
    """
    获取全局数据库连接池实例。
    在执行 CRUD 操作或测试环境的数据清理时调用此函数。
    """
    global db_pool
    if db_pool is None:
        raise RuntimeError("数据库连接池尚未初始化！请确保在 FastAPI 的 lifespan 或测试 setup 中调用了 init_db_pool()")
    return db_pool

async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    当 API 路由被访问时，这个函数会从连接池中借出一个连接，
    通过 yield 交给你的 Repo 函数使用，执行完毕后自动归还给连接池。
    """
    if db_pool is None:
        raise RuntimeError("数据库连接池未初始化，请检查启动配置！")

    async with db_pool.acquire() as conn:
        yield conn
