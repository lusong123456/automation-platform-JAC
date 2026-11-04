"""合同-标答管理 API。

操作范围限定在 settings.data_dir 下的 test-contracts/ 和 standard-results/ 目录。
不涉及 pytest、不涉及 DB，纯文件系统 CRUD + 模板生成。
"""

import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from server.config import REPO_ROOT, settings
from server.core.token import get_token
from server.schemas import StandardGroup

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contracts"])

# 两个目标目录（与 test_review_accuracy.py 里的路径约定保持一致）
CONTRACTS_DIR = settings.data_dir / "test-contracts"
STANDARDS_DIR = settings.data_dir / "standard-results"

# 文件名安全清洗：只保留中文、英文、数字、-_ 和 .
_SAFE_FILENAME_RE = re.compile(r'[^\w\u4e00-\u9fff\-\.]+', re.UNICODE)

# docx magic number（PK 开头的 ZIP）
_DOCX_MAGIC = b"PK\x03\x04"


# ──────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    STANDARDS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_filename(raw: str) -> str:
    """把任意输入清洗成安全的文件名。"""
    # 1. 去除路径分隔符
    name = raw.replace("/", "").replace("\\", "").replace("..", "")
    # 2. Unicode 全角转半角
    name = unicodedata.normalize("NFKC", name)
    # 3. 非法字符替换
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._-")
    if not name:
        name = "contract"
    # 4. 限制长度
    if len(name) > 120:
        stem, ext = (name.rsplit(".", 1) + ["docx"])[:2]
        name = stem[:100] + "." + ext
    return name


def _check_docx_magic(data: bytes) -> bool:
    """校验 docx 文件头（ZIP 格式）。"""
    return data.startswith(_DOCX_MAGIC)


def _is_temp_contract(name: str) -> bool:
    """AI 初稿过程中的临时文件（历史版本曾落在本目录，需要过滤并清理）。"""
    return name.startswith(".") or "tmp_ai_draft" in name


def _cleanup_leaked_temps() -> None:
    """清理历史遗留的 AI 初稿临时文件，避免出现在合同清单里。"""
    for f in CONTRACTS_DIR.glob("*"):
        if f.is_file() and _is_temp_contract(f.name):
            try:
                f.unlink()
                logger.info("清理 AI 初稿临时文件：%s", f.name)
            except OSError:
                pass


def _list_pairs() -> list[dict]:
    """扫描并配对所有合同和标答。"""
    _ensure_dirs()
    _cleanup_leaked_temps()
    contracts = {}
    for f in CONTRACTS_DIR.glob("*.docx"):
        if _is_temp_contract(f.name):
            continue
        stat = f.stat()
        contracts[f.stem] = {
            "name": f.name,
            "stem": f.stem,
            "size_kb": max(1, stat.st_size // 1024),
            "uploaded_at": _mtime_iso(stat.st_mtime),
        }

    standards = {}
    for f in STANDARDS_DIR.glob("*.json"):
        standards[f.stem] = f.name

    pairs = []
    for stem, c in sorted(contracts.items(), key=lambda x: x[1]["name"]):
        sname = standards.get(stem)
        pairs.append({
            **c,
            "has_standard": sname is not None,
            "standard_name": sname,
        })
    return pairs


def _mtime_iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _stem_from_name(name: str) -> str:
    """从 URL 参数里的文件名提取 stem（兼容 docx 或 json 后缀）。"""
    base = Path(name).stem
    # 如果 name 本身没后缀但带了 .docx 或 .json 字样也能提取
    if name.lower().endswith(".docx") or name.lower().endswith(".json"):
        base = name.rsplit(".", 1)[0]
    return base


# ──────────────────────────────────────────────────────────
# 1. 上传合同
# ──────────────────────────────────────────────────────────

@router.post("/contracts/upload")
async def upload_contract(file: UploadFile) -> dict:
    """上传 .docx 合同文件，落盘到 test-contracts/。"""
    _ensure_dirs()

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 文件")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")

    if not _check_docx_magic(data[:8]):
        raise HTTPException(status_code=400, detail="文件格式异常（不是合法的 docx）")

    safe_name = _sanitize_filename(file.filename)
    # 确保后缀
    if not safe_name.lower().endswith(".docx"):
        safe_name += ".docx"

    target = CONTRACTS_DIR / safe_name
    if target.exists():
        raise HTTPException(status_code=409, detail=f"文件 {safe_name} 已存在，请先删除或重命名后再上传")

    target.write_bytes(data)
    logger.info("合同上传成功：%s", target)

    return {
        "name": safe_name,
        "stem": target.stem,
        "size_kb": len(data) // 1024,
        "has_standard": False,
    }


