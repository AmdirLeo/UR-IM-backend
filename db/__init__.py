import asyncio
import asyncpg
import os


async def init_db():
    # 格式: postgresql://用户名:密码@localhost:5432/数据库名
    db_url = "postgresql://postgres:123456@127.0.0.1:5432/im_db"

    print("正在连接数据库...")
    conn = await asyncpg.connect(db_url)

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        sql_file_path = os.path.join(current_dir, "migrations", "_init_tables.sql")

        print(f"正在读取建表脚本: {sql_file_path}")
        with open(sql_file_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

        print("正在执行建表操作...")
        await conn.execute(sql_content)

        print("数据库表初始化完成")

    except Exception as e:
        print(f"初始化失败: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(init_db())
