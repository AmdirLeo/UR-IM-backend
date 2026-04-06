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
# 优先读取 CI 注入的环境变量 DATABASE_URL，读不到再用你原来的本地地址作为备胎
TEST_DB_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://postgres:123456@127.0.0.1:5432/{TEST_DB_NAME}"
)
# 连接默认库的 URL，专门用来执行 CREATE DATABASE
DEFAULT_DB_URL = os.environ["DATABASE_URL"]

os.environ["DATABASE_URL"] = TEST_DB_URL


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
    # 1. 连接到默认数据库，并强制创建测试数据库
    sys_conn = await asyncpg.connect(DEFAULT_DB_URL)
    try:
        # PostgreSQL 不允许在一个事务块中执行 CREATE DATABASE，所以要用执行直接命令的方式
        # 检查是否已存在，不存在才创建
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await sys_conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
            print(f"成功创建测试专用数据库: {TEST_DB_NAME}")
    except Exception as e:
        print(f"建库失败 (可能已存在或权限不足): {e}")
    finally:
        await sys_conn.close()

    # 2. 初始化连接池 (此时它会连上刚刚建好的 test_im_db)
    await init_db_pool()

    # 3. 读取并执行建表 SQL
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql_file_path = os.path.join(current_dir, 'db', 'migrations', '_init_tables.sql')

    try:
        conn = await asyncpg.connect(TEST_DB_URL)

        # --- 新增这行：彻底清空 public schema 并重建 ---
        # 这一步能保证不管上次留下了什么垃圾数据或表结构，都会被一扫而空
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

        with open(sql_file_path, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        await conn.execute(sql_content)
        print("测试数据库初始化建表完成。")
    except Exception as e:
        print(f"测试数据库建表失败: {e}")
    finally:
        await conn.close()

# ... 后面的 clear_database_data 和 test_client 保持不变 ...


@pytest.fixture(autouse=True)
async def clear_database_data():
    """
    Function 级别的 Fixture：每个测试用例运行前，自动清空表数据。
    """
    conn = await asyncpg.connect(TEST_DB_URL)
    try:
        # 使用 CASCADE 级联清空所有相关表
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
        await conn.close()


@pytest.fixture
def test_client():
    """
    提供一个测试专用的 FastAPI Client
    """
    with TestClient(app) as client:
        yield client
