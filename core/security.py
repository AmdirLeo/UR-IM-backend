from datetime import datetime, timedelta, timezone
import jwt
import bcrypt
from core.config import settings
import secrets


def generate_verification_code(length: int = 6) -> str:
    """
    生成指定长度的纯数字随机验证码。
    使用 secrets 模块保证密码学安全，防止随机数种子被暴力破解。
    """
    # 从 0-9 中安全地随机挑选字符，循环 6 次并拼接成字符串
    return "".join(secrets.choice("0123456789") for _ in range(length))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    校验明文密码与哈希值是否匹配。

    调用示例:
        # 从数据库中取出已加密的密码
        db_hashed_password = user.password

        # 验证前端传来的明文密码
        is_valid = verify_password("user_input_123", db_hashed_password)
        if not is_valid:
            raise HTTPException(status_code=400, detail="密码错误")
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    """
    生成密码的 bcrypt 哈希值。

    调用示例:
        # 接收前端传来的明文密码进行加密
        hashed_pw = get_password_hash("user_input_123")

        # 随后将 hashed_pw 保存至数据库的 password 字段
        db_user = User(username="allan", password=hashed_pw)
    """
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed_bytes.decode("utf-8")


def create_access_token(
        data: dict,
        expires_delta: timedelta | None = None) -> str:
    """
    生成 JWT Token。

    调用示例 1 (使用 config.py 中的默认过期时间):
        token = create_access_token(data={"sub": str(user.user_id)})

    调用示例 2 (自定义该 token 的过期时间为 30 分钟):
        expire_time = timedelta(minutes=30)
        token = create_access_token(data={"sub": str(user.user_id)}, expires_delta=expire_time)

    返回结果可以直接作为 API 的响应内容:
        return {"access_token": token, "token_type": "bearer"}
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(
            timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm="HS256")
    return encoded_jwt
