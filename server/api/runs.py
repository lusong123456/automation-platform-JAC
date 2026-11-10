"""执行相关接口：触发执行、查询进度与记录、跳转报告。

错误响应统一为 {"code": "...", "message": "中文提示"}，便于前端直接展示。
"""

import json
import logging
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from server import db
from server.api.auth import current_user
from server.config import settings
from server.core import collector, runner
from server.core.runner import InvalidCases, RunInProgress
from server.schemas import RunCreateRequest

router = APIRouter(tags=["runs"])
logger = logging.getLogger(__name__)


def _to_api(run: dict) -> dict:
    """DB 行 → 对外响应结构（字段名与设计文档一致）。"""
    return {
        "run_id": run["id"],
        "status": run["status"],
        "triggered_by": run["triggered_by"],
        "case_total": run["case_total"],
        "case_executed": run["case_executed"],
        "case_passed": run["case_passed"],
        "case_failed": run["case_failed"],
        "current_case": run["current_case"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_ms": run["duration_ms"],
        "report_url": run["report_url"],
        "report_ready": run["report_ready"],
        "error_message": run["error_message"],
        "failed_cases": run.get("failed_cases", []),
    }


@router.post("/runs")
def create_run(payload: RunCreateRequest, request: Request):
    # 操作人留空时默认记为当前登录用户
    triggered_by = (
        (payload.triggered_by or "").strip()
        or current_user(request)
        or settings.operator_default
        or "未填写"
    )
    try:
        run = runner.start_run(payload.case_ids, triggered_by)
    except InvalidCases as exc:
        return JSONResponse(status_code=400, content={"code": "INVALID_CASES", "message": str(exc)})
    except RunInProgress as exc:
        return JSONResponse(
            status_code=409,
            content={
                "code": "RUN_IN_PROGRESS",
                "message": "已有任务在执行中，请等待当前任务完成",
                "run_id": exc.run_id,
            },
        )
    return {
        "run_id": run["id"],
        "status": run["status"],
        "case_total": run["case_total"],
        "started_at": run.get("started_at"),
    }


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    total, items = db.list_runs(limit, offset)
    return {"total": total, "items": [_to_api(item) for item in items]}


@router.get("/runs/active")
def get_active_run():
    """返回当前正在执行的 run（含完整进度和最新子步骤）；没有则返回 null。"""
    run = db.get_running_run()
    if not run:
        return {"run": None}
    run_api = _to_api(run)
    # 附带所有子步骤（前端 mount 时用来重建 UI）
    run_api["sub_steps"] = db.list_sub_steps(run["id"])
    return {"run": run_api}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        return JSONResponse(
            status_code=404,
            content={"code": "RUN_NOT_FOUND", "message": "执行记录不存在"},
        )
    return _to_api(run)


def _group_cases(cases: list[dict]) -> list[dict]:
    """按模块分组并统计通过/失败/跳过，保持执行顺序。

    kind: flow（IT 流程）/ contract（合同功能）/ accuracy（审核正确性）
    """
    contract_feature_modules = {"合同上传", "合同审核", "合同对比"}
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for case in cases:
        name = case["module"]
        group = index.get(name)
        if group is None:
            if case.get("category") == "contract":
                kind = "accuracy"
            elif name in contract_feature_modules:
                kind = "contract"
            else:
                kind = "flow"
            group = {
                "name": name,
                "category": case.get("category", "flow"),
                "kind": kind,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "cases": [],
            }
            index[name] = group
            groups.append(group)
        group["total"] += 1
        status = case.get("status") or ""
        if status == "passed":
            group["passed"] += 1
        elif status in ("failed", "error"):
            group["failed"] += 1
        elif status == "skipped":
            group["skipped"] += 1
        group["cases"].append(case)
    return groups


@router.get("/runs/{run_id}/report-data")
def get_report_data(run_id: str):
    """报告页数据：执行摘要 + 用例明细（按模块分组）+ 审核正确性详情。"""
    run = db.get_run(run_id)
    if not run:
        return JSONResponse(
            status_code=404, content={"code": "RUN_NOT_FOUND", "message": "执行记录不存在"}
        )
    cases = db.list_run_cases(run_id)
    for case in cases:
        raw = case.get("module") or ""
        case["category"] = collector.module_category(raw)
        case["module"] = collector.module_display(raw)
    accuracy = db.get_accuracy_detail(run_id)
    if accuracy and not accuracy.get("accuracy_data"):
        accuracy = None
    return {
        "run": _to_api(run),
        "cases": cases,
        "modules": _group_cases(cases),
        "accuracy": accuracy,
    }


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: str):
    """返回本次执行的 pytest 日志（超过 40 万字符时只保留尾部）。"""
    run = db.get_run(run_id)
    if not run:
        return JSONResponse(
            status_code=404, content={"code": "RUN_NOT_FOUND", "message": "执行记录不存在"}
        )
    log_path = settings.runs_dir / run_id / "pytest.log"
    if not log_path.exists():
        return JSONResponse(
            status_code=404,
            content={"code": "LOG_NOT_FOUND", "message": "该执行的日志不存在"},
        )
    text = log_path.read_text(encoding="utf-8", errors="replace")
    limit = 400_000
    if len(text) > limit:
        text = "……（日志过长，仅显示末尾部分）\n" + text[-limit:]
    return PlainTextResponse(text)


@router.get("/runs/{run_id}/report")
def open_report(run_id: str):
    run = db.get_run(run_id)
    if not run:
        return JSONResponse(
            status_code=404, content={"code": "RUN_NOT_FOUND", "message": "执行记录不存在"}
        )
    if not run["report_ready"] or not run["report_url"]:
        return JSONResponse(
            status_code=404,
            content={
                "code": "REPORT_UNAVAILABLE",
                "message": "报告不存在或已按保留策略清理",
            },
        )
    return RedirectResponse(url=run["report_url"])


@router.get("/runs/{run_id}/stream")
def stream_run(run_id: str):
    """SSE 实时推送执行进度事件（子步骤 + 用例完成）。

    前端每 200ms 轮询一次 DB 最新子步骤，有变化就推。
    """

    def gen():
        last_id = 0
        last_sub_step_id = 0
        last_case_executed = 0
        while True:
            run = db.get_run(run_id)
            if not run:
                yield f"data: {json.dumps({'done': True, 'message': 'run 不存在或已结束'})}\n\n"
                return

            # 1. 用例级进度变化
            run_changed = False
            if run["case_executed"] != last_case_executed or run["status"] != "running":
                run_changed = True
                last_case_executed = run["case_executed"]

            # 2. 子步骤变化
            latest = db.get_latest_sub_step(run_id)
            sub_step_changed = False
            if latest and latest["id"] != last_sub_step_id:
                sub_step_changed = True
                last_sub_step_id = latest["id"]

            if run_changed or sub_step_changed:
                evt = {
                    "case_executed": run["case_executed"],
                    "case_passed": run["case_passed"],
                    "case_failed": run["case_failed"],
                    "case_total": run["case_total"],
                    "current_case": run.get("current_case"),
                    "status": run["status"],
                }
                if sub_step_changed and latest:
                    evt["latest_sub_step"] = latest
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

            # 结束了就停
            if run["status"] != "running":
                yield f"data: {json.dumps({'done': True, 'status': run['status']})}\n\n"
                return

            time.sleep(0.2)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx buffering
        },
    )
