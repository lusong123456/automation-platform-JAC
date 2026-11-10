"""平台用户管理：新增与删除（内置管理员由 .env 配置，不在此管理）。"""

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server import db
from server.api.auth import current_user, hash_password
from server.config import settings

router = APIRouter(tags=["users"])


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""


@router.get("/users")
def list_users() -> dict:
    return {"items": db.list_users()}


@router.post("/users")
def create_user(body: UserCreate) -> dict:
    username = body.username.strip()
    display_name = body.display_name.strip()
    password = body.password

    if not username:
        raise HTTPException(status_code=400, detail="请输入用户名")
    if len(username) > 64:
        raise HTTPException(status_code=400, detail="用户名不能超过 64 个字符")
    if username == settings.auth_username:
        raise HTTPException(status_code=400, detail="该用户名为内置管理员，请更换")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if db.get_user_by_username(username):
        raise HTTPException(status_code=400, detail="用户名已存在")

    try:
        return db.create_user(username, display_name, hash_password(password))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="用户名已存在")


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request) -> dict:
    target = next((user for user in db.list_users() if user["id"] == user_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if current_user(request) == target["username"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    if not db.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"deleted": target["username"]}
