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
    UsernameEdit,
    PasswordEdit,
    EmailResponse,
    EmailEdit,
    BaseResponse,
    UserForgetPWD,
    PortraitResponse,
    UserInfoResponse,
)
from db.repositories.group_repo import (
    db_get_owned_groups,
    db_disband_all_owned_groups,
    db_clean_group_invites,
)
from schemas.message import SendMessageRequest
from typing import List
from core.ws_manager import manager
import os
import uuid
import shutil
from fastapi import UploadFile
from services.message_service import send_message_service
from db.repositories.friend_repo import db_remove_friend
from core.s3_client import s3_client
from core.config import settings
import io


async def search_users(
        db_session,
        keyword: str,
        page: int = 1,
        page_size: int = 20) -> List[UserSearchResult]:
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
            user_id=u["user_id"], username=u["username"], avatar_url=u.get("avatar_url"))
        for u in users["items"]  # <--- 重点：加上 ["items"]
    ]


async def send_register_email_service(email: str) -> EmailResponse:
    v_code = generate_verification_code(6)
    # await smtp_send_email(email, verification_code)
    await db_save_verification_code(email, v_code)
    return EmailResponse(code=200, verification_code=v_code)


async def register_service(conn, user_data: UserRegister) -> RegisterResponse:
    if not await db_verify_code(user_data.email, user_data.verification_code):
        raise BusinessException(status_code=400, detail="验证码错误")

    existing_user = await db_get_user_by_email(conn, user_data.email)
    if existing_user:
        raise BusinessException(status_code=400, detail="该邮箱已被注册")

    hashed_pw = get_password_hash(user_data.password)
    async with conn.transaction():
        user_id = await db_create_user(conn, user_data.username, hashed_pw, user_data.email)

        # 2. 创建与系统通知助手 (-1) 的会话
        conv_notify_id = await conn.fetchval(
            "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;"
        )

        # 把刚注册的新用户 (user_id) 和系统助手 (-1) 拉进这个会话
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, -1);",
            conv_notify_id,
            user_id,
        )

        # 3. 创建与群聊助手 (-2) 的会话
        conv_group_id = await conn.fetchval(
            "INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;"
        )

        # 把刚注册的新用户 (user_id) 和群聊助手 (-2) 拉进这个会话
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, -2);",
            conv_group_id,
            user_id,
        )

        # 4. 发送欢迎消息
        system_req = SendMessageRequest(
            conversation_id=conv_notify_id,
            message_content="欢迎来到 UR-IM！我是你的系统小助手。有关好友申请的处理结果等重要通知，都会在这里显示。",
            msg_type="text",
            local_id=str(uuid.uuid4())  # 后端自己随便生成一个临时 ID 骗过校验即可
        )

        # 调用发消息服务。注意这里的发件人 user_id 强行指定为 -1
        await send_message_service(
            db_session=conn,
            user_id=-1,
            req=system_req
        )

    return RegisterResponse(code=200, id=user_id)


async def forget_password_send_service(conn, email: str) -> EmailResponse:
    user = await db_get_user_by_email(conn, email)
    if not user:
        raise BusinessException(status_code=404, detail="未找到绑定该邮箱的账号")
    verification_code = generate_verification_code(6)
    # await smtp_send_email(email, verification_code)
    await db_save_verification_code(email, verification_code)
    return EmailResponse(
        code=200,
        verification_code=verification_code
    )


async def forget_password_set_service(
        conn, request: UserForgetPWD) -> BaseResponse:
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
    elif login_data.id.isdigit():
        user_id = int(login_data.id)
        user = await db_get_user_by_id(conn, user_id)
        if user:
            user["password"] = await db_get_password_by_id(conn, user_id)
    # 3. 非法输入：既不是邮箱，也不是纯数字 ID，直接拦截不查库
    else:
        raise BusinessException(status_code=400, detail="请输入正确的邮箱或数字 ID")

    if not user:
        raise BusinessException(status_code=400, detail="账号不存在或密码错误")

    hashed_pwd = user.get("password")
    if not hashed_pwd:
        raise BusinessException(status_code=400, detail="账号数据异常，请联系管理员")

    if not verify_password(login_data.password, hashed_pwd):
        raise BusinessException(status_code=400, detail="账号不存在或密码错误")

    await db_update_user_login_time(conn, user["user_id"])
    access_token = create_access_token(data={"sub": str(user["user_id"])})

    return LoginResponse(code=200, token=access_token)


async def logout_service(current_user_id: int) -> BaseResponse:
    await manager.disconnect(current_user_id)
    return BaseResponse(code=200, msg="登出成功")


