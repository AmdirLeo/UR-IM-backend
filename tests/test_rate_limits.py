import pytest
import asyncio
from httpx import AsyncClient, ASGITransport

# 导入你的 FastAPI app 和 Redis 客户端
from main import app
from db.redis_client import redis_client

API_URL = "/api/user/register/email"

# ==========================================
# 异步夹具 (Fixture)：自动清理 Redis 环境
# ==========================================


@pytest.fixture(autouse=True)
async def cleanup_redis_limits():
    """
    原生异步夹具，不再需要 asyncio.run() 这种强硬手段
    """
    # 运行测试前
    yield

    # 运行测试后清理
    keys_to_delete = [
        "limit:vcode:ci_test_user@test.com",
        "limit:api:/register/email:192.168.1.100",
        "limit:global:192.168.1.100"
    ]
    for i in range(1, 6):
        keys_to_delete.append(f"limit:vcode:ci_concurrent_{i}@test.com")
        keys_to_delete.append(f"limit:api:/register/email:192.168.1.100")

    if keys_to_delete:
        await redis_client.delete(*keys_to_delete)
        print("\n[Fixture] 测试清理完成：已清除 Redis 限流 Key")


# ==========================================
# 测试用例 1：第三层 业务限流 (1次/60秒)
# ==========================================
@pytest.mark.asyncio
async def test_layer_3_business_limit():
    email = "ci_test_user@test.com"

    # 使用 httpx 的 AsyncClient 作为测试客户端
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. 第一次请求：应该成功 (返回 200)
        resp1 = await client.post(API_URL, json={"email": email})
        assert resp1.status_code == 200, "第一次请求预期成功，但失败了"

        # 2. 第二次请求：应该被 VCodeLimit 拦截 (返回 429)
        resp2 = await client.post(API_URL, json={"email": email})
        assert resp2.status_code == 429, "第二次请求预期被拦截，但放行了"
        assert "频繁" in resp2.text, f"拦截提示信息不匹配，实际返回: {resp2.text}"


# ==========================================
# 测试用例 2：第二层 单接口并发限流 (2次/1秒)
# ==========================================
@pytest.mark.asyncio
async def test_layer_2_api_limit():
    emails = [f"ci_concurrent_{i}@test.com" for i in range(1, 6)]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 创建 5 个并发的异步任务，而不是使用多线程
        tasks = []
        for email in emails:
            tasks.append(
                client.post(
                    API_URL,
                    json={"email": email},
                    headers={"X-Real-IP": "192.168.1.100"}
                )
            )

        # asyncio.gather 会瞬间同时发出这 5 个请求！真正的并发！
        results = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in results]

    success_count = status_codes.count(200)
    blocked_count = status_codes.count(429)

    # 因为并发发出，哪个成功哪个失败不确定，但总的成功数绝对不能超过 2
    assert success_count <= 2, f"并发限流失效！实际成功了 {success_count} 次"
    assert blocked_count >= 3, f"并发拦截异常！实际只拦截了 {blocked_count} 次"
