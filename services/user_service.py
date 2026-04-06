from db.repositories.user_repo import (
    db_search_users,
    db_create_user,
    db_get_user_by_email,
    db_update_user_login_time,
    db_update_user_password,
    db_update_user_profile,
    db_delete_user,
    db_get_password_by_id,
    db_get_user_by_id,
)
from db.redis_client import db_save_verification_code, db_verify_code
from core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    generate_verification_code,
)
from core.exceptions import BusinessException
from schemas.user import (
    UserSearchResult,
    UserRegister,
    RegisterResponse,
    UserLogin,
    LoginResponse,
    EmailRequest,
    UserEdit,
    EmailEdit,
    BaseResponse,
    UserForgetPWD,
)
from typing import List
from core.ws_manager import manager


async def search_users(
    db_session, keyword: str, page: int = 1, page_size: int = 20
) -> List[UserSearchResult]:
    # 直接调用 repository 层已实现的函数
    users = await db_search_users(
        db_session,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    # 如果 repository 返回的是字典列表，直接转换
    return [
        UserSearchResult(
            user_id=u["user_id"], username=u["username"], avatar_url=u.get("avatar_url")
        )
        for u in users["items"]  # <--- 重点：加上 ["items"]
    ]


async def send_register_email_service(email: str) -> BaseResponse:
    verification_code = generate_verification_code(6)
    # await smtp_send_email(email, verification_code)
    await db_save_verification_code(email, verification_code)
    return BaseResponse(code=200, msg="验证码已发送至邮箱")


async def register_service(conn, user_data: UserRegister) -> RegisterResponse:
    if not await db_verify_code(user_data.email, user_data.verification_code):
        raise BusinessException(status_code=400, detail="验证码错误")

    existing_user = await db_get_user_by_email(conn, user_data.email)
    if existing_user:
        raise BusinessException(status_code=400, detail="该邮箱已被注册")

    hashed_pw = get_password_hash(user_data.password)
    user_id = await db_create_user(conn, user_data.username, hashed_pw, user_data.email)

    return RegisterResponse(code=200, id=user_id)


async def forget_password_send_service(conn, email: str) -> BaseResponse:
    user = await db_get_user_by_email(conn, email)
    if not user:
        raise BusinessException(status_code=404, detail="未找到绑定该邮箱的账号")
    verification_code = generate_verification_code(6)
    # await smtp_send_email(email, verification_code)
    await db_save_verification_code(email, verification_code)
    return BaseResponse(code=200, msg="密码找回邮件已发送")


async def forget_password_set_service(conn, request: UserForgetPWD) -> BaseResponse:
    if not await db_verify_code(request.email, request.verification_code):
        raise BusinessException(status_code=400, detail="验证码错误")
    new_password_hash = get_password_hash(request.password)
    user = await db_get_user_by_email(conn, request.email)
    if user is None:
        raise BusinessException(status_code=404, detail="未找到绑定该邮箱的账号")
    await db_update_user_password(conn, user["user_id"], new_password_hash)
    return BaseResponse(code=200, msg="密码修改完毕，请重新登陆")


async def login_service(conn, login_data: UserLogin) -> LoginResponse:
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

    hashed_pwd = user["password"]
    if not hashed_pwd:
        raise BusinessException(status_code=400, detail="账号数据异常，请联系管理员")
    
    if not verify_password(login_data.password, hashed_pwd):
        raise BusinessException(status_code=400, detail="密码错误")

    await db_update_user_login_time(conn, user["user_id"])
    access_token = create_access_token(data={"sub": str(user["user_id"])})

    return LoginResponse(code=200, token=access_token)


async def logout_service(current_user_id: int) -> BaseResponse:
    await manager.disconnect(current_user_id)
    return BaseResponse(code=200, msg="登出成功")


async def delete_account_service(conn, current_user_id: int) -> BaseResponse:
    await db_delete_user(conn, current_user_id)
    return BaseResponse(code=200, msg="账号已彻底注销")


async def edit_profile_service(
    conn, current_user_id: int, edit_data: UserEdit
) -> BaseResponse:
    if edit_data.old_password and edit_data.new_password:
        hashed_pwd = await db_get_password_by_id(conn, current_user_id)
        if not hashed_pwd:
            raise BusinessException(status_code=400, detail="账号数据异常，请联系管理员")
        if not verify_password(edit_data.old_password, hashed_pwd):
            raise BusinessException(status_code=400, detail="密码错误")
        hashed_new = get_password_hash(edit_data.new_password)
        await db_update_user_password(conn, current_user_id, hashed_new)

    if edit_data.user_name or edit_data.email:
        await db_update_user_profile(
            conn, current_user_id, username=edit_data.user_name, email=edit_data.email
        )
    return BaseResponse(code=200, msg="信息修改成功")


async def edit_email_service(
    conn, current_user_id: int, edit_data: EmailEdit
) -> BaseResponse:
    success = await db_update_user_profile(
        conn, current_user_id, email=edit_data.new_email
    )
    if not success:
        raise BusinessException(status_code=400, detail="邮箱更新失败")
    return BaseResponse(code=200, msg="邮箱修改成功")
