import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from core.config import settings
from core.exceptions import BusinessException

# 声明前端携带 Token 的标准方式：在 HTTP Header 中使用 Authorization: Bearer <token>
# 这里的 tokenUrl 只是给 Swagger UI 测试用的提示，告诉它去哪里换取 Token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

async def get_current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    """
    全局 Token 拦截与解析依赖。
    如果 Token 合法，返回解密后的 user_id；如果非法或过期，直接抛出全局 401 异常拦截请求。
    """
    try:
        # 使用你在 security.py 中配置的同一个密钥和算法进行解密
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[getattr(settings, "ALGORITHM", "HS256")]
        )
        
        # 提取之前在 create_access_token 中存入的 "sub" 字段
        user_id_str = payload.get("sub")
        
        if user_id_str is None:
            raise BusinessException(status_code=401, detail="无效的凭证载荷")
            
        return int(user_id_str)
        
    except jwt.ExpiredSignatureError:
        # 捕获 Token 过期异常
        raise BusinessException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        # 捕获 Token 签名错误、被篡改或格式错误等异常
        raise BusinessException(status_code=401, detail="无效的身份凭证")