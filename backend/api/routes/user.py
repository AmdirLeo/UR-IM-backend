from fastapi import APIRouter, Depends, UploadFile, File
from api.dependencies import CurrentUserId, DBConnection
from core.ws_manager import manager
from core.security import (
    get_password_hash, verify_password, create_access_token,
    generate_verification_code
)
from schemas.user import (
    UserRegister, RegisterResponse, UserLogin, LoginResponse, 
    EmailRequest, UserEdit, EmailEdit, BaseResponse, UserForgetPWD
)
from core.exceptions import BusinessException
from db.repositories.user_repo import (
    db_get_user_by_email, db_create_user, db_get_user_by_email,
    db_get_user_by_email, db_update_user_login_time, db_update_user_password,
    db_update_user_profile, db_delete_user, db_get_password_by_id,
    db_get_user_by_id
)
from db.redis_client import db_save_verification_code, db_get_verification_code
from core.smtp import smtp_send_email

router = APIRouter()

# ==========================================
# 1. 注册与密码找回
# ==========================================
@router.post("/register/email", response_model=BaseResponse, summary="发送注册验证码")
async def send_register_email(request: EmailRequest):
    verification_code = generate_verification_code(6)
    await smtp_send_email(request.email, verification_code)
    await db_save_verification_code(request.email, verification_code)
    return BaseResponse(code=200, msg="验证码已发送至邮箱")

@router.post("/register", response_model=RegisterResponse, summary="用户注册")
async def register(user_data: UserRegister, conn: DBConnection):
    verification_code = await db_get_verification_code(user_data.email)
    if user_data.verification_code != verification_code:
        raise BusinessException(status_code=400, detail="验证码错误")
        
    # 2. 检查邮箱是否已被注册
    existing_user = await db_get_user_by_email(conn, user_data.email)
    if existing_user:
        raise BusinessException(status_code=400, detail="该邮箱已被注册")
        
    # 3. 密码哈希与入库
    hashed_pw = get_password_hash(user_data.password)
    user_id = await db_create_user(conn, user_data.username, hashed_pw, user_data.email)
    
    return RegisterResponse(code=200, id=user_id)

@router.post("/register/forgetpswdsend", response_model=BaseResponse, summary="忘记密码申请")
async def forget_password_send(request: EmailRequest, conn: DBConnection):
    # 1. 检查用户是否存在
    user = await db_get_user_by_email(conn, request.email)
    if not user:
        raise BusinessException(status_code=404, detail="未找到绑定该邮箱的账号")
    verification_code = generate_verification_code(6)
    await smtp_send_email(request.email, verification_code)
    await db_save_verification_code(request.email, verification_code)  
    return BaseResponse(code=200, msg="密码找回邮件已发送")

@router.post("/register/forgetpswdset", response_model=BaseResponse, summary="忘记密码修改")
async def forget_password_set(request: UserForgetPWD, conn: DBConnection):
    verification_code = await db_get_verification_code(request.email)
    if request.verification_code != verification_code:
        raise BusinessException(status_code=400, detail="验证码错误")
    new_password_hash = get_password_hash(request.password)
    user = await db_get_user_by_email(conn, request.email)
    await db_update_user_password(conn, user["user_id"], new_password_hash)
    return BaseResponse(code=200, msg="密码修改完毕，请重新登陆")

# ==========================================
# 2. 登录、登出与注销
# ==========================================
@router.post("/login", response_model=LoginResponse, summary="用户登录")
async def login(login_data: UserLogin, conn: DBConnection):
    user = None
    if "@" in login_data.id:
        user = await db_get_user_by_email(conn, login_data.id)
    else:
        user_id = int(login_data.id)
        user = await db_get_user_by_id(conn, user_id)
        if user:
            user["password"] = await db_get_password_by_id(conn, user_id)

    if not user:
        raise BusinessException(status_code=400, detail="账号不存在")

    if not verify_password(login_data.password, user["password"]):
        raise BusinessException(status_code=400, detail="密码错误")
        
    await db_update_user_login_time(conn, user["user_id"])
    access_token = create_access_token(data={"sub": str(user["user_id"])})
    
    return LoginResponse(code=200, token=access_token)

@router.post("/logout", response_model=BaseResponse, summary="用户登出")
async def logout(current_user_id: CurrentUserId):
    # JWT 无状态，前端应自行清除 Token。
    await manager.disconnect(current_user_id)
    return BaseResponse(code=200, msg="登出成功")

@router.post("/delete", response_model=BaseResponse, summary="用户注销")
async def delete_account(
    current_user_id: CurrentUserId,
    conn: DBConnection
):
    # 执行数据库删除
    success = await db_delete_user(conn, current_user_id)
    if not success:
        raise BusinessException(status_code=404, detail="账号不存在")
        
    return BaseResponse(code=200, msg="账号已彻底注销")

# ==========================================
# 3. 个人信息修改 (必须携带 Token)
# ==========================================
@router.put("/edit", response_model=BaseResponse, summary="修改基本信息")
async def edit_profile(
    edit_data: UserEdit, 
    current_user_id: CurrentUserId,
    conn: DBConnection
):
    # 如果用户想修改密码
    if edit_data.old_password and edit_data.new_password:
        hashed_pwd = await db_get_password_by_id(conn, current_user_id)
        if not verify_password(edit_data.old_password, hashed_pwd):
            raise BusinessException(status_code=400, detail="密码错误")
        hashed_new = get_password_hash(edit_data.new_password)
        await db_update_user_password(conn, current_user_id, hashed_new)
        
    # 如果用户修改了用户名或邮箱
    if edit_data.user_name or edit_data.email:
        await db_update_user_profile(
            conn, 
            current_user_id, 
            username=edit_data.user_name, 
            email=edit_data.email
        )
        
    return BaseResponse(code=200, msg="信息修改成功")

@router.put("/edit/email", response_model=BaseResponse, summary="修改邮箱")
async def edit_email(
    edit_data: EmailEdit, 
    current_user_id: CurrentUserId,
    conn: DBConnection
):
    success = await db_update_user_profile(conn, current_user_id, email=edit_data.new_email)
    if not success:
        raise BusinessException(status_code=400, detail="邮箱更新失败")
        
    return BaseResponse(code=200, msg="邮箱修改成功")

'''
@router.put("/edit/portrait", summary="修改头像")
async def edit_portrait(
    file: UploadFile = File(...),
    current_user_id: CurrentUserId,
    conn: DBConnection
):
    # TODO: 接入图片对象存储 (OSS/S3)
    mock_avatar_url = f"https://mock-oss.com/avatars/{current_user_id}_{file.filename}"
    
    # 写入数据库
    await db_update_user_profile(conn, current_user_id, avatar_url=mock_avatar_url)
    
    return {
        "code": 200, 
        "filekey": mock_avatar_url,
        "width": 1024,
        "height": 1024
    }
'''