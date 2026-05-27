# core/s3_client.py
import json
from minio import Minio
from core.config import settings
import os
from core.exceptions import BusinessException
from fastapi import UploadFile
import uuid
import io

# 1. 初始化全局唯一的 MinIO 客户端
# 这里的配置自动分流：本地看齐 .env (localhost:9000)，线上使用写死的 SECoder 内网域名
s3_client = Minio(
    endpoint=settings.S3_ENDPOINT,
    access_key=settings.S3_ACCESS_KEY,
    secret_key=settings.S3_SECRET_KEY,
    secure=settings.S3_SECURE
)


def init_s3_buckets():
    """
    系统启动时由 main.py 调用的初始化钩子：
    1. 自动检查并创建预设的头像桶与聊天文件桶（如果已存在则跳过）
    2. 自动为头像存储桶（avatars）刷新匿名公开可读策略
    """
    try:
        # 从配置中心读取桶清单
        buckets = [settings.BUCKET_AVATAR, settings.BUCKET_CHAT]

        for bucket in buckets:
            # 幂等性检查：如果桶已经存在（比如你之前脚本建好的），直接跳过创建，不会报错
            if not s3_client.bucket_exists(bucket):
                s3_client.make_bucket(bucket)
                print(f"[MinIO] 成功自动创建存储桶: {bucket}")
            else:
                print(f"[MinIO] 存储桶 '{bucket}' 已存在，跳过创建步骤")

        # 🌟 重新刷新策略：确保头像桶（avatars）拥有完美的匿名公开可读权限
        public_read_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": f"arn:aws:s3:::{settings.BUCKET_AVATAR}/*"
                }
            ]
        }

        # 将构造好的 JSON 策略重新刷入 MinIO
        s3_client.set_bucket_policy(
            settings.BUCKET_AVATAR,
            json.dumps(public_read_policy)
        )
        print(f"[MinIO] 头像存储桶 '{settings.BUCKET_AVATAR}' 公开只读策略同步成功！")

    except Exception as e:
        print(f"[MinIO] 存储桶基础设施初始化失败，错误详情: {e}")
        # 非本地调试模式下（如线上部署），如果连接存储失败应当抛出异常中断启动，防止带病运行
        if not settings.DEBUG:
            raise e


async def upload_image_to_s3(
    file: UploadFile,
    bucket_name: str,
    max_size: int = 2 * 1024 * 1024,  # 默认 2MB
    err_msg_prefix: str = "头像"
) -> tuple[str, str]:
    """
    通用公网图片流式上传服务
    :return: (object_name, public_url) 用于后续的数据库操作和逆向擦除
    """
    # 1. 校验文件大小
    if file.size > max_size:
        raise BusinessException(
            status_code=400, detail=f"{err_msg_prefix}图片大小不能超过 {max_size // (1024 * 1024)}MB")

    # 2. 校验后缀名
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    if ext not in allowed_extensions:
        raise BusinessException(status_code=400, detail="不支持的图片格式")

    # 3. 生成唯一的 Object Key
    object_name = f"{uuid.uuid4().hex}{ext}"

    # 4. 读取二进制流
    file_bytes = await file.read()
    file_stream = io.BytesIO(file_bytes)

    # 5. 上传至 MinIO
    try:
        if not s3_client.bucket_exists(bucket_name):
            s3_client.make_bucket(bucket_name)

        s3_client.put_object(
            bucket_name=bucket_name,
            object_name=object_name,
            data=file_stream,
            length=len(file_bytes),
            content_type=file.content_type
        )
    except Exception:
        raise BusinessException(
            status_code=500,
            detail=f"{err_msg_prefix}文件保存至云存储失败")

    # 6. 拼接公网 URL
    protocol = "https" if settings.S3_SECURE else "http"
    public_url = f"{protocol}://{settings.S3_PUBLIC_DOMAIN}/{bucket_name}/{object_name}"

    return object_name, public_url