# ──────────────────────────────────────────────────────────
# 2. 列出所有合同
# ──────────────────────────────────────────────────────────

@router.get("/contracts")
def list_contracts() -> dict:
    return {"items": _list_pairs()}


# ──────────────────────────────────────────────────────────
# 3. 删除合同（可选同步删除标答）
# ──────────────────────────────────────────────────────────

@router.delete("/contracts/{name}")
def delete_contract(name: str) -> dict:
    """删除指定合同；同时删除同名配对的标答（如果存在）。"""
    _ensure_dirs()
    stem = _stem_from_name(name)
    docx = CONTRACTS_DIR / f"{stem}.docx"
    std = STANDARDS_DIR / f"{stem}.json"

    deleted = {"contract": False, "standard": False}
    if docx.exists():
        docx.unlink()
        deleted["contract"] = True
    if std.exists():
        std.unlink()
        deleted["standard"] = True

    if not deleted["contract"] and not deleted["standard"]:
        raise HTTPException(status_code=404, detail=f"未找到文件 stem={stem}")

    return {"deleted": deleted, "stem": stem}


# ──────────────────────────────────────────────────────────
# 4. 读取标答
# ──────────────────────────────────────────────────────────

@router.get("/standards/{name}")
def get_standard(name: str) -> dict:
    stem = _stem_from_name(name)
    std = STANDARDS_DIR / f"{stem}.json"
    if not std.exists():
        raise HTTPException(status_code=404, detail=f"标答不存在：{std.name}")
    try:
        data = json.loads(std.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"标答 JSON 损坏：{exc}")
    return {"name": std.name, "stem": stem, "data": data}


# ──────────────────────────────────────────────────────────
# 5. 保存标答（带 schema 校验）
# ──────────────────────────────────────────────────────────

