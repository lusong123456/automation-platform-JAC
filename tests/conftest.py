"""用例侧公共约定（平台与用例的唯一耦合点）。

1) `regcase` marker：提供用例元数据，平台据此生成清单；
2) `CASE_MANIFEST_FILE` 环境变量：收集时由平台注入，本文件写入清单；
3) `RUN_RESULT_FILE` 环境变量：执行时由平台注入，本文件写入用例级结果。

登录策略（AUTH_MODE）：
  账号密码   — POST /login（username/password）换取业务 Token；
  客户端凭证 — POST /ai-contract/platform/auth/token（appId/appSecret/employeeId）。
  两种方式都按 TTL 提前续期；未配置对应凭证时 skip 需要鉴权的用例。
"""

import json
import os
import threading
import time
from pathlib import Path

import allure
import httpx
import pytest

# ── 子步骤进度钩子（runner.py 通过 RUN_PROGRESS_FILE 环境变量注入路径） ──
# 每调用一次 _progress({step, case, status, ...}) 就追加一行 JSON 到 progress.jsonl
_PROGRESS_FILE = os.environ.get("RUN_PROGRESS_FILE", "").strip()
_PROGRESS_LOCK = threading.Lock()
_current_case_name: str = ""


def _progress(event: dict) -> None:
    """向 progress.jsonl 追加一条事件（runner.py 的 tail 线程会读这个文件）。"""
    if not _PROGRESS_FILE:
        return  # 没注入路径说明不是 runner 执行，跳过
    event.setdefault("ts", time.time())
    global _current_case_name
    event.setdefault("case_id", _current_case_name)
    try:
        with _PROGRESS_LOCK:
            with open(_PROGRESS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass  # progress 写失败不影响主流程


def _case_display_name(item) -> str:
    """从 regcase marker 取 name（中文用例名），取不到回退 item.name。"""
    marker = item.get_closest_marker("regcase")
    if marker:
        name = marker.kwargs.get("name")
        if name:
            return str(name)
    return item.name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    global _current_case_name
    _current_case_name = _case_display_name(item)
    _progress({"step": "case_start", "status": "running", "case": _current_case_name})
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    _progress({"step": "case_end", "status": "done", "case": _current_case_name})
    yield


# 加载 .env（conftest.py 自己也要用 env）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / "deploy" / ".env")
except ImportError:
    pass

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:9000").rstrip("/")
# 如果 BASE_URL 没带 /prod-api 前缀且检测到是 nginx 11000 前端，自动补
if ":11000" in BASE_URL and "/prod-api" not in BASE_URL:
    BASE_URL += "/prod-api"
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
# 上传接口解析较慢，单独放宽超时
UPLOAD_TIMEOUT = float(os.getenv("UPLOAD_TIMEOUT", "180"))
# 被测网关会主动断开空闲长连接，复用死连接会抛 RemoteProtocolError；
# 关闭 keep-alive，让每个请求新建连接，代价只有一次局域网握手
HTTP_LIMITS = httpx.Limits(max_keepalive_connections=0)

# ── 认证方式与 Token 续期 ────────────────────────────────────────
# password：POST /login，只需账号密码；
# client_credentials：POST /ai-contract/platform/auth/token，用 appId/appSecret/employeeId 换取 Token。

AUTH_MODE = os.getenv("AUTH_MODE", "client_credentials").strip().lower()
TEST_USERNAME = os.getenv("TEST_USERNAME", "").strip()
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "").strip()
APP_ID = os.getenv("APP_ID", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "").strip()
APP_EMPLOYEE_ID = os.getenv("APP_EMPLOYEE_ID", "").strip()
TOKEN_TTL = float(os.getenv("TOKEN_TTL_SECONDS", "1800"))
TOKEN_REFRESH_AHEAD = float(os.getenv("TOKEN_REFRESH_AHEAD", "300"))


