from fastapi import APIRouter, Depends, UploadFile, File
from api.dependencies import CurrentUserId, DBConnection, vcode_limit_check
from schemas.user import (
    UserRegister,
    RegisterResponse,
    UserLogin,
    LoginResponse,
    EmailRequest,
    EmailResponse,
    UserEdit,
    EmailEdit,
    BaseResponse,
    UserForgetPWD,
    PortraitResponse,
    UserInfoResponse,
)
from services import user_service

router = APIRouter()


# ==========================================
# 1. 注册与密码找回
# ==========================================
@router.post(
    "/register/email",
    response_model=EmailResponse,
    summary="发送注册验证码",
    dependencies=[Depends(vcode_limit_check)])
async def send_register_email(request: EmailRequest):
    return await user_service.send_register_email_service(request.email)


@router.post("/register", response_model=RegisterResponse, summary="用户注册")
async def register(user_data: UserRegister, conn: DBConnection):
    return await user_service.register_service(conn, user_data)


@router.post("/register/forgetpswdsend", response_model=EmailResponse, summary="忘记密码申请")
async def forget_password_send(request: EmailRequest, conn: DBConnection):
    return await user_service.forget_password_send_service(conn, request.email)


@router.post("/register/forgetpswdset", response_model=BaseResponse, summary="忘记密码修改")
async def forget_password_set(request: UserForgetPWD, conn: DBConnection):
    return await user_service.forget_password_set_service(conn, request)


# ==========================================
# 2. 登录、登出与注销
# ==========================================
@router.post("/login", response_model=LoginResponse, summary="用户登录")
async def login(login_data: UserLogin, conn: DBConnection):
    return await user_service.login_service(conn, login_data)


@router.post("/logout", response_model=BaseResponse, summary="用户登出")
async def logout(current_user_id: CurrentUserId):
    return await user_service.logout_service(current_user_id)


@router.post("/delete", response_model=BaseResponse, summary="用户注销")
async def delete_account(current_user_id: CurrentUserId, conn: DBConnection):
    return await user_service.delete_account_service(conn, current_user_id)


# ==========================================
# 3. 个人信息修改 (必须携带 Token)
# ==========================================
@router.put("/edit", response_model=BaseResponse, summary="修改基本信息")
async def edit_profile(edit_data: UserEdit, current_user_id: CurrentUserId, conn: DBConnection):
    return await user_service.edit_profile_service(conn, current_user_id, edit_data)


@router.put("/edit/email", response_model=BaseResponse, summary="修改邮箱")
async def edit_email(edit_data: EmailEdit, current_user_id: CurrentUserId, conn: DBConnection):
    return await user_service.edit_email_service(conn, current_user_id, edit_data)

# 【新增：修改头像的路由】


@router.put("/edit/portrait", response_model=PortraitResponse, summary="修改头像")
async def edit_portrait(
    current_user_id: CurrentUserId,
    conn: DBConnection,
    file: UploadFile = File(...)
):
    # 把连接、用户ID和文件，统统交给 service 处理
    return await user_service.edit_portrait_service(conn, current_user_id, file)


@router.get("/info", response_model=UserInfoResponse, summary="获取个人信息")
async def get_user_info(
    current_user_id: CurrentUserId,  # 只要加了这个，FastAPI 就会自动拦截没有 Token 的请求
    conn: DBConnection
):
    # 直接呼叫 Service 层干活
    return await user_service.get_user_info_service(conn, current_user_id)