@router.put("/standards/{name}")
def save_standard(name: str, body: list[StandardGroup]) -> dict:
    stem = _stem_from_name(name)
    target = STANDARDS_DIR / f"{stem}.json"

    # model_dump 成纯 dict 列表
    raw_data = [g.model_dump() for g in body]

    target.write_text(
        json.dumps(raw_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("标答保存成功：%s，%d 个分组", target, len(raw_data))

    # 后台异步刷新用例清单（不阻塞保存响应）
    import threading

    def _bg_refresh() -> None:
        try:
            from server.core import collector
            collector.collect_cases()
            logger.info("保存标答后用例清单刷新完成")
        except Exception as exc:
            logger.warning("保存标答后刷新清单失败（不影响保存本身）：%s", exc)

    threading.Thread(target=_bg_refresh, daemon=True).start()

    return {"name": target.name, "stem": stem, "groups": len(raw_data)}


# ──────────────────────────────────────────────────────────
# 6. 生成模板（从现有 4 份标答里提取规则骨架，去重合并）
# ──────────────────────────────────────────────────────────

def _build_template(contract_type: str | None = None) -> list[dict]:
    """扫描现有标准 JSON，提取 parentRuleId → ruleId 骨架并去重。"""
    _ensure_dirs()

    # 收集所有 parentRuleId 及其下属 issues 骨架
    seen_parents: dict[str, dict] = {}  # parentRuleId → 分组 dict
    for f in STANDARDS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for group in data:
            pid = group.get("parentRuleId", "")
            if not pid:
                continue
            if pid not in seen_parents:
                seen_parents[pid] = {
                    "parentRuleId": pid,
                    "parentRuleIntent": group.get("parentRuleIntent", ""),
                    "issues": {},  # ruleId → issue skeleton
                }
            issues = seen_parents[pid]["issues"]
            for issue in group.get("issues", []) or []:
                rid = issue.get("ruleId", "")
                if rid and rid not in issues:
                    issues[rid] = {
                        "ruleId": rid,
                        "ruleIntent": issue.get("ruleIntent", ""),
                        "compliant": True,
                        "original": "",
                        "revised": "",
                        "suggestion": "",
                    }

    # 转成最终列表，TYPE_SPECIFIC_REVIEW 放最后
    groups = []
    type_specific = []
    for pid, g in sorted(seen_parents.items()):
        issues_list = list(g["issues"].values())
        entry = {
            "parentRuleId": pid,
            "parentRuleIntent": g["parentRuleIntent"],
            "issues": issues_list,
        }
        if pid == "TYPE_SPECIFIC_REVIEW":
            # 如果用户指定了合同类型，只保留匹配的
            if contract_type:
                filtered = [
                    i for i in issues_list
                    if not i["ruleIntent"] or contract_type in i["ruleIntent"]
                ]
                # 没匹配到时保留全部也合理——让用户自己挑
                if filtered:
                    entry["issues"] = filtered
            type_specific.append(entry)
        else:
            groups.append(entry)
    groups.extend(type_specific)
    return groups


@router.get("/template")
def get_template(contract: str | None = None, contract_type: str | None = None) -> dict:
    """生成标答模板。

    - contract: 可选，合同文件名（用于从文件名推断合同类型关键词）
    - contract_type: 可选，直接指定合同类型关键词
    两者都没给 → 返回通用骨架（所有 parentRuleId）。
    """
    # 从合同名里猜类型关键词
    inferred_type = contract_type
    if not inferred_type and contract:
        cname = contract.lower()
        if "软件" in contract or "software" in cname:
            inferred_type = "软件"
        elif "技术" in contract or "开发" in contract or "tech" in cname:
            inferred_type = "技术开发"
        elif "服务" in contract or "service" in cname:
            inferred_type = "服务"
        elif "采购" in contract or "purchase" in cname:
            inferred_type = "货物"

    groups = _build_template(inferred_type)
    return {
        "contract_type": inferred_type,
        "groups": groups,
        "total_issues": sum(len(g["issues"]) for g in groups),
    }


# ──────────────────────────────────────────────────────────
# 7. AI 自动初稿：上传 docx → 调用审核链路 → 把后端结果转成标答 JSON
# ──────────────────────────────────────────────────────────

def _map_item_views_to_standard(item_views: list[dict]) -> list[dict]:
    """把后端 report/view 返回的 itemViews 直接转换成标答 JSON 格式。"""
    grouped = []
    for g in item_views:
        issues = []
        for issue in g.get("issues", []) or []:
            issues.append({
                "ruleId": issue.get("ruleId", ""),
                "ruleIntent": issue.get("intent", ""),
                "compliant": bool(issue.get("compliant", False)),
                "original": issue.get("original", "") or "",
                "revised": issue.get("revised", "") or "",
                "suggestion": issue.get("suggestion", "") or "",
            })
        grouped.append({
            "parentRuleId": g.get("ruleId", ""),
            "parentRuleIntent": g.get("intent", ""),
            "issues": issues,
        })
    return grouped


def _sse_event(obj: dict) -> str:
    """SSE 单条事件：`data: {json}\n\n`。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


class _TokenAuth(httpx.Auth):
    """同步版动态注入 Bearer Token；鉴权失效时强刷并重试一次（供后台线程使用）。"""

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + get_token()
        response = yield request
        is_401 = response.status_code == 401
        if not is_401:
            try:
                is_401 = response.json().get("code") == 401
            except Exception:
                pass
        if is_401:
            new_token = get_token(force=True)
            if new_token:
                request.headers["Authorization"] = "Bearer " + new_token
                yield request


# ───────── AI 初稿后台任务：服务端执行，切页不中断 ─────────
# 任务存在进程内存里，前端通过 task_id 重连/恢复；页面切换不再影响任务生命周期。
_AI_DRAFT_TASKS: dict[str, dict] = {}
_AI_DRAFT_LOCK = threading.Lock()
_AI_DRAFT_TTL = 3600  # 结束后保留 1 小时供前端恢复


def _purge_ai_draft_tasks() -> None:
    now = time.time()
    with _AI_DRAFT_LOCK:
        for key, task in list(_AI_DRAFT_TASKS.items()):
            if task["status"] != "running" and now - task["updated_at"] > _AI_DRAFT_TTL:
                _AI_DRAFT_TASKS.pop(key, None)


def _ai_append(task_id: str, evt: dict) -> None:
    """追加一条进度事件，并维护任务终态。"""
    with _AI_DRAFT_LOCK:
        task = _AI_DRAFT_TASKS.get(task_id)
        if task is None:
            return
        task["events"].append(evt)
        task["updated_at"] = time.time()
        if evt.get("done"):
            task["status"] = "success"
            task["result"] = evt
        elif evt.get("status") == "fail":
            task["status"] = "fail"
            task["error"] = evt.get("desc") or "AI 初稿生成失败"


def _ai_finish_unexpected(task_id: str) -> None:
    with _AI_DRAFT_LOCK:
        task = _AI_DRAFT_TASKS.get(task_id)
        if task and task["status"] == "running":
            task["status"] = "fail"
            task["error"] = "审核链路未返回结果"


def _ai_snapshot(task_id: str, task: dict) -> dict:
    return {
        "task_id": task_id,
        "contract_name": task["contract_name"],
        "stem": task["stem"],
        "status": task["status"],
        "events": list(task["events"]),
        "result": task["result"],
        "error": task["error"],
        "created_at": task["created_at"],
    }


def _run_ai_draft(task_id: str, contract_path: Path, safe_name: str, base_url: str) -> None:
    """后台线程：upload → extract → commit → poll → report → 转标答。"""
    timeout = httpx.Timeout(180.0, connect=30.0, write=60.0, read=180.0)
    try:
        with httpx.Client(
            base_url=base_url,
            timeout=timeout,
            verify=False,
            auth=_TokenAuth(),
        ) as client:
            # 1. upload
            _ai_append(task_id, {"step": "upload", "status": "running", "desc": "正在上传合同"})
            t0 = time.time()
            try:
                with open(contract_path, "rb") as fh:
                    r1 = client.post(
                        "/ai-contract/file/upload",
                        files={"file": (safe_name, fh, "application/octet-stream")},
                    )
            except Exception as ex:
                _ai_append(task_id, {"step": "upload", "status": "fail", "desc": f"上传异常：{ex}"})
                return
            if r1.status_code != 200:
                _ai_append(task_id, {"step": "upload", "status": "fail", "desc": f"上传失败：{r1.status_code}"})
                return
            try:
                j1 = r1.json()
            except Exception:
                _ai_append(task_id, {"step": "upload", "status": "fail", "desc": "上传响应非 JSON"})
                return
            payload = j1.get("data") or {}
            file_id = payload.get("id")
            task_code = payload.get("taskCode")
            remote_task_id = payload.get("taskId")
            if not all([file_id, remote_task_id, task_code]):
                _ai_append(task_id, {"step": "upload", "status": "fail", "desc": "上传缺少必要字段"})
                return
            _ai_append(task_id, {"step": "upload", "status": "done", "elapsed": round(time.time() - t0, 1)})

            # 2. extract/keyword
            _ai_append(task_id, {"step": "extract", "status": "running", "desc": "正在解析合同内容"})
            t0 = time.time()
            try:
                client.post("/ai-contract/file/extract/keyword", params={"fileId": file_id})
            except Exception as ex:
                _ai_append(task_id, {"step": "extract", "status": "fail", "desc": f"解析异常：{ex}"})
                return
            _ai_append(task_id, {"step": "extract", "status": "done", "elapsed": round(time.time() - t0, 1)})

            # 3. commit
            _ai_append(task_id, {"step": "commit", "status": "running", "desc": "正在提交审核任务"})
            t0 = time.time()
            try:
                r3 = client.post("/ai-contract/file/task/commit", params={"taskId": remote_task_id})
            except Exception as ex:
                _ai_append(task_id, {"step": "commit", "status": "fail", "desc": f"提交异常：{ex}"})
                return
            if r3.status_code != 200:
                _ai_append(task_id, {"step": "commit", "status": "fail", "desc": f"提交失败：{r3.status_code}"})
                return
            _ai_append(task_id, {"step": "commit", "status": "done", "elapsed": round(time.time() - t0, 1)})

            # 4. 轮询
            poll_total = 60
            _ai_append(task_id, {"step": "poll", "status": "running", "desc": "等待审核结果",
                                 "idx": 0, "total": poll_total})
            status = None
            finished_poll_idx = 0
            t_poll = time.time()
            for poll_idx in range(poll_total):
                time.sleep(5)
                finished_poll_idx = poll_idx + 1
                try:
                    r4 = client.get("/ai-contract/file/task/item", params={"taskCode": task_code})
                except Exception as ex:
                    _ai_append(task_id, {"step": "poll", "status": "running",
                                         "idx": finished_poll_idx, "total": poll_total,
                                         "auditStatus": f"异常: {ex}"})
                    continue
                if r4.status_code != 200:
                    _ai_append(task_id, {"step": "poll", "status": "running",
                                         "idx": finished_poll_idx, "total": poll_total,
                                         "auditStatus": f"HTTP {r4.status_code}"})
                    continue
                d4 = (r4.json().get("data") or {})
                status = d4.get("taskStatus") or d4.get("auditStatus") or d4.get("status")
                _ai_append(task_id, {"step": "poll", "status": "running",
                                     "idx": finished_poll_idx, "total": poll_total,
                                     "auditStatus": status or "UNKNOWN"})
                if status in ("FINISHED", "FINISH", "COMPLETED", "DONE", "SUCCESS"):
                    break
                if status in ("FAIL", "ERROR", "FAILED", "REJECTED"):
                    _ai_append(task_id, {"step": "poll", "status": "fail",
                                         "desc": f"审核失败：{status}",
                                         "idx": finished_poll_idx, "total": poll_total})
                    return
            else:
                _ai_append(task_id, {"step": "poll", "status": "fail",
                                     "desc": f"审核超时（{poll_total * 5}s）",
                                     "idx": finished_poll_idx, "total": poll_total})
                return
            _ai_append(task_id, {"step": "poll", "status": "done",
                                 "elapsed": round(time.time() - t_poll, 1),
                                 "idx": finished_poll_idx, "total": poll_total})

            # 5. report/view
            _ai_append(task_id, {"step": "report", "status": "running", "desc": "正在拉取完整报告"})
            t0 = time.time()
            try:
                r5 = client.get("/ai-contract/file/report/view",
                                params={"taskCode": task_code, "fileId": file_id})
            except Exception as ex:
                _ai_append(task_id, {"step": "report", "status": "fail", "desc": f"报告异常：{ex}"})
                return
            if r5.status_code != 200:
                _ai_append(task_id, {"step": "report", "status": "fail",
                                     "desc": f"报告失败：{r5.status_code}"})
                return
            item_views = ((r5.json().get("data") or {}).get("itemViews")) or []
            _ai_append(task_id, {"step": "report", "status": "done",
                                 "elapsed": round(time.time() - t0, 1),
                                 "issues": sum(len(g.get("issueViews") or []) for g in item_views)})

            # 6. 转标答 + 最终结果
            standard_data = _map_item_views_to_standard(item_views)
            _ai_append(task_id, {
                "done": True,
                "contract_name": safe_name,
                "standard_data": standard_data,
                "total_groups": len(standard_data),
                "total_issues": sum(len(g["issues"]) for g in standard_data),
                "note": "此为后端实时审核结果自动生成的初稿，请人工 review 后再保存。",
            })
    except Exception as ex:  # noqa: BLE001
        _ai_append(task_id, {"status": "fail", "desc": f"任务异常：{ex}"})
    finally:
        _ai_finish_unexpected(task_id)


@router.post("/contracts/ai-draft/start")
async def start_ai_draft(file: UploadFile) -> dict:
    """创建 AI 初稿任务：立即落盘合同并返回 task_id，任务在服务端后台继续跑。

    页面切换只断开 SSE 连接，任务不受影响；回来用 task_id 恢复进度与结果。
    """
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 文件")
    data = await file.read()
    if not _check_docx_magic(data[:8]):
        raise HTTPException(status_code=400, detail="文件格式异常（不是合法的 docx）")

    _ensure_dirs()
    safe_name = _sanitize_filename(file.filename)
    if not safe_name.lower().endswith(".docx"):
        safe_name += ".docx"
    contract_path = CONTRACTS_DIR / safe_name
    contract_path.write_bytes(data)

    base_url = settings.base_url.rstrip("/")
    if not base_url:
        raise HTTPException(status_code=500, detail="未配置 BASE_URL，无法调用审核后端")
    if ":11000" in base_url and "/prod-api" not in base_url:
        base_url += "/prod-api"

    _purge_ai_draft_tasks()
    task_id = uuid.uuid4().hex[:16]
    now = time.time()
    with _AI_DRAFT_LOCK:
        _AI_DRAFT_TASKS[task_id] = {
            "contract_name": safe_name,
            "stem": _stem_from_name(safe_name),
            "status": "running",
            "events": [],
            "result": None,
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
    threading.Thread(
        target=_run_ai_draft,
        args=(task_id, contract_path, safe_name, base_url),
        daemon=True,
    ).start()
    return {"task_id": task_id, "contract_name": safe_name, "stem": _stem_from_name(safe_name)}


@router.get("/contracts/ai-draft/active")
def get_active_ai_draft() -> dict:
    """最近一个 AI 初稿任务（含已结束未过期的），用于切页返回时恢复。"""
    _purge_ai_draft_tasks()
    with _AI_DRAFT_LOCK:
        if not _AI_DRAFT_TASKS:
            return {"task_id": ""}
        task_id, task = max(_AI_DRAFT_TASKS.items(), key=lambda kv: kv[1]["updated_at"])
        return _ai_snapshot(task_id, task)


@router.get("/contracts/ai-draft/{task_id}")
def get_ai_draft(task_id: str) -> dict:
    with _AI_DRAFT_LOCK:
        task = _AI_DRAFT_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        return _ai_snapshot(task_id, task)


@router.get("/contracts/ai-draft/{task_id}/stream")
def stream_ai_draft(task_id: str, offset: int = 0) -> StreamingResponse:
    """SSE：从 offset 开始回放历史事件，然后持续推送新增事件，任务结束即关闭。"""

    def _gen():
        sent = max(offset, 0)
        while True:
            with _AI_DRAFT_LOCK:
                task = _AI_DRAFT_TASKS.get(task_id)
                if task is None:
                    yield _sse_event({"status": "fail", "desc": "任务不存在或已过期"})
                    return
                new_events = list(task["events"][sent:])
                status = task["status"]
            for evt in new_events:
                yield _sse_event(evt)
            sent += len(new_events)
            if status != "running":
                return
            time.sleep(0.3)

    return StreamingResponse(_gen(), media_type="text/event-stream")
