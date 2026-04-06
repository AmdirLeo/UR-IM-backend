import pytest
from httpx import AsyncClient, ASGITransport
from typing import Dict
from unittest.mock import patch
from main import app
from api.routes.conversation import router
from core.exceptions import setup_exception_handlers

# Setup FastAPI App for testing
setup_exception_handlers(app)
app.include_router(router, prefix="/api/conversation")


def get_auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio(loop_scope="session")
@patch("services.conversation_service.db_set_conversation_mute")
async def test_mute_conversation(mock_mute):
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        req_data = {"conversation_id": 100, "is_muted": True}
        res = await client.put("/api/conversation/mute", json=req_data, headers=headers)
        assert res.status_code == 200
        mock_mute.assert_called_once()


@pytest.mark.asyncio(loop_scope="session")
@patch("services.conversation_service.db_set_conversation_pin")
async def test_pin_conversation(mock_pin):
    from core.security import create_access_token

    token = create_access_token(data={"sub": "1"})
    headers = get_auth_headers(token)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        req_data = {"conversation_id": 100, "is_pinned": True}
        res = await client.put("/api/conversation/pin", json=req_data, headers=headers)
        assert res.status_code == 200
        mock_pin.assert_called_once()
