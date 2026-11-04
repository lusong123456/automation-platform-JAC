"""用例清单接口。"""

from fastapi import APIRouter

from server.core import collector

router = APIRouter(tags=["cases"])


@router.get("/cases")
def list_cases() -> dict:
    """返回缓存清单；未收集过则即时收集一次。"""
    return collector.public_view(collector.get_cases())


@router.post("/cases/refresh")
def refresh_cases() -> dict:
    """用例代码更新后手动刷新清单。"""
    return collector.public_view(collector.collect_cases())
