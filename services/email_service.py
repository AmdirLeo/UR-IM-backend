# backend/services/email_service.py
import aiosmtplib
from email.message import EmailMessage

# 注意：这里的导入路径可能需要根据你们项目的实际结构微调
# 如果你的 config.py 在 backend/core/ 目录下，可能需要改成 from backend.core.config import settings
from core.config import settings 

async def smtp_send_email(to_email: str, code: str) -> bool:
    """
    异步发送注册验证码邮件
    :param to_email: 目标用户的邮箱地址
    :param code: 6位或4位数字验证码
    :return: 发送成功返回 True,失败返回 False
    """
    message = EmailMessage()
    message["From"] = settings.MAIL_USERNAME
    message["To"] = to_email
    message["Subject"] = "UR-IM 注册验证码"
    
    # 邮件正文内容
    message.set_content(
        f"欢迎注册 UR-IM!\n\n"
        f"您的注册验证码是：{code}\n"
        f"请在 5 分钟内输入。如果非本人操作，请忽略此邮件。"
    )

    try:
        # 使用 aiosmtplib 异步发送，避免阻塞 FastAPI 主线程
        await aiosmtplib.send(
            message,
            hostname=settings.MAIL_SERVER,
            port=settings.MAIL_PORT,
            username=settings.MAIL_USERNAME,
            password=settings.MAIL_PASSWORD,
            use_tls=True  # 现代邮箱服务通常强制要求 TLS 加密
        )
        return True
    except Exception as e:
        # 捕获异常，防止邮件发送失败导致整个程序崩溃
        print(f"❌ 邮件发送给 {to_email} 失败, 错误信息: {e}")
        # 实际生产环境中，建议换成 logging.error(f"...")
        return False