async def delete_account_service(
        conn,
        current_user_id: int,
        plain_password: str) -> BaseResponse:
    # 1. 尝试获取用户信息
    user = await db_get_user_by_id(conn, current_user_id)

    # 2. 检查用户是否存在（虽然有 Token 鉴权，但为了健壮性建议保留）
    if not user:
        raise BusinessException(status_code=404, detail="用户不存在")

    # 3. 获取该用户的加密密码
    # 参考你登录时的逻辑：db_get_password_by_id
    hashed_pwd = await db_get_password_by_id(conn, current_user_id)

    if not hashed_pwd:
        raise BusinessException(status_code=400, detail="账号数据异常，无法验证身份")

    # 4. 验证用户输入的密码是否匹配
    if not verify_password(plain_password, hashed_pwd):
        # 为了安全，注销时的密码错误可以直接提示“密码错误”
        raise BusinessException(status_code=400, detail="注销失败：验证密码错误")

    # 5. 🌟【核心新增】拦截群主
    owned_groups = await db_get_owned_groups(conn, current_user_id)
    if owned_groups:
        group_names_str = "、".join(owned_groups)
        raise BusinessException(
            status_code=400,
            detail=f"注销失败：您还是以下群聊的群主（{group_names_str}）。请先解散群聊或转让群主身份，或使用强制注销功能。")

    # 6. 验证通过，执行注销逻辑
    # 这里的 db_delete_user 就是你之前写的那个 DELETE SQL
    # 6.1. 删除所有双向好友关系
    friend_rows = await conn.fetch(
        "SELECT friend_user_id FROM friend_relationship WHERE user_id = $1",
        current_user_id,
    )
    friend_ids = [row["friend_user_id"] for row in friend_rows]

    for friend_id in friend_ids:
        await db_remove_friend(conn, current_user_id, friend_id, delete_history=False)

    await db_delete_user(conn, current_user_id)
    return BaseResponse(code=200, msg="账号已彻底注销")


async def force_delete_account_service(
        conn,
        current_user_id: int,
        plain_password: str) -> BaseResponse:
    """强力注销接口：无视群主身份，连带解散名下所有群"""

    # 1. 身份与密码验证
    user = await db_get_user_by_id(conn, current_user_id)
    if not user:
        raise BusinessException(status_code=404, detail="用户不存在")

    hashed_pwd = await db_get_password_by_id(conn, current_user_id)
    if not hashed_pwd:
        raise BusinessException(status_code=400, detail="账号数据异常，无法验证身份")

    if not verify_password(plain_password, hashed_pwd):
        raise BusinessException(status_code=400, detail="强力注销失败：验证密码错误")

    # 2. 🌟【核心逻辑】批量解散名下所有群聊
    disbanded_conv_ids = await db_disband_all_owned_groups(conn, current_user_id)

    # 2. 🌟 循环补上服务层的附属动作（发通知 + 清理残余记录）
    if disbanded_conv_ids:
        for conv_id in disbanded_conv_ids:
            # 清理针对这个群的悬而未决的入群申请
            await db_clean_group_invites(conn, conv_id)

    # 💡 建议提示：如果你的系统有全局的消息下发机制（例如系统助手），
    # 可以在这里遍历 disbanded_conv_ids，给被解散的群成员发送“群聊已解散”的通知。

    # 3. 删除所有双向好友关系
    friend_rows = await conn.fetch(
        "SELECT friend_user_id FROM friend_relationship WHERE user_id = $1",
        current_user_id,
    )
    friend_ids = [row["friend_user_id"] for row in friend_rows]

    for friend_id in friend_ids:
        await db_remove_friend(conn, current_user_id, friend_id, delete_history=False)

    # 4. 软删除用户
    await db_delete_user(conn, current_user_id)

    msg = "账号已强制注销"
    if disbanded_conv_ids:
        msg += f"，并连带解散了 {len(disbanded_conv_ids)} 个群聊"

    return BaseResponse(code=200, msg=msg)


async def _verify_current_password(conn, user_id: int, plain_password: str):
    """
    业务层辅助函数：整合查库、验密、抛异常三大步骤
    """
    # 1. 查数据库拿到密文
    hashed_pwd = await db_get_password_by_id(conn, user_id)
    if not hashed_pwd:
        raise BusinessException(status_code=400, detail="账号数据异常，无法验证身份")

    # 2. 🌟 调用你在 core/security.py 里写的函数进行比对 🌟
    if not verify_password(plain_password, hashed_pwd):
        raise BusinessException(status_code=400, detail="密码验证失败")


# ----------------------------------------
# 修改用户名 Service (不需要密码)
# ----------------------------------------
async def edit_username_service(
        conn,
        current_user_id: int,
        edit_data: UsernameEdit) -> BaseResponse:
    success = await db_update_user_profile(conn, current_user_id, username=edit_data.new_username)
    if not success:
        raise BusinessException(status_code=400, detail="用户名更新失败")
    return BaseResponse(code=200, msg="用户名修改成功")


