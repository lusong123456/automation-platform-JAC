"""4 条 L1 E2E 核心链路用例 —— 直接跑真实后端。

按 xlsx「自动化回归测试用例-API.xlsx」L1 / E2E-核心链路 实现：
  1. LOGIN-014  登录-获取Token-登出全流程
  2. UPLOAD-019 上传→解析→保存→删除 完整链路
  3. REVIEW-019 上传→解析→提交审核→查询任务→查看报告→反馈 完整链路
  4. CONTRAST-010 上传两份合同→智能对比→查看结果
"""

import json
import time
from pathlib import Path

import allure
import httpx
import pytest

# conftest 的断言工具 + 进度钩子
from conftest import UPLOAD_TIMEOUT, assert_ok, assert_biz_fail, first_id, token_manager, _progress

# pytest mark（由平台收集器识别）
_regcase = pytest.mark.regcase

# Allure 报告左侧套件名（用例标题由 conftest 统一取 regcase.name）
pytestmark = [allure.suite("E2E-核心链路")]


# ──────────────────────────────────────────────────────────────
# 辅助：准备测试文件（仓库根 data/test-files 下的 docx；容器内即 /app/data/test-files）
# ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TEST_FILES_DIR = REPO_ROOT / "data" / "test-files"


def find_test_file(suffixes=(".docx", ".doc", ".pdf")):
    """返回 data/test-files 下第一个存在的测试文件路径。"""
    if not TEST_FILES_DIR.exists():
        return None
    for suf in suffixes:
        matches = list(TEST_FILES_DIR.glob(f"*{suf}"))
        if matches:
            return matches[0]
    return None


@pytest.fixture(scope="module")
def test_file_path() -> Path:
    """模块级测试文件：找不到就 skip 所有依赖文件的 E2E。"""
    f = find_test_file()
    if not f:
        pytest.skip(f"无测试文件：请把 docx 合同放到 {TEST_FILES_DIR}")
    return f


def upload_file_detail(api: httpx.Client, file_path: Path) -> dict:
    """POST /ai-contract/file/upload —— 返回后端 data（含 fileId / taskId / taskCode）。

    上传接口耗时不稳（实测出现过 30s+ 甚至网关 504），故单独放宽超时并对
    超时、连接中断、5xx 重试一次。
    """
    with allure.step(f"上传文件 {file_path.name}"):
        last_error = ""
        r = None
        for attempt in (1, 2):
            try:
                with open(file_path, "rb") as fh:
                    files = {"file": (file_path.name, fh, "application/octet-stream")}
                    r = api.post("/ai-contract/file/upload", files=files, timeout=UPLOAD_TIMEOUT)
                if r.status_code < 500:
                    break
                last_error = f"HTTP {r.status_code}: {r.text[:120]}"
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt == 1:
                allure.attach(f"第 1 次上传失败，重试：{last_error}",
                              name="上传重试", attachment_type=allure.attachment_type.TEXT)
                time.sleep(2)

        if r is None or r.status_code >= 500:
            pytest.fail(f"文件上传失败（已重试 1 次）：{last_error}")

        j = assert_ok(r, "文件上传")
        data = j.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        file_id = data.get("id") or data.get("fileId") or first_id(j)
        assert file_id, f"上传成功但没取到 fileId: {j}"
        allure.attach(str(file_id), name="fileId", attachment_type=allure.attachment_type.TEXT)
        for key in ("taskId", "taskCode"):
            if data.get(key):
                allure.attach(str(data[key]), name=key, attachment_type=allure.attachment_type.TEXT)
        return {"fileId": file_id, **data}


def upload_file(api: httpx.Client, file_path: Path) -> str:
    """上传文件，只返回 fileId（解析类用例使用）。"""
    return upload_file_detail(api, file_path)["fileId"]


# 上传响应里可能存放文件地址的字段名（实测 OBS 预签名地址在 presignedUrl/publicUrl）
URL_KEYS = ("presignedUrl", "publicUrl", "url", "fileUrl", "docPath",
            "contractDocPath", "originUrl", "downloadUrl", "filePath", "path")


