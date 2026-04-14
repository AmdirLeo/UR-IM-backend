import asyncpg
from typing import AsyncGenerator
import os
from core.config import settings

db_pool: asyncpg.Pool | None = None


async def init_system_data():
    """在服务启动时自检并初始化系统账号"""
    print("正在进行系统自检...")
    # get_db_conn 通常是一个依赖生成器 (async generator)，拿一条连接用完即毁
    async for conn in get_db_conn():
        try:
            # 这里的 ON CONFLICT (user_id) DO NOTHING 是灵魂！
            # 保证了每次重启服务都不会重复插入，也不会报错。
            await conn.execute("""
                INSERT INTO user_account (user_id, username, password, email)
                VALUES (10000, '系统通知助手', 'system_fake_password', 'system@ur-im.com')
                ON CONFLICT (user_id) DO NOTHING;
            """)
            print("系统核心数据自检完毕：系统助手(10000)已就绪。")
        except Exception as e:
            print(f"系统核心数据自检失败: {e}")
        break  # 取一次连接执行完毕就主动跳出循环


async def init_db_pool():
    """
    初始化全局数据库连接池。
    这个函数应该在 FastAPI 程序启动时被调用。
    """
    global db_pool

    # --- 新增调试代码：确认当前到底在用哪个 URL ---
    print(f"DEBUG: 准备连接数据库，当前 URL 为: {settings.DATABASE_URL}")
    # ------------------------------------------

    try:
        db_pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=5,  # 池子里最少保持 5 个常驻连接
            max_size=20,  # 最多允许同时建立 20 个连接
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
        raise RuntimeError(
            "数据库连接池尚未初始化！请确保在 FastAPI 的 lifespan 或测试 setup 中调用了 init_db_pool()")
    return db_pool


async def get_db_conn() -> AsyncGenerator[asyncpg.pool.PoolConnectionProxy, None]:
    """
    当 API 路由被访问时，这个函数会从连接池中借出一个连接，
    通过 yield 交给你的 Repo 函数使用，执行完毕后自动归还给连接池。
    """
    if db_pool is None:
        raise RuntimeError("数据库连接池未初始化，请检查启动配置！")

    async with db_pool.acquire() as conn:
        yield conn