# ----------------------------------------
# 修改密码 Service
# ----------------------------------------
async def edit_password_service(
        conn,
        current_user_id: int,
        edit_data: PasswordEdit) -> BaseResponse:
    # 1. 直接调用辅助函数，一行代码完成验证！
    await _verify_current_password(conn, current_user_id, edit_data.old_password)

    # 2. 验证通过，执行更新
    hashed_new = get_password_hash(edit_data.new_password)
    # 3. 安全更新
    success = await db_update_user_password(conn, current_user_id, hashed_new)

    if not success:
        # 如果返回了 False，说明刚才发生了可怕的“静默失败”！
        raise BusinessException(status_code=500, detail="密码更新失败，该账号可能状态异常")

    return BaseResponse(code=200, msg="密码修改成功")


# ----------------------------------------
# 修改邮箱 Service (现在需要密码了)
# ----------------------------------------
async def edit_email_service(
        conn,
        current_user_id: int,
        edit_data: EmailEdit) -> BaseResponse:
    # 1. 同样调用辅助函数，先验密码！
    await _verify_current_password(conn, current_user_id, edit_data.password)

    # 2. 密码对了，才允许改邮箱
    success = await db_update_user_profile(conn, current_user_id, email=edit_data.new_email)
    if not success:
        raise BusinessException(status_code=400, detail="邮箱更新失败")

    return BaseResponse(code=200, msg="邮箱修改成功")


# ==========================================
# 头像存储的本地相对路径配置
# ==========================================
AVATAR_DIR = "static/avatars"
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 限制为 2MB (以字节为单位)


async def edit_portrait_service(conn, current_user_id: int, file: UploadFile):
    """
    修改头像的业务逻辑服务
    """
    # 1. 【新增】：校验文件大小，放在最前面，第一时间把巨型文件踢出去
    if file.size > MAX_AVATAR_SIZE:
        raise BusinessException(status_code=400, detail="头像图片大小不能超过 2MB")

    # 2. 校验后缀名，防止上传恶意文件
    ext = os.path.splitext(file.filename)[1].lower()
    allowed_extensions = [".jpg", ".jpeg", ".png", ".webp"]
    if ext not in allowed_extensions:
        raise BusinessException(status_code=400, detail="不支持的图片格式")

    # 3. 🌟 生成唯一的 Object Key（在 MinIO 中的文件名）
    object_name = f"{uuid.uuid4().hex}{ext}"

    file_bytes = await file.read()
    file_stream = io.BytesIO(file_bytes)

    # 4. 🌟 替代原有的 open/shutil，直接流式上传到 MinIO
    try:
        # 防御性兜底建桶
        if not s3_client.bucket_exists(settings.BUCKET_AVATAR):
            s3_client.make_bucket(settings.BUCKET_AVATAR)
        s3_client.put_object(
            bucket_name=settings.BUCKET_AVATAR,  # 这里读出来的就是 "avatars"
            object_name=object_name,
            data=file_stream,                      # FastAPI 的文件二进制流
            length=len(file_bytes),                    # 文件大小
            content_type=file.content_type       # 保证浏览器能正确识别图片类型而不是触发下载
        )
    except Exception as e:
        # 🌟 核心修复：把真凶打印出来！不要生吞报错！
        print(f"\n[💥 MINIO UPLOAD ERROR] 具体原因: {str(e)}\n")
        raise BusinessException(status_code=500, detail="头像文件保存至云存储失败")

    # 5. 🌟 拼接对外暴露的完整网络 URL 路径
    avatar_url = f"http://{settings.S3_ENDPOINT}/{settings.BUCKET_AVATAR}/{object_name}"

    # 6. 更新数据库里的路径信息（Repo 层不需要动）
    is_success = await db_update_user_profile(
        conn=conn,
        user_id=current_user_id,
        avatar_url=avatar_url
    )

    if not is_success:
        # 🌟 如果数据库挂了，逆向擦除刚才传到 MinIO 的垃圾图片，防止空间膨胀
        try:
            s3_client.remove_object(settings.BUCKET_AVATAR, object_name)
        except Exception:
            pass  # 擦除失败也无需中断，优先向用户抛出数据库错误

        raise BusinessException(status_code=500, detail="数据库更新头像失败")

    # 7. 成功！返回前端要求的数据结构
    return PortraitResponse(
        code=200,
        filekey=avatar_url,
        width=256,
        height=256
    )


async def get_user_info_service(
        conn, current_user_id: int) -> UserInfoResponse:
    """
    获取当前用户个人信息的业务逻辑
    """
    # 1. 去数据库查询用户信息
    user = await db_get_user_by_id(conn, current_user_id)

    # 2. 安全校验（理论上带有合法 Token 的用户一定存在，但防一手总是好的）
    if not user:
        raise BusinessException(status_code=404, detail="用户不存在")

    # 3. 封装成咱们定义好的返回类
    return UserInfoResponse(
        code=200,
        id=user["user_id"],
        username=user["username"],
        avatar_url=user.get("avatar_url"),  # get方法防止数据库里没有这个字段时报错
        email=user["email"]
    )
