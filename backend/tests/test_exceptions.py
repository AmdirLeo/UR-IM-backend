from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from core.exceptions import setup_exception_handlers, BusinessException

# 1. 构造测试专用的微型应用并注册异常处理器
app = FastAPI()
setup_exception_handlers(app)

# 2. 定义用于触发各类异常的测试路由
@app.get("/test-business-error")
async def trigger_business_error():
    raise BusinessException(status_code=400, detail="业务逻辑错误测试")

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