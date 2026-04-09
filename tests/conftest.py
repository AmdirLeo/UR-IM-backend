from db.database import init_db_pool, close_db_pool
from main import app
import pytest
import os
import asyncpg
from fastapi.testclient import TestClient
import asyncio

from dotenv import load_dotenv
load_dotenv()

TEST_DB_NAME = "test_im_db"

# 优先读取 CI 注入的环境变量 DATABASE_URL，读不到再用本地的地址作为备胎
TEST_DB_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://postgres:123456@127.0.0.1:5432/{TEST_DB_NAME}")

# 同样使用 getenv 提供备胎。
# 优先读取 CI 环境的默认库 URL，如果本地开发没配环境变量，则使用你本地暂存的稳妥地址
DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:123456@127.0.0.1:5432/postgres?sslmode=disable",
)

# 必须在导入 app 之前设置环境变量
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["DEFAULT_DATABASE_URL"] = DEFAULT_DB_URL


# === 新增：强制全局单例事件循环 ===


@pytest.fixture(scope="session")
def event_loop():
    """
    强制整个测试会话（Session）共用同一个 Event Loop！
    彻底解决 asyncpg 连接池与测试用例跨循环抛出 RuntimeError 的世纪难题。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    # 1. 强制建库
    sys_conn = await asyncpg.connect(DEFAULT_DB_URL)
    try:
        exists = await sys_conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME)
        if not exists:
            await sys_conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
            print(f"成功创建测试专用数据库: {TEST_DB_NAME}")
    except Exception as e:
        print(f"建库失败: {e}")
    finally:
        await sys_conn.close()

    # 2. 初始化全局连接池
    await init_db_pool()

    # 3. 读取并执行建表 SQL
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql_file_path = os.path.join(
        current_dir, "db", "migrations", "_init_tables.sql")

    try:
        conn = await asyncpg.connect(TEST_DB_URL)

        # --- 新增这行：彻底清空 public schema 并重建 ---
        # 这一步能保证不管上次留下了什么垃圾数据或表结构，都会被一扫而空
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

        with open(sql_file_path, "r", encoding="utf-8") as f:
            await conn.execute(f.read())
        print("测试数据库初始化建表完成。")
    except Exception as e:
        print(f"测试数据库建表失败: {e}")
    finally:
        if not conn.is_closed():
            await conn.close()

    # ==========================================
    # 4. 关键：交出控制权，让所有测试用例开始运行
    # ==========================================
    yield

    # 5. 测试结束后，必须优雅关闭连接池！否则会报跨循环错误
    try:
        await close_db_pool()
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def clear_database_data():
    conn = None
    try:
        # 使用 CASCADE 级联清空所有相关表
        conn = await asyncpg.connect(TEST_DB_URL)
        await conn.execute("""
            TRUNCATE TABLE
                user_account,
                friend_relationship,
                friend_request,
                conversation,
                conversation_member,
                message,
                conversation_message,
                user_inbox
            CASCADE;
        """)
    except Exception as e:
        print(f"清空测试数据失败: {e}")
    finally:
        # 终极保护：哪怕连接断开，这里的安全外套也不会让测试崩溃！
        try:
            if conn and not conn.is_closed():
                await conn.close()
        except Exception:
            pass


@pytest.fixture
def test_client(scope="session"):
    """提供一个测试专用的 FastAPI Client"""
    with TestClient(app) as client:
        yield client
