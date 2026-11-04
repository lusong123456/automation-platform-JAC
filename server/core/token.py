"""客户端凭证换取 Token（被 pytest conftest 和后端 ai-draft 共用）。

被测系统对外部系统开放 /ai-contract/platform/auth/token 接口：
  用 appId / appSecret / employeeId 换取业务 Token，Bearer Token 形式鉴权。
按 TTL 提前续期；未配置客户端凭证时 get_token() 返回空串。
"""

import os
import threading
import time

import httpx

# 与 conftest.py 保持一致的环境变量名
APP_ID = os.getenv("APP_ID", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "").strip()
APP_EMPLOYEE_ID = os.getenv("APP_EMPLOYEE_ID", "").strip()
TOKEN_TTL = float(os.getenv("TOKEN_TTL_SECONDS", "1800"))
TOKEN_REFRESH_AHEAD = float(os.getenv("TOKEN_REFRESH_AHEAD", "300"))

# 被测基址（用于调 auth/token 接口；与 conftest.py 保持 prod-api 自动补全）
def _resolve_base_url() -> str:
    base = os.getenv("BASE_URL", "").rstrip("/")
    if base and ":11000" in base and "/prod-api" not in base:
        base += "/prod-api"
    return base


class TokenManager:
    """单例：客户端凭证 → 业务 Token，按 TTL 提前续期。"""

    def __init__(self) -> None:
        self._token = ""
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> str:
        base_url = _resolve_base_url()
        if not base_url:
            raise RuntimeError("未配置 BASE_URL，无法换取 Token")
        if not (APP_ID and APP_SECRET and APP_EMPLOYEE_ID):
            raise RuntimeError("未配置客户端凭证（APP_ID / APP_SECRET / APP_EMPLOYEE_ID）")

        with httpx.Client(base_url=base_url, timeout=30.0, verify=False) as c:
            r = c.post("/ai-contract/platform/auth/token", json={
                "appId": APP_ID,
                "appSecret": APP_SECRET,
                "employeeId": APP_EMPLOYEE_ID,
            })
            try:
                j = r.json()
            except Exception:
                raise RuntimeError(f"换取 Token 响应非 JSON：status={r.status_code} body={r.text[:200]}")
        token = (j.get("data") or {}).get("token")
        if j.get("code") != 200 or not token:
            raise RuntimeError(f"客户端凭证换取 Token 失败: {j.get('msg')} code={j.get('code')}")
        return token

    def get(self, force: bool = False) -> str:
        now = time.time()
        with self._lock:
            fresh = self._token and (now - self._fetched_at) < (TOKEN_TTL - TOKEN_REFRESH_AHEAD)
            if fresh and not force:
                return self._token
            self._token = self._fetch()
            self._fetched_at = now
            return self._token


_manager = TokenManager()


def get_token(force: bool = False) -> str:
    """对外入口：返回 Bearer Token；未配置凭证时返回空串。"""
    try:
        return _manager.get(force=force)
    except Exception:
        return ""