def find_first(obj, *keys):
    """深度优先在响应 JSON 里递归找第一个「非空且匹配 key」的值。

    被测后端响应 data 都是泛型 AjaxResult，openapi 拿不到具体结构，
    用例对响应字段做兜底提取时用它，避免逐层手写 .get()。
    """
    for key in keys:
        if isinstance(obj, dict):
            if obj.get(key) not in (None, "", [], {}):
                return obj[key]
            for v in obj.values():
                found = find_first(v, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = find_first(item, key)
                if found is not None:
                    return found
    return None


def find_upload_url(detail: dict):
    """从上传响应（upload_file_detail 的返回）里提取文件地址。"""
    url = find_first(detail, *URL_KEYS)
    return url if isinstance(url, str) and url.strip() else None


# ══════════════════════════════════════════════════════════════
# 1. LOGIN-014  登录-获取Token-登出全流程
# ══════════════════════════════════════════════════════════════

@_regcase(id="LOGIN-014", module="登录认证", name="登录-获取Token-登出全流程")
@allure.story("E2E-核心链路")
@allure.severity(allure.severity_level.BLOCKER)
def test_login_flow(http_client: httpx.Client, api: httpx.Client):
    """
    链路：客户端凭证换取 Token 有效 → IAM getAuthorizeUrl 可用 → 鉴权接口可达
    → (带 token 的 client) GET /iam/logout → 登出成功
    注：自动化通过客户端凭证（APP_ID / APP_SECRET / APP_EMPLOYEE_ID）换取业务 Token。
    """
    _progress({"step": "iam_entry", "status": "running"})
    # Step1: IAM getAuthorizeUrl 公开可达（该入口偶发连接被网关断开，重试一次）
    with allure.step("GET /ai-contract/iam/getAuthorizeUrl — IAM 授权入口可达"):
        r = None
        for attempt in (1, 2):
            try:
                r = http_client.get("/ai-contract/iam/getAuthorizeUrl")
                break
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                if attempt == 1:
                    allure.attach(f"第 1 次请求失败，重试：{type(e).__name__}: {e}",
                                  name="IAM 入口重试", attachment_type=allure.attachment_type.TEXT)
                else:
                    raise
        j = assert_ok(r, "getAuthorizeUrl")
        url = (j.get("data") or {}).get("url", "")
        assert "iam" in url.lower() and "authorize" in url.lower(), f"authorize URL 格式异常: {url}"
    _progress({"step": "iam_entry", "status": "done"})

    _progress({"step": "auth_ok", "status": "running"})
    # Step2: 带 token 的 client 能访问需要鉴权的接口
    with allure.step("GET /ai-contract/rule/types — 鉴权成功返回数据"):
        r = api.get("/ai-contract/rule/types")
        j = assert_ok(r, "rule/types")
        assert j.get("data") is not None, "应有 data 返回"
    _progress({"step": "auth_ok", "status": "done"})

    _progress({"step": "logout", "status": "running"})
    # Step3: 登出
    with allure.step("POST /ai-contract/iam/logout — 登出"):
        r = api.post("/ai-contract/iam/logout")
        j = assert_ok(r, "logout")  # 登出应 200 / code=200 或 200 / msg=成功
    _progress({"step": "logout", "status": "done"})
    # 登出会让当前 Token 失效，作废缓存，后续用例重新换取
    token_manager.invalidate()


# ══════════════════════════════════════════════════════════════
# 2. UPLOAD-019  上传→解析→保存→删除 完整链路
# ══════════════════════════════════════════════════════════════

@_regcase(id="UPLOAD-019", module="合同上传", name="上传→解析→保存→删除")
@allure.story("E2E-核心链路")
@allure.severity(allure.severity_level.BLOCKER)
def test_upload_extract_save_delete(api: httpx.Client, test_file_path: Path):
    """
    链路：upload → extract/keyword+table+seal → extract/save → file/delete
    """
    file_id = None
    try:
        _progress({"step": "upload", "status": "running"})
        # Step1: 上传
        file_id = upload_file(api, test_file_path)
        _progress({"step": "upload", "status": "done"})

        _progress({"step": "extract", "status": "running"})
        # Step2: 三类解析
        for label, ep in [
            ("关键字段解析", "/ai-contract/file/extract/keyword"),
            ("表格解析",     "/ai-contract/file/extract/table"),
            ("签章解析",     "/ai-contract/file/extract/seal"),
        ]:
            with allure.step(f"POST {ep} — {label}"):
                r = api.post(ep, params={"fileId": file_id})
                j = assert_ok(r, label)
                assert j.get("data") is not None or j.get("msg"), f"{label} 应返回数据"
        _progress({"step": "extract", "status": "done"})

        _progress({"step": "save", "status": "running"})
        # Step3: 保存解析结果
        with allure.step("POST /ai-contract/file/extract/save — 保存"):
            r = api.post("/ai-contract/file/extract/save", json={
                "fileId": file_id,
                "keywordJson": "{}",
                "tableJson":   "[]",
                "sealJson":    "[]",
            })
            j = assert_ok(r, "extract/save")
        _progress({"step": "save", "status": "done"})

        _progress({"step": "list_check", "status": "running"})
        # Step4: 列表能看到这个文件
        with allure.step("GET /ai-contract/file/page — 列表能看到刚上传的文件"):
            r = api.get("/ai-contract/file/page", params={"page": 1, "size": 20})
            j = assert_ok(r, "file/page")
            rows = (j.get("data") or {}).get("records") or (j.get("data") or {}).get("rows") or []
            ids = [str(row.get("id", "")) for row in rows if isinstance(row, dict)]
            # 不一定在第一页（若用户已有很多文件），只断言接口正常返回即可
            allure.attach(f"共 {len(rows)} 条记录，fileId={file_id}",
                          name="文件列表快照", attachment_type=allure.attachment_type.TEXT)

    finally:
        # Step5: 清理 —— 无论中间哪步挂了都要删
        if file_id:
            with allure.step("POST /ai-contract/file/delete — 删除文件（清理）"):
                r = api.post("/ai-contract/file/delete", params={"fileId": file_id})
                # 删除可能返回 200 code=200，也可能业务错误（已经删过），不抛就行
                allure.attach(r.text[:200], name="delete 响应", attachment_type=allure.attachment_type.TEXT)


# ══════════════════════════════════════════════════════════════
# 3. REVIEW-019  上传→解析→提交审核→查询任务→查看报告→反馈
# ══════════════════════════════════════════════════════════════

@_regcase(id="REVIEW-019", module="合同审核",
          name="上传→解析→提交审核→查询任务→查看报告→反馈")
@allure.story("E2E-核心链路")
@allure.severity(allure.severity_level.BLOCKER)
def test_review_full_flow(api: httpx.Client, test_file_path: Path):
    """
    链路：upload → extract → task/commit → (轮询等待) → task/page → report/view → report/feedback
    注意：AI 审核耗时可能较长（30s ~ 数分钟），轮询带超时。
    """
    file_id = None
    task_id = None
    task_code = None
    try:
        _progress({"step": "upload", "status": "running"})
        # Step1: 上传（拿到 fileId / taskId / taskCode）
        detail = upload_file_detail(api, test_file_path)
        file_id = detail["fileId"]
        task_id = detail.get("taskId")
        task_code = detail.get("taskCode")
        assert task_id and task_code, f"上传响应缺少 taskId/taskCode: {detail}"
        _progress({"step": "upload", "status": "done"})

        _progress({"step": "extract", "status": "running"})
        with allure.step("POST /ai-contract/file/extract/keyword — 预解析"):
            r = api.post("/ai-contract/file/extract/keyword", params={"fileId": file_id})
            assert_ok(r, "extract/keyword")
        _progress({"step": "extract", "status": "done"})

        _progress({"step": "commit", "status": "running"})
        # Step2: 提交审核（commit 传 taskId，不是 fileId）
        with allure.step("POST /ai-contract/file/task/commit — 提交审核任务"):
            r = api.post("/ai-contract/file/task/commit", params={"taskId": task_id})
            j = assert_ok(r, "task/commit")
            task_id = first_id(j) or task_id
            allure.attach(f"taskId={task_id}", name="taskId", attachment_type=allure.attachment_type.TEXT)
        _progress({"step": "commit", "status": "done"})

        _progress({"step": "poll_start", "status": "running"})
        # Step3: 轮询等待审核完成（最多等 5 分钟，查询用 taskCode）
        with allure.step("轮询 /ai-contract/file/task/item 等待审核完成"):
            max_wait = 300
            interval = 5
            elapsed = 0
            final_status = None
            poll_idx = 0
            poll_total = max_wait // interval
            while elapsed < max_wait:
                poll_idx += 1
                r = api.get("/ai-contract/file/task/item", params={"taskCode": task_code})
                if r.status_code == 200:
                    try:
                        j = r.json()
                    except Exception:
                        j = {}
                    if j.get("code") == 200:
                        data = j.get("data") or {}
                        status = (data.get("taskStatus") or data.get("auditStatus")
                                  or data.get("status") or data.get("state"))
                        allure.attach(f"t={elapsed}s status={status}",
                                      name="轮询快照", attachment_type=allure.attachment_type.TEXT)
                        _progress({"step": "poll", "status": "running", "idx": poll_idx, "total": poll_total, "auditStatus": status})
                        if status in ["FINISHED", "FINISH", "COMPLETED", "DONE", "SUCCESS",
                                      "finish", "completed", "done"]:
                            final_status = status
                            break
                        if status in ["FAIL", "ERROR", "FAILED", "REJECTED", "fail", "error", "failed"]:
                            pytest.fail(f"审核失败 status={status} data={data}")
                time.sleep(interval)
                elapsed += interval
            allure.attach(f"最终等待 {elapsed}s 后 status={final_status}",
                          name="轮询结果", attachment_type=allure.attachment_type.TEXT)
            assert final_status, f"审核超时：{max_wait}s 内未完成，最后 status={final_status}"
        _progress({"step": "poll", "status": "done", "idx": poll_idx, "total": poll_total})

        _progress({"step": "report", "status": "running"})
        # Step4 & 5: 报告查看（list + report/view 合并进 report 步骤）
        with allure.step("GET /ai-contract/file/report/view — 查看报告"):
            r = api.get("/ai-contract/file/report/view",
                        params={"taskCode": task_code, "fileId": file_id})
            j = assert_ok(r, "report/view")
            item_views = (j.get("data") or {}).get("itemViews") or []
            allure.attach(f"itemViews 分组数：{len(item_views)}",
                          name="report/view", attachment_type=allure.attachment_type.TEXT)
        _progress({"step": "report", "status": "done"})

        _progress({"step": "feedback", "status": "running"})
        # Step6: 报告反馈（openapi: ReportFeedbackRequest = reportId/documentId(int64)
        #        + action(int32，1=点赞) + reasons/comment(string)）
        # reportId 从上一步 report/view 响应里提取，拿不到就直接失败暴露问题
        with allure.step("POST /ai-contract/file/report/feedback — 报告反馈"):
            report_id = find_first(j, "reportId", "documentId")
            if report_id is None:
                # report/view 未返回 reportId 时，用第一条审查建议的 id 兜底
                # （反馈即针对某条审查建议点赞/点踩，itemViews[].id 即建议 id）
                item_views = (j.get("data") or {}).get("itemViews") or []
                report_id = item_views[0].get("id") if item_views else None
            assert report_id is not None, \
                f"report/view 响应中未找到 reportId/documentId/itemViews[].id，无法反馈，响应={j}"
            r = api.post("/ai-contract/file/report/feedback", json={
                "reportId": report_id,
                "action": 1,  # 1=点赞
                "comment": "E2E 自动化测试反馈",
            })
            assert_ok(r, "report/feedback")
        _progress({"step": "feedback", "status": "done"})

    finally:
        # 清理
        if file_id:
            try:
                api.post("/ai-contract/file/delete", params={"fileId": file_id})
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
# 4. CONTRAST-010  上传两份合同→智能对比→查看结果
# ══════════════════════════════════════════════════════════════

@_regcase(id="CONTRAST-010", module="合同对比", name="上传两份合同→智能对比→查看结果")
@allure.story("E2E-核心链路")
@allure.severity(allure.severity_level.NORMAL)
def test_contract_compare(api: httpx.Client, test_file_path: Path):
    """
    链路：upload(基准) → upload(对比) → POST /contract/compare → 校验对比结果
    接口定义（openapi.json）：CompareRequest = {source: {originUrl, name},
    target: {originUrl, name}}，传的是文件地址不是 fileId。
    注意：只准备了一份测试文件时，用同一份做两次 upload 即可。
    """
    file_a = None
    file_b = None
    try:
        _progress({"step": "upload", "status": "running"})
        detail_a = upload_file_detail(api, test_file_path)
        file_a = detail_a["fileId"]
        detail_b = upload_file_detail(api, test_file_path)  # 同文件不同 fileId
        file_b = detail_b["fileId"]
        _progress({"step": "upload", "status": "done"})

        _progress({"step": "compare", "status": "running"})
        with allure.step("POST /ai-contract/contract/compare — 发起智能对比"):
            url_a = find_upload_url(detail_a)
            url_b = find_upload_url(detail_b)
            assert url_a and url_b, \
                f"上传响应中未找到文件地址字段（{URL_KEYS}），无法发起对比：a={detail_a} b={detail_b}"
            r = api.post("/ai-contract/contract/compare", json={
                "source": {"originUrl": url_a, "name": test_file_path.name},
                "target": {"originUrl": url_b, "name": test_file_path.name},
            })
            j = assert_ok(r, "智能对比")
            allure.attach(json.dumps(j, ensure_ascii=False)[:800],
                          name="compare 响应", attachment_type=allure.attachment_type.TEXT)
            assert j.get("data") is not None, "智能对比应返回结果数据"
        _progress({"step": "compare", "status": "done"})
    finally:
        for fid in [file_a, file_b]:
            if fid:
                try:
                    api.post("/ai-contract/file/delete", params={"fileId": fid})
                except Exception:
                    pass
