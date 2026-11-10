"""用例清单收集：调用 pytest 收集用例元数据（regcase marker），生成清单文件。

清单由用例代码决定，平台不感知具体接口；用例更新后重新收集即可。
"""

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

from server.config import REPO_ROOT, settings

logger = logging.getLogger(__name__)
_collect_lock = threading.Lock()

# 与 tests/api/e2e/test_review_accuracy.py 的 regcase.module 保持一致
_CONTRACT_CASE_MODULE = "审核正确性"
# 用例列表展示名；分组与排序仍按原始 module 名
_MODULE_DISPLAY_NAMES = {
    "审核正确性": "审核详情",
    "合同上传与文件管理": "合同上传",
    "合同审核-任务管理": "合同审核",
    "合同对比与合同类型": "合同对比",
}


def _summarize(text: str, limit: int = 800) -> str:
    """截断 pytest 错误输出，只保留对使用者有意义的部分。"""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join(lines[-15:])[:limit]


def _contract_upload_times() -> dict[str, float]:
    """合同 stem → 上传（文件修改）时间，用于合同用例的倒序展示。"""
    contracts_dir = settings.data_dir / "test-contracts"
    times: dict[str, float] = {}
    if not contracts_dir.exists():
        return times
    for f in contracts_dir.glob("*.docx"):
        if f.name.startswith("."):
            continue
        try:
            times[f.stem] = f.stat().st_mtime
        except OSError:
            continue
    return times


def _sort_contract_cases(modules: dict[str, list[dict]]) -> None:
    """审核正确性模块按合同上传时间倒序，与合同清单排序逻辑一致。

    只调整展示顺序，不改用例 id（id 仍按文件名生成），避免历史执行编号漂移。
    """
    items = modules.get(_CONTRACT_CASE_MODULE)
    if not items:
        return
    times = _contract_upload_times()
    items.sort(key=lambda c: times.get(c["name"], float("-inf")), reverse=True)


def module_display(raw: str) -> str:
    """原始模块名 → 对外展示名（未知模块原样返回）。"""
    return _MODULE_DISPLAY_NAMES.get(raw or "", raw or "未分组")


def module_category(raw: str) -> str:
    """模块分类：contract=合同审核用例，flow=IT 流程用例。"""
    return "contract" if raw == _CONTRACT_CASE_MODULE else "flow"


def _group(cases: list[dict]) -> list[dict]:
    """按模块分组，保持收集顺序；合同用例按上传时间倒序展示。"""
    modules: dict[str, list[dict]] = {}
    for case in cases:
        modules.setdefault(case.get("module") or "未分组", []).append(
            {"id": case["id"], "name": case["name"], "nodeid": case["nodeid"]}
        )
    _sort_contract_cases(modules)
    return [
        {
            "name": module_display(name),
            "category": module_category(name),
            "cases": items,
        }
        for name, items in modules.items()
    ]


def collect_cases() -> dict:
    """执行 pytest --collect-only 生成清单；失败不抛异常，返回 collect_error 摘要。"""
    with _collect_lock:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_manifest = settings.data_dir / "case_manifest.tmp.json"
        tmp_manifest.unlink(missing_ok=True)

        env = os.environ.copy()
        env["CASE_MANIFEST_FILE"] = str(tmp_manifest)
        env["NO_COLOR"] = "1"
        cmd = [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", str(settings.tests_dir),
        ]

        collect_error = None
        cases: list[dict] = []
        try:
            proc = subprocess.run(
                cmd, cwd=str(REPO_ROOT), env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=180,
            )
            if tmp_manifest.exists():
                cases = json.loads(tmp_manifest.read_text(encoding="utf-8"))
            # pytest 退出码 5 表示没有收集到用例，不算收集错误
            if proc.returncode not in (0, 5):
                collect_error = _summarize(proc.stderr or proc.stdout) or f"收集失败，退出码 {proc.returncode}"
        except subprocess.TimeoutExpired:
            collect_error = "用例清单收集超时（超过 180 秒）"
        except Exception as exc:  # 兜底：任何异常都不能影响平台启动
            collect_error = f"用例清单收集异常：{exc}"
            logger.exception("用例清单收集异常")

        result = {
            "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "total": len(cases),
            "collect_error": collect_error,
            "modules": _group(cases),
            "cases": cases,
        }
        try:
            settings.manifest_cache.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.exception("清单缓存写入失败")
        logger.info("用例清单收集完成：%s 条，collect_error=%s", len(cases), bool(collect_error))
        return result


def get_cases(force: bool = False) -> dict:
    """优先读缓存；force=True 或缓存不存在时重新收集。"""
    if not force and settings.manifest_cache.exists():
        try:
            return json.loads(settings.manifest_cache.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("清单缓存读取失败，改为重新收集")
    return collect_cases()


def public_view(data: dict) -> dict:
    """对外响应：剔除内部使用的 cases 明细，并按当前展示规则重算分组。"""
    view = {k: v for k, v in data.items() if k != "cases"}
    view["modules"] = _group(data.get("cases") or [])
    return view
