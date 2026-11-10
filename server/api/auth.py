"""登录鉴权：内置管理员 + 平台用户表，HttpOnly Cookie + HMAC 自签 token。"""

import base64
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from server import db
from server.config import settings

router = APIRouter(tags=["auth"])

COOKIE_NAME = "session"
_PBKDF2_ITERATIONS = 200_000


class LoginBody(BaseModel):
    username: str
    password: str


def auth_active() -> bool:
    """开关打开且配置了密码才生效，避免误配把平台锁死。"""
    return settings.auth_enabled and bool(settings.auth_password)


def _secret() -> str:
    return settings.auth_secret or settings.auth_password


def _sign(payload: str) -> str:
    digest = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _make_token(username: str) -> str:
    payload = f"{username}|{int(time.time()) + settings.auth_session_ttl}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """校验签名与有效期，通过时返回用户名。"""
    try:
        encoded, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    username, _, exp = payload.rpartition("|")
    if not username or not exp.isdigit() or int(exp) <= time.time():
        return None
    return username


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希，格式：算法$迭代$盐$摘要。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt), int(iterations)
        )
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def current_user(request: Request) -> str | None:
    """校验登录态；用户被删除后其已签发的 token 立即失效。"""
    username = verify_token(request.cookies.get(COOKIE_NAME, ""))
    if not username:
        return None
    if username != settings.auth_username and not db.user_exists(username):
        return None
    return username


@router.post("/auth/login")
def login(body: LoginBody, response: Response) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(status_code=400, detail="登录功能未启用")
    if not settings.auth_password:
        raise HTTPException(status_code=500, detail="服务端未配置登录密码")

    username = body.username.strip()
    builtin = hmac.compare_digest(username.encode(), settings.auth_username.encode()) and (
        hmac.compare_digest(body.password.encode(), settings.auth_password.encode())
    )
    if not builtin:
        user = db.get_user_by_username(username)
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")

    response.set_cookie(
        COOKIE_NAME,
        _make_token(username),
        max_age=settings.auth_session_ttl,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"username": username}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict:
    if not auth_active():
        return {"username": "", "auth_enabled": False}
    username = current_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": username, "auth_enabled": True}
