import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict
from unittest.mock import patch, MagicMock
from main import app
from api.routes.message import router
from core.exceptions import setup_exception_handlers

# Setup FastAPI App for testing
setup_exception_handlers(app)
app.include_router(router, prefix="/api/message")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_dependencies():
    # Mock CurrentUserId and DBConnection to bypass actual DB connections during this isolated test suite
    pass


@pytest.mark.asyncio(loop_scope="session")
@patch("services.message_service.db_send_message")
@patch("services.message_service.db_quote_message")
async def test_send_message(mock_quote, mock_send):
    mock_send.return_value = 1
    mock_quote.return_value = 2
    # Provide a simple JWT token mock payload
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        # 1. Test normal send message
        req_data = {
            "conversation_id": 100,
            "local_id": "local_123",
            "message_content": "Hello World",
            "msg_type": "text",
        }
        res = await client.post("/api/message/send", json=req_data, headers=headers)
        assert res.status_code == 200
        assert res.json()["data"]["msg_id"] == 1
        assert res.json()["data"]["local_id"] == "local_123"
        mock_send.assert_called_once()
        # 2. Test quote message
        req_data_quote = {
            "conversation_id": 100,
            "local_id": "local_124",
            "message_content": "Hello again",
            "msg_type": "text",
            "quote_message_id": 999,
        }
        res_quote = await client.post(
            "/api/message/send", json=req_data_quote, headers=headers
        )
        assert res_quote.status_code == 200
        assert res_quote.json()["data"]["msg_id"] == 2
        mock_quote.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
@patch("services.message_service.db_filter_messages")
async def test_search_message(mock_filter):
    from datetime import datetime, timezone

    # Mocking db return data
    mock_filter.return_value = [
        {
            "sender_id": 1,
            "msg_id": 10,
            "msg_content": "Test Msg",
            "create_time": datetime.now(timezone.utc),
        }
    ]
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        req_data = {"conversation_id": 100, "keyword": "Test"}
        res = await client.post("/api/message/search", json=req_data, headers=headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert len(data) == 1
        assert data[0]["msg_id"] == 10
        assert data[0]["msg"] == "Test Msg"
        mock_filter.assert_called_once()
        # Validation test: Empty keyword, start_time, end_time
        empty_req = {"conversation_id": 100}
        res_empty = await client.post(
            "/api/message/search", json=empty_req, headers=headers
        )
        # Should raise business exception INVALID_REQUEST (which is handled and might return 400/200 code based on global handler)
        # Check if error message corresponds to the thrown Exception string in business logic.
        assert "全为空" in res_empty.text or res_empty.status_code >= 400


@pytest.mark.asyncio(loop_scope="session")
@patch("services.message_service.db_delete_local_messages")
async def test_delete_message(mock_delete):
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        req_data = {"conversation_id": 100, "message_id": 10}
        # In HTTPX we need to send json in a custom request for DELETE, or use client.request
        res = await client.request(
            "DELETE", "/api/message", json=req_data, headers=headers
        )
        assert res.status_code == 200
        mock_delete.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
@patch("services.conversation_service.db_sync_conversations")
async def test_sync_conversations(mock_db_sync):
    from core.security import create_access_token

    # 模拟数据库返回空列表，符合会话同步逻辑
    mock_db_sync.return_value = []

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        res = await client.get("/api/conversation/sync", headers=headers)

        assert res.status_code == 200
        assert res.json()["data"] == []
        mock_db_sync.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
@patch("services.conversation_service.db_mark_conversation_as_read")
async def test_read_acknowledgement(mock_db_read):
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        req_data = {"conversation_id": 100, "msg_id": 999}
        res = await client.post(
            "/api/conversation/read_ack", json=req_data, headers=headers
        )

        assert res.status_code == 200
        mock_db_read.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
@patch("services.message_service.db_get_message_history")
async def test_get_message_history(mock_db_history):
    from core.security import create_access_token

    # 模拟数据库返回历史消息列表（这里为了简化测试，直接返回空列表）
    # 返回空列表通常足以通过 Pydantic 对 List[MessageHistoryItem] 的序列化校验
    mock_db_history.return_value = []

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        # 对应 MessageHistoryRequest 的结构构造请求体
        req_data = {
            "conversation_id": 100,
            "start_msg_id": 999,  # 可以为 None
            "limit": 20,
        }
        res = await client.post("/api/message/history", json=req_data, headers=headers)

        assert res.status_code == 200
        assert res.json()["data"] == []
        mock_db_history.assert_called_once()
