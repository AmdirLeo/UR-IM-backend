import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, ANY

from api.routes.friend import router
from api.dependencies import get_current_user_id, get_db_conn
from core.exceptions import setup_exception_handlers

# ==========================================
# 1. Setup & Mocks
# ==========================================
app = FastAPI()
setup_exception_handlers(app)
app.include_router(router, prefix="/api")


class MockDBConnection:
    pass


async def override_get_db_conn():
    yield MockDBConnection()


async def override_get_current_user_id():
    return 1  # 模拟当前登录用户ID


app.dependency_overrides[get_db_conn] = override_get_db_conn
app.dependency_overrides[get_current_user_id] = override_get_current_user_id

client = TestClient(app)


# ==========================================
# 2. Search Users Tests (GET /search)
# ==========================================
@patch("services.user_service.db_search_users", new_callable=AsyncMock)
def test_search_users_success(mock_search):
    # 模拟返回的用户列表
    mock_search.return_value = {
        "items": [
            {
                "user_id": 2,
                "username": "张三丰",
                "avatar_url": "http://example.com/avatar2.jpg",
            },
            {"user_id": 3, "username": "张三疯", "avatar_url": None},
        ],
        "total": 2,
        "page": 1,
        "page_size": 20,
    }

    # 测试默认分页（page=1, size=20）
    response = client.get("/api/friend/search?keyword=张三")
    assert response.status_code == 200
    # 注意：service中是通过关键字传参的
    mock_search.assert_called_once_with(ANY, keyword="张三", page=1, page_size=20)
    mock_search.reset_mock()

    # 测试自定义分页
    response = client.get("/api/friend/search?keyword=张三&page=2&size=5")
    assert response.status_code == 200
    mock_search.assert_called_once_with(ANY, keyword="张三", page=2, page_size=5)


@patch("services.user_service.db_search_users", new_callable=AsyncMock)
def test_search_users_empty(mock_search):
    mock_search.return_value = {"items": [], "total": 0, "page": 1, "page_size": 20}
    response = client.get("/api/friend/search?keyword=不存在的用户")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert data["data"] == []


@patch("services.user_service.db_search_users", new_callable=AsyncMock)
def test_search_users_invalid_keyword(mock_search):
    # 关键词太短（min_length=1，长度0）
    response = client.get("/api/friend/search?keyword=")
    assert response.status_code == 422  # 参数校验失败


