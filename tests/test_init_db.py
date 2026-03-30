import pytest
from unittest.mock import AsyncMock, patch, mock_open

# 因为是 __init__.py，所以直接从 db 导入即可
from db import init_db 

# ==========================================
# 覆盖 init_db 的测试
# ==========================================

@pytest.mark.asyncio(loop_scope="session")
# ⬇️ 重点修改这里：路径改成 "db.asyncpg.connect"
@patch("db.asyncpg.connect", new_callable=AsyncMock)
async def test_init_db_success(mock_connect):
    """
    测试正常流程：成功连接、读取文件、执行 SQL 并关闭连接
    """
    mock_conn = AsyncMock()
    mock_connect.return_value = mock_conn

    fake_sql = "CREATE TABLE mock_table (id INT);"
    
    with patch("builtins.open", mock_open(read_data=fake_sql)):
        await init_db()

    # 核心断言
    mock_connect.assert_called_once_with("postgresql://postgres:123456@127.0.0.1:5432/im_db")
    mock_conn.execute.assert_called_once_with(fake_sql)
    mock_conn.close.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
# ⬇️ 重点修改这里：路径改成 "db.asyncpg.connect"
@patch("db.asyncpg.connect", new_callable=AsyncMock)
async def test_init_db_exception(mock_connect):
    """
    测试异常流程：如果执行 SQL 时报错，连接是否依然能被安全关闭
    """
    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = Exception("模拟的数据库报错")
    mock_connect.return_value = mock_conn

    with patch("builtins.open", mock_open(read_data="BAD SQL;")):
        await init_db() 

    # 核心断言
    mock_conn.execute.assert_called_once()
    mock_conn.close.assert_called_once()