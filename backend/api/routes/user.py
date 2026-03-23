from fastapi import APIRouter, Depends, UploadFile, File
from api.dependencies import get_current_user_id
from schemas.user import (
    UserRegister, RegisterResponse, UserLogin, LoginResponse, 
    EmailRequest, UserEdit, EmailEdit, BaseResponse
)
from core.exceptions import BusinessException

router = APIRouter()

# ==========================================
# 1. 注册与密码找回
# ==========================================
@router.post("/register/email", response_model=BaseResponse, summary="发送注册验证码")
async def send_register_email(request: EmailRequest):
    # TODO: 接入 SMTP 生成并发送验证码，存入 Redis
    return BaseResponse(code=200, msg="验证码已发送至邮箱")

@router.post("/register", response_model=RegisterResponse, summary="用户注册")
async def register(user_data: UserRegister):
    # TODO: 校验 Redis 中的验证码
    if user_data.verification_code != "123456": # Mock
        raise BusinessException(status_code=400, detail="验证码错误")
        
    # TODO: 校验邮箱/用户名是否重复
    # TODO: 哈希密码 -> INSERT INTO user_account -> db.refresh(new_user) 拿自增 ID
    
    # 模拟数据库生成的自增 ID (如：第一位用户是 1，第二位是 2...)
    mock_db_generated_id = 1 
    return RegisterResponse(code=200, id=mock_db_generated_id)

@router.post("/register/forgetpswd", response_model=BaseResponse, summary="忘记密码")
async def forget_password(request: EmailRequest):
    # TODO: 校验邮箱，发送重置邮件
    return BaseResponse(code=200, msg="密码找回邮件已发送")

# ==========================================
# 2. 登录、登出与注销
# ==========================================
@router.post("/login", response_model=LoginResponse, summary="用户登录")
async def login(login_data: UserLogin):
    # TODO: SELECT * FROM user_account WHERE id = login_data.id
    # TODO: 校验密码 verify_password(login_data.password, db_user.password)
    # TODO: 触发拉取未读消息逻辑
    
    if login_data.password == "wrong": # Mock 错误
        raise BusinessException(status_code=400, detail="密码不一致或id不存在")
        
    # TODO: 签发真实 JWT Token
    mock_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    return LoginResponse(code=200, token=mock_token)

@router.post("/logout", response_model=BaseResponse, summary="用户登出")
async def logout(current_user_id: int = Depends(get_current_user_id)):
    # 业务逻辑：前端清除 Token，后端可选加入 Redis 黑名单
    return BaseResponse(code=200, msg=f"用户 {current_user_id} 登出成功")

@router.post("/delete", response_model=BaseResponse, summary="用户注销")
async def delete_account(current_user_id: int = Depends(get_current_user_id)):
    # TODO: 执行 DELETE 或 UPDATE is_deleted=1，级联清理聊天记录
    return BaseResponse(code=200, msg=f"账号 {current_user_id} 已彻底注销")

# ==========================================
# 3. 个人信息修改 (必须携带 Token)
# ==========================================
@router.put("/edit", response_model=BaseResponse, summary="修改基本信息")
async def edit_profile(
    edit_data: UserEdit, 
    current_user_id: int = Depends(get_current_user_id)
):
    # TODO: 校验旧密码 (如果有)
    # TODO: UPDATE user_account SET ...
    return BaseResponse(code=200, msg="信息修改成功")

@router.put("/edit/email", response_model=BaseResponse, summary="修改邮箱")
async def edit_email(
    edit_data: EmailEdit, 
    current_user_id: int = Depends(get_current_user_id)
):
    # TODO: 校验 edit_data.password
    # TODO: UPDATE user_account SET email = edit_data.new_email
    return BaseResponse(code=200, msg="邮箱修改成功")

@router.put("/edit/portrait", summary="修改头像")
async def edit_portrait(
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id)
):
    # TODO: 校验图片格式，上传至对象存储 (OSS/S3)
    # TODO: 获取 URL/filekey 和宽高尺寸，写入数据库
    
    return {
        "code": 200, 
        "filekey": f"avatar_{current_user_id}_{file.filename}",
        "width": 1024,
        "height": 1024
    }