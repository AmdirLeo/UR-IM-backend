from enum import Enum
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging

# ==========================================
# 1. 自定义业务异常类
# ==========================================
class BusinessException(Exception):
    """
    业务逻辑异常。
    当你的代码中遇到由于业务规则导致的错误时（例如：密码错误、用户不存在），
    直接 raise BusinessException(status_code=400, detail="密码错误")
    """
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
class UserErrors(Enum):
    NotFound = "NotFound"
    AlreadyExists = "AlreadyExists"
    AuthFailed = "AuthFailed"
    InvalidVerifyCode = "InvalidVerifyCode"
    NoUpdateFields = "NoUpdateFields"

class FriendErrors(Enum):
    CantAddSelf = "CantAddSelf"
    AlreadyFriends = "AlreadyFriends"
    RequestPending = "RequestPending"
    InvalidAction = "InvalidAction"
    RequestNotFound = "RequestNotFound"
    Unauthorized = "Unauthorized"
    FriendNotFound = "FriendNotFound"
    TagAlreadyExists = "TagAlreadyExists"
    TagNotFound = "TagNotFound"
    NotInTag = "NotInTag"

# ==========================================
# 2. 定义业务异常类
# ==========================================
class UserException(Exception):
    def __init__(self, error_code: UserErrors):
        self.error_code = error_code

class FriendException(Exception):
    def __init__(self, error_code: FriendErrors):
        self.error_code = error_code
# ==========================================
# 2. 全局异常注册函数
# ==========================================
def setup_exception_handlers(app):
    
    # 捕获我们自定义的业务异常
    @app.exception_handler(BusinessException)
    @app.exception_handler(UserException)
    async def user_exception_handler(request: Request, exc: UserException):
        error_mapping = {
            UserErrors.NotFound: (404, "用户不存在"),
            UserErrors.AlreadyExists: (409, "该邮箱已被注册"),
            UserErrors.AuthFailed: (401, "邮箱或密码错误"),
            UserErrors.InvalidVerifyCode: (400, "验证码错误或已失效"),
            UserErrors.NoUpdateFields: (400, "没有任何字段需要更新"),
        }
        status_code, detail = error_mapping.get(exc.error_code, (500, "用户模块未知错误"))
        return JSONResponse(status_code=status_code, content={"code": status_code, "msg": detail, "data": None})

    # 捕获好友模块异常
    @app.exception_handler(FriendException)
    async def friend_exception_handler(request: Request, exc: FriendException):
        error_mapping = {
            FriendErrors.CantAddSelf: (400, "不能添加自己为好友"),
            FriendErrors.AlreadyFriends: (409, "你们已经是好友了，无需重复添加"),
            FriendErrors.RequestPending: (409, "已有待处理的好友申请，请耐心等待或前往处理"),
            FriendErrors.InvalidAction: (400, "无效的操作类型"),
            FriendErrors.RequestNotFound: (404, "好友申请不存在或已被处理"),
            FriendErrors.Unauthorized: (403, "越权操作：无权处理他人的好友申请"),
            FriendErrors.FriendNotFound: (404, "好友关系不存在"),
            FriendErrors.TagAlreadyExists: (409, "该分组已存在"),
            FriendErrors.TagNotFound: (404, "分组不存在"),
            FriendErrors.NotInTag: (404, "该好友不在当前分组中"),
        }
        status_code, detail = error_mapping.get(exc.error_code, (500, "好友模块未知错误"))
        return JSONResponse(status_code=status_code, content={"code": status_code, "msg": detail, "data": None})
    
    # 捕获 FastAPI 原生的参数校验异常 (Pydantic 报错)
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # 提取 Pydantic 返回的第一个错误信息，将其扁平化，变得人类可读
        errors = exc.errors()
        if errors:
            # errors[0]['loc'] 通常长这样: ('body', 'password')
            field = errors[0]['loc'][-1] 
            msg = errors[0]['msg']
            error_detail = f"参数 '{field}' 校验失败: {msg}"
        else:
            error_detail = "数据格式错误"
            
        return JSONResponse(
            status_code=422,
            content={
                "code": 422, 
                "msg": error_detail, 
                "data": None
            }
        )

    # 捕获所有未知的系统级崩溃 (兜底)
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # 真实项目中这里应该接入日志系统 (如 Sentry)
        logging.error(f"未捕获的系统异常: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "code": 500, 
                "msg": "服务器内部错误，请稍后再试", 
                "data": None
            }
        )