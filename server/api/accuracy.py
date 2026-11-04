"""审核正确性 API：列出 accuracy 执行记录、查询单条 run 的完整 diff。"""

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from server import db

router = APIRouter(tags=["accuracy"])
logger = logging.getLogger(__name__)


@router.get("/accuracy/runs")
def list_accuracy_runs(limit: int = Query(20, ge=1, le=200)) -> dict:
    items = db.list_accuracy_runs(limit)
    return {"total": len(items), "items": items}


@router.get("/accuracy/runs/{run_id}")
def get_accuracy_detail(run_id: str):
    detail = db.get_accuracy_detail(run_id)
    if not detail:
        return JSONResponse(
            status_code=404,
            content={"code": "ACCURACY_NOT_FOUND", "message": "该执行无审核正确性详情"},
        )
    return detail
