from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from pydantic import Field


class Settings(BaseSettings):
    # --- 基础配置 ---
    PROJECT_NAME: str = "UR-IM"
    VERSION: str = "0.1.0"
    DEBUG: bool = True

    # --- 数据库配置 ---
    # Pydantic 会自动从 .env 中读取 DATABASE_URL 变量
    DATABASE_URL: str = Field(
        default="postgresql://postgres:123456@ur_im_db.Default.secoder.local:5432/postgres")

    # 🌟 Redis 也是同理
    REDIS_URL: str = Field(
        default="redis://im_redis.Default.secoder.local:6379/0"
    )

    # 🌟 --- MinIO / S3 分布式存储配置 ---
    # 默认 Endpoint 写死 SECoder 截图里的内网域名和 9000 端口
    S3_ENDPOINT: str = Field(
        default="im-minio.default.secoder.local:80"
    )
    # 账号密码与你定制的 MinIO 镜像保持严格一致
    S3_ACCESS_KEY: str = "IM_Admin_8w2P"
    S3_SECRET_KEY: str = "X9vM4cK7zL1qN5bY"
    S3_SECURE: bool = False  # 内网 http 通信用 False

    # 存储桶规划
    BUCKET_AVATAR: str = "avatars"
    BUCKET_CHAT: str = "chatfiles"

    # --- 安全与 JWT 配置 ---
    JWT_SECRET_KEY: str = Field(
        default="ur_im_super_secret_key_2026_secoder_bypass"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 默认为30分钟

    # --- 邮件服务配置 (可选) ---
    MAIL_USERNAME: Optional[str] = None
    MAIL_PASSWORD: Optional[str] = None
    MAIL_SERVER: Optional[str] = None
    MAIL_PORT: Optional[int] = 587

    # 配置 Pydantic 读取 .env 文件
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 中多余的变量
    )


# 实例化全局配置对象
settings = Settings()