class TokenManager:
    """按认证方式换取 Token，并按 TTL 提前续期。

    必须是模块级单例：鉴权失效重试发生在请求执行期间（auth_flow 内），
    那时取不到 fixture，只能引用模块级对象。
    """

    def __init__(self) -> None:
        self._token = ""
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch(self) -> str:
        with httpx.Client(base_url=BASE_URL, timeout=HTTP_TIMEOUT, verify=False) as c:
            if AUTH_MODE == "password":
                if not (TEST_USERNAME and TEST_PASSWORD):
                    raise RuntimeError("未配置被测系统账号密码（TEST_USERNAME / TEST_PASSWORD）")
                r = c.post("/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
                j = r.json()
                token = j.get("token") or (j.get("data") or {}).get("token")
                fail_msg = j.get("msg") or "账号密码登录失败"
            else:
                if not (APP_ID and APP_SECRET and APP_EMPLOYEE_ID):
                    raise RuntimeError("未配置客户端凭证（APP_ID / APP_SECRET / APP_EMPLOYEE_ID）")
                r = c.post("/ai-contract/platform/auth/token", json={
                    "appId": APP_ID,
                    "appSecret": APP_SECRET,
                    "employeeId": APP_EMPLOYEE_ID,
                })
                j = r.json()
                token = (j.get("data") or {}).get("token")
                fail_msg = j.get("msg") or "客户端凭证换取 Token 失败"
        if j.get("code") != 200 or not token:
            raise RuntimeError(fail_msg)
        return token

    def get(self, force: bool = False) -> str:
        now = time.time()
        with self._lock:
            fresh = self._token and (now - self._fetched_at) < (TOKEN_TTL - TOKEN_REFRESH_AHEAD)
            if fresh and not force:
                return self._token
            self._token = self._fetch()
            self._fetched_at = time.time()
            return self._token

    def invalidate(self) -> None:
        """主动作废缓存（如用例调用过登出接口），下次请求重新换取 Token。"""
        with self._lock:
            self._token = ""
            self._fetched_at = 0.0


token_manager = TokenManager()


def _is_auth_expired(resp: httpx.Response) -> bool:
    """判断响应是否表示鉴权失效。

    本后端鉴权失败返回 HTTP 200 + body {"code":401}，
    因此必须读 body 判断，不能只看 HTTP 状态码。
    """
    if resp.status_code == 401:
        return True
    try:
        return resp.json().get("code") == 401
    except Exception:
        return False


class DynamicTokenAuth(httpx.Auth):
    """请求前注入最新 Token；若鉴权失效则强刷并重试一次。"""

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + token_manager.get()
        response = yield request
        if _is_auth_expired(response):
            request.headers["Authorization"] = "Bearer " + token_manager.get(force=True)
            yield request


# ── 平台钩子：用例清单 ───────────────────────────────────────────

def pytest_collection_modifyitems(config, items):
    """把收集到的用例元数据写成清单文件，供平台展示用例列表。

    同时把 regcase 的中文 name 注入 Allure 标题，使报告里的用例名与平台一致；
    只设置展示名，不改函数名与 nodeid，平台映射和历史趋势不受影响。
    """
    manifest_path = os.getenv("CASE_MANIFEST_FILE")

    manifest = []
    for item in items:
        marker = item.get_closest_marker("regcase")
        kwargs = marker.kwargs if marker else {}
        doc_first = ((item.function.__doc__ or "").strip().splitlines() or [""])[0].strip()
        name = kwargs.get("name") or doc_first or item.name
        if hasattr(item, "obj"):
            item.obj.__allure_display_name__ = name
        manifest.append({
            "id": kwargs.get("id") or item.nodeid,
            "name": name,
            "module": kwargs.get("module") or Path(str(item.fspath)).parent.name,
            "nodeid": item.nodeid,
        })

    if not manifest_path:
        return
    target = Path(manifest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 平台钩子：用例级结果 ────────────────────────────────────────

def pytest_runtest_logreport(report):
    """把每条用例的执行结果追加写入 results.jsonl（平台的结果权威数据源）。"""
    result_path = os.getenv("RUN_RESULT_FILE")
    if not result_path:
        return
    if report.when != "call" and not (report.when == "setup" and report.outcome != "passed"):
        return

    message = ""
    if report.failed:
        lines = (report.longreprtext or "").strip().splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("E "):
                text = stripped[2:].strip()
                if text.startswith("AssertionError:"):
                    text = text[len("AssertionError:"):].strip()
                message = text
                break
        if not message and lines:
            message = lines[-1].strip()
        message = message[:500]

    record = {
        "nodeid": report.nodeid,
        "outcome": report.outcome,
        "when": report.when,
        "duration": report.duration or 0,
        "message": message,
    }
    target = Path(result_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Allure 报告环境信息 ──────────────────────────────────────────

def _prop_escape(text: str) -> str:
    """把非 ASCII 字符转义为 \\uXXXX。

    Allure 用 Java Properties 读取 environment.properties，按 ISO-8859-1 解码，
    直接写中文会渲染成乱码，必须先转义再由 Java 还原。
    """
    return "".join(ch if ord(ch) < 128 else f"\\u{ord(ch):04x}" for ch in text)


def pytest_sessionstart(session):
    try:
        allure_dir = session.config.getoption("--alluredir")
    except (ValueError, AttributeError):
        return
    if not allure_dir:
        return
    target = Path(allure_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "environment.properties").write_text(
        "\n".join([
            f"{_prop_escape('被测环境地址')}={_prop_escape(BASE_URL)}",
            f"{_prop_escape('执行人')}={_prop_escape(os.getenv('OPERATOR', os.getenv('OPERATOR_DEFAULT', '未填写')))}",
            f"{_prop_escape('执行平台')}={_prop_escape('自动化接口回归测试平台')}",
        ]),
        encoding="utf-8",
    )


# ── 全局 fixture ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def http_client(base_url: str) -> httpx.Client:
    """裸 HTTP 客户端（不带 token），用于登录前/公开接口。"""
    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT, verify=False,
                      limits=HTTP_LIMITS) as c:
        yield c


@pytest.fixture(scope="session")
def token() -> str:
    """登录凭证 fixture —— 按 AUTH_MODE 换取的业务 Token（Bearer 形式）。"""
    try:
        t = token_manager.get()
        print(f"\n  [conftest] Token 就绪（{AUTH_MODE}）")
        return "Bearer " + t
    except Exception as e:
        pytest.skip(f"无法获取 Token：{e}")
    return ""


@pytest.fixture(scope="session")
def api(base_url: str) -> httpx.Client:
    """带 Token 的 HTTP 客户端（独立于 http_client，Token 动态注入 + 鉴权失效自动重试）。"""
    with httpx.Client(
        base_url=base_url,
        timeout=HTTP_TIMEOUT,
        verify=False,
        limits=HTTP_LIMITS,
        auth=DynamicTokenAuth(),
        headers={"Accept": "application/json, text/plain, */*"},
    ) as c:
        yield c


# ── 通用断言工具 ──────────────────────────────────────────────────

class ApiError(AssertionError):
    pass


def assert_ok(resp: httpx.Response, msg: str = "") -> dict:
    """断言 HTTP 200 + 业务 code=200，返回 JSON 响应体。"""
    assert resp.status_code == 200, f"HTTP {resp.status_code} {resp.text[:200]} | {msg}"
    try:
        j = resp.json()
    except Exception:
        raise ApiError(f"响应不是 JSON: {resp.text[:200]}")
    code = j.get("code")
    assert code == 200, f"业务 code={code} msg={j.get('msg','')} | {msg}"
    return j


def assert_biz_fail(resp: httpx.Response, msg_contains: str = "", msg: str = "") -> dict:
    """断言 HTTP 200 但业务 code≠200，用于校验"业务异常"场景。"""
    assert resp.status_code == 200, f"HTTP {resp.status_code} | {msg}"
    j = resp.json()
    assert j.get("code") != 200, f"期望业务失败但 code=200: {j} | {msg}"
    if msg_contains:
        assert msg_contains in str(j.get("msg", "")), f"期望 msg 包含 '{msg_contains}' 但实际 '{j.get('msg','')}' | {msg}"
    return j


def pick(j: dict, *keys, default=None):
    """安全地从嵌套 dict 里取值，list 会取第一个有匹配 key 的元素。"""
    cur = j
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        elif isinstance(cur, list):
            for item in cur:
                if isinstance(item, dict) and k in item:
                    cur = item[k]
                    break
            else:
                return default
        else:
            return default
    return cur


def first_id(j: dict) -> str | None:
    """从响应里捞第一个有 id 的对象的 id。"""
    data = j.get("data")
    if isinstance(data, dict):
        return data.get("id") or data.get("fileId") or data.get("taskId")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first.get("id") or first.get("fileId") or first.get("taskId")
    # 兜底扫 list / rows / items
    for k in ["list", "rows", "items", "records", "result"]:
        arr = j.get(k) or (data if isinstance(data, list) else None)
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict):
                return first.get("id") or first.get("fileId") or first.get("taskId")
    return None