# ==========================================
# 3. Apply Friend Tests (POST /apply)
# ==========================================
@patch("services.friend_service.db_create_friend_request", new_callable=AsyncMock)
def test_apply_friend_success(mock_create):
    # 根据现有的 service 逻辑，只需要 mock 这一个 DB 调用
    mock_create.return_value = True  # 创建成功

    response = client.post(
        "/api/friend/apply", json={"target_user_id": 2, "message": "交个朋友"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert data["msg"] == "好友申请已发送"
    mock_create.assert_called_once()


def test_apply_friend_self():
    # 申请添加自己 (代码在此处直接抛出异常，不需要 Mock 数据库)
    response = client.post(
        "/api/friend/apply", json={"target_user_id": 1, "message": "不能加自己"}
    )
    assert response.status_code == 400
    data = response.json()
    assert data["msg"] == "不能添加自己为好友"


@patch("services.friend_service.db_create_friend_request", new_callable=AsyncMock)
def test_apply_friend_repo_fails(mock_create):
    # 模拟底层的 db 返回 False
    mock_create.return_value = False
    response = client.post(
        "/api/friend/apply", json={"target_user_id": 999, "message": "不存在"}
    )
    # 根据 service 逻辑，会抛出 500
    assert response.status_code == 500
    assert response.json()["msg"] == "好友申请发送失败"


# ==========================================
# 4. Handle Friend Request Tests (PUT /handle)
# ==========================================
@patch("services.friend_service.db_handle_friend_request", new_callable=AsyncMock)
def test_handle_request_accept_success(mock_handle):
    mock_handle.return_value = True

    response = client.put(
        "/api/friend/handle", json={"request_id": 123, "action": "accepted"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert data["msg"] == "已同意好友申请"
    mock_handle.assert_called_once()


@patch("services.friend_service.db_handle_friend_request", new_callable=AsyncMock)
def test_handle_request_reject_success(mock_handle):
    mock_handle.return_value = True

    response = client.put(
        "/api/friend/handle", json={"request_id": 123, "action": "rejected"}
    )
    assert response.status_code == 200
    assert response.json()["msg"] == "已拒绝好友申请"


@patch("services.friend_service.db_handle_friend_request", new_callable=AsyncMock)
def test_handle_request_repo_fails(mock_handle):
    mock_handle.return_value = False  # 如果更新失败（比如申请已处理或不存在）

    response = client.put(
        "/api/friend/handle", json={"request_id": 123, "action": "accepted"}
    )
    assert response.status_code == 400
    assert response.json()["msg"] == "处理失败，请稍后重试"


# ==========================================
# 5. Remove Friend Tests (DELETE /remove/{friend_id})
# ==========================================
@patch("services.friend_service.db_remove_friend", new_callable=AsyncMock)
def test_remove_friend_success(mock_remove):
    mock_remove.return_value = True
    response = client.delete("/api/friend/remove/2")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert data["msg"] == "好友删除成功"
    mock_remove.assert_called_once()


@patch("services.friend_service.db_remove_friend", new_callable=AsyncMock)
def test_remove_friend_self(mock_remove):
    # 尝试删除自己，应该直接被 service 层拦截
    response = client.delete("/api/friend/remove/1")
    assert response.status_code == 400
    assert response.json()["msg"] == "不能删除自己"
    mock_remove.assert_not_called()  # 不应该调用 repo


@patch("services.friend_service.db_remove_friend", new_callable=AsyncMock)
def test_remove_friend_not_friend(mock_remove):
    mock_remove.return_value = False  # repo 返回删除失败（找不到关系）
    response = client.delete("/api/friend/remove/2")
    assert response.status_code == 404
    assert response.json()["msg"] == "好友不存在或已删除"


# ==========================================
# 6. Get Friend List Tests (GET /)
# ==========================================
@patch("services.friend_service.db_get_friend_list", new_callable=AsyncMock)
def test_get_friend_list_success(mock_list):
    mock_list.return_value = [
        {
            "user_id": 2,
            "username": "张三",
            "avatar_url": "http://example.com/2.jpg",
            "tag": "同学",
            "be_friend_time": "2025-03-20T10:30:00",
        },
        {
            "user_id": 3,
            "username": "李四",
            "avatar_url": None,
            "tag": None,
            "be_friend_time": "2025-03-21T15:20:00",
        },
    ]
    response = client.get("/api/friend")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert len(data["data"]) == 2
    assert data["data"][0]["username"] == "张三"
    assert data["data"][0]["tag"] == "同学"
    assert "be_friend_time" in data["data"][0]
    mock_list.assert_called_once()


@patch("services.friend_service.db_get_friend_list", new_callable=AsyncMock)
def test_get_friend_list_empty(mock_list):
    mock_list.return_value = []
    response = client.get("/api/friend")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert data["data"] == []


# ==========================================
# 7. Friend Tag Tests (POST /tag/...)
# ==========================================


@patch("services.friend_service.db_create_friend_tag", new_callable=AsyncMock)
def test_create_friend_tag_success(mock_create):
    mock_create.return_value = None
    response = client.post("/api/friend/tag/new", json={"tag_name": "同学"})
    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["msg"] == "新建标签成功"


@patch("services.friend_service.db_create_friend_tag", new_callable=AsyncMock)
def test_create_friend_tag_conflict(mock_create):
    mock_create.side_effect = Exception("db error")
    response = client.post("/api/friend/tag/new", json={"tag_name": "同学"})
    assert response.status_code == 409
    assert response.json()["msg"] == "该分组已存在"


@patch("services.friend_service.db_delete_friend_tag", new_callable=AsyncMock)
def test_delete_friend_tag_success(mock_delete):
    mock_delete.return_value = None
    response = client.post("/api/friend/tag/delete", json={"tag_name": "同学"})
    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["msg"] == "删除标签成功"


@patch("services.friend_service.db_delete_friend_tag", new_callable=AsyncMock)
def test_delete_friend_tag_not_found(mock_delete):
    mock_delete.side_effect = Exception("db error")
    response = client.post("/api/friend/tag/delete", json={"tag_name": "同学"})
    assert response.status_code == 404
    assert response.json()["msg"] == "分组不存在"


@patch("services.friend_service.db_add_friends_to_tag", new_callable=AsyncMock)
def test_add_friends_to_tag_success(mock_add):
    mock_add.return_value = None
    response = client.post(
        "/api/friend/tag/add", json={"tag_name": "同学", "friend_ids": [2, 3]}
    )
    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["msg"] == "添加好友到标签成功"


@patch("services.friend_service.db_add_friends_to_tag", new_callable=AsyncMock)
def test_add_friends_to_tag_not_found(mock_add):
    mock_add.side_effect = Exception("db error")
    response = client.post(
        "/api/friend/tag/add", json={"tag_name": "同学", "friend_ids": [2, 3]}
    )
    assert response.status_code == 404
    assert response.json()["msg"] == "分组不存在"


@patch("services.friend_service.db_get_friends_by_tag", new_callable=AsyncMock)
def test_query_friends_by_tag_success(mock_get):
    mock_get.return_value = [
        {
            "user_id": 2,
            "username": "张三",
            "avatar_url": "http://example.com/2.jpg",
            "tag": "同学",
            "be_friend_time": "2025-03-20T10:30:00",
        }
    ]
    response = client.post("/api/friend/tag/query", json={"tag_name": "同学"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert len(data["data"]) == 1
    assert data["data"][0]["username"] == "张三"


@patch("services.friend_service.db_remove_friend_from_tag", new_callable=AsyncMock)
def test_remove_friend_from_tag_success(mock_remove):
    mock_remove.return_value = None
    response = client.post(
        "/api/friend/tag/remove", json={"tag_name": "同学", "friend_id": 2}
    )
    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["msg"] == "移出好友成功"


@patch("services.friend_service.db_remove_friend_from_tag", new_callable=AsyncMock)
def test_remove_friend_from_tag_not_found(mock_remove):
    mock_remove.side_effect = Exception("db error")
    response = client.post(
        "/api/friend/tag/remove", json={"tag_name": "同学", "friend_id": 2}
    )
    assert response.status_code == 404
    assert response.json()["msg"] == "该好友不在当前分组中"
