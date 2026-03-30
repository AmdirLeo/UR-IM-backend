from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from core.exceptions import setup_exception_handlers, BusinessException, FriendException, FriendErrors
from fastapi.exceptions import RequestValidationError

# 1. 构造测试专用的微型应用并注册异常处理器
app = FastAPI()
setup_exception_handlers(app)

# 2. 定义用于触发各类异常的测试路由
@app.get("/test-business-error")
async def trigger_business_error():
    raise BusinessException(status_code=400, detail="业务逻辑错误测试")

# 专门用于触发空错误列表的路由
@app.get("/test-empty-validation-error")
async def trigger_empty_validation_error():
    # 强行抛出一个没有错误详情的异常
    raise RequestValidationError(errors=[])

@app.get("/test-friend-error-self")
async def trigger_friend_error_self():
    raise FriendException(error_code=FriendErrors.CantAddSelf)

@app.get("/test-friend-error-already")
async def trigger_friend_error_already():
    raise FriendException(error_code=FriendErrors.AlreadyFriends)

@app.get("/test-friend-error-pending")
async def trigger_friend_error_pending():
    raise FriendException(error_code=FriendErrors.RequestPending)

@app.get("/test-friend-error-invalid-action")
async def trigger_friend_error_invalid_action():
    raise FriendException(error_code=FriendErrors.InvalidAction)

@app.get("/test-friend-error-not-found")
async def trigger_friend_error_not_found():
    raise FriendException(error_code=FriendErrors.RequestNotFound)

@app.get("/test-friend-error-unauthorized")
async def trigger_friend_error_unauthorized():
    raise FriendException(error_code=FriendErrors.Unauthorized)

class MockSchema(BaseModel):
    # 要求 name 至少 5 个字符，用于触发 Pydantic 校验错误
    name: str = Field(..., min_length=5)

@app.post("/test-validation-error")
async def trigger_validation_error(data: MockSchema):
    return data

@app.get("/test-server-error")
async def trigger_server_error():
    raise ValueError("模拟的底层代码崩溃")

# 3. 初始化测试客户端
client = TestClient(app, raise_server_exceptions=False)

# 4. 编写具体的测试用例
def test_business_exception_handler():
    response = client.get("/test-business-error")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == 400
    assert data["msg"] == "业务逻辑错误测试"
    assert data["data"] is None

def test_validation_exception_handler():
    # 发送长度仅为 3 的 name，故意触发 422 校验失败
    response = client.post("/test-validation-error", json={"name": "abc"})
    assert response.status_code == 422
    data = response.json()
    assert data["code"] == 422
    assert "校验失败" in data["msg"]
    assert "name" in data["msg"]
    assert data["data"] is None

def test_global_exception_handler():
    response = client.get("/test-server-error")
    assert response.status_code == 500
    data = response.json()
    assert data["code"] == 500
    assert data["msg"] == "服务器内部错误，请稍后再试"
    assert data["data"] is None

def test_empty_validation_exception_handler():
    response = client.get("/test-empty-validation-error")
    assert response.status_code == 422
    data = response.json()
    assert data["code"] == 422
    # 完美命中我们要测试的 else 分支！
    assert data["msg"] == "数据格式错误" 
    assert data["data"] is None

def test_friend_exception_handler_self():
    response = client.get("/test-friend-error-self")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == 400
    assert data["msg"] == "不能添加自己为好友"
    assert data["data"] is None

def test_friend_exception_handler_already():
    response = client.get("/test-friend-error-already")
    assert response.status_code == 409
    data = response.json()
    assert data["code"] == 409
    assert data["msg"] == "你们已经是好友了，无需重复添加"
    assert data["data"] is None

def test_friend_exception_handler_pending():
    response = client.get("/test-friend-error-pending")
    assert response.status_code == 409
    data = response.json()
    assert data["code"] == 409
    assert data["msg"] == "已有待处理的好友申请，请耐心等待或前往处理"
    assert data["data"] is None

def test_friend_exception_handler_invalid_action():
    response = client.get("/test-friend-error-invalid-action")
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == 400
    assert data["msg"] == "无效的操作类型"
    assert data["data"] is None

def test_friend_exception_handler_not_found():
    response = client.get("/test-friend-error-not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["code"] == 404
    assert data["msg"] == "好友申请不存在或已被处理"
    assert data["data"] is None
    
def test_friend_exception_handler_unauthorized():
    response = client.get("/test-friend-error-unauthorized")
    assert response.status_code == 403
    data = response.json()
    assert data["code"] == 403
    assert data["msg"] == "越权操作：无权处理他人的好友申请"
    assert data["data"] is None