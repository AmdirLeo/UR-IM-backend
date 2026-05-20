# core/s3_client.py
import json
from minio import Minio
from core.config import settings

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
