"""执行引擎：subprocess 调用 pytest，实时推进进度，汇总结果并生成报告。

设计要点：
- 选中用例以 nodeid 精确传入 pytest 参数，避免 -k 关键字误匹配；
- 结果以 conftest 钩子写入的 results.jsonl 为权威数据源，stdout 只用于实时进度；
- 与用例代码进程隔离，超时/崩溃不会拖垮 API 进程。
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from server import db
from server.config import REPO_ROOT, settings
from server.core import collector, report, state

logger = logging.getLogger(__name__)

# pytest -v 输出形如：tests/api/contract/test_flow.py::test_ping_ok PASSED [ 25%]
PROGRESS_RE = re.compile(
    r"^(?P<nodeid>\S+::\S+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
)


class InvalidCases(Exception):
    """勾选的用例不合法。"""


class RunInProgress(Exception):
    """已有任务在执行中。"""

    def __init__(self, run_id: str | None):
        super().__init__("已有任务在执行中")
        self.run_id = run_id


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """超时或关停时终止整棵子进程树。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def _match_case(nodeid: str, index: dict[str, dict]) -> dict | None:
    if nodeid in index:
        return index[nodeid]
    for key, case in index.items():  # 兼容参数化用例的 [参数] 后缀
        if nodeid.startswith(key):
            return case
    return None


def _read_results(results_file: Path, index: dict[str, dict]) -> list[dict]:
    """读取 conftest 结果钩子产出的 results.jsonl。"""
    if not results_file.exists():
        return []
    records: dict[str, dict] = {}
    for line in results_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        nodeid = raw.get("nodeid", "")
        case = _match_case(nodeid, index)
        case_id = case["id"] if case else nodeid
        outcome = raw.get("outcome") or "unknown"
        status = {"passed": "passed", "failed": "failed", "skipped": "skipped"}.get(outcome, outcome)
        records[case_id] = {
            "case_id": case_id,
            "nodeid": (case or {}).get("nodeid") or nodeid,
            "name": (case or {}).get("name") or nodeid,
            "module": (case or {}).get("module") or "未分组",
            "status": status,
            "message": (raw.get("message") or "")[:500],
            "duration_ms": int((raw.get("duration") or 0) * 1000),
        }
    return list(records.values())


def start_run(case_ids: list[str], triggered_by: str) -> dict:
    """校验并启动一次执行，立即返回 run 记录（实际执行在后台线程）。"""
    if not case_ids:
        raise InvalidCases("请先勾选要执行的用例")

    manifest = collector.get_cases()
    all_cases = manifest.get("cases", [])
    by_id = {c["id"]: c for c in all_cases}
    index = {c["nodeid"]: c for c in all_cases}

    if not by_id:
        reason = manifest.get("collect_error") or "用例清单为空"
        raise InvalidCases(f"用例清单不可用：{reason}")

    unknown = [cid for cid in case_ids if cid not in by_id]
    if unknown:
        raise InvalidCases(f"以下用例不存在：{', '.join(unknown)}")

    running = db.get_running_run()
    if running or state.is_running():
        raise RunInProgress((running or {}).get("id"))

    if not state.try_acquire():
        raise RunInProgress(None)

    try:
        selected = [by_id[cid] for cid in case_ids]
        run_id = _new_run_id()
        workdir = settings.runs_dir / run_id
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "case_manifest.json").write_text(
            json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        db.create_run(run_id, case_ids, triggered_by, len(selected))
        threading.Thread(target=_execute, args=(run_id, selected, index), daemon=True).start()
    except Exception:
        state.release()
        raise

    return db.get_run(run_id) or {
        "id": run_id, "status": "running", "case_total": len(case_ids),
        "case_executed": 0, "case_passed": 0, "case_failed": 0,
    }


def _execute(run_id: str, selected: list[dict], index: dict[str, dict]) -> None:
    workdir = settings.runs_dir / run_id
    log_path = workdir / "pytest.log"
    results_file = workdir / "results.jsonl"
    allure_dir = workdir / "allure-results"

    status = "error"
    error_message: str | None = None
    report_ok = False
    report_path: str | None = None
    executed = passed = failed = 0
    started = time.time()

    try:
        if not settings.base_url:
            raise RuntimeError("未配置被测环境地址，请在系统设置页填写后重试")

        name_by_nodeid = {c["nodeid"]: c["name"] for c in selected}

        # —— 组装子进程命令：选中用例以 nodeid 精确传入 ——
        cmd = [
            sys.executable, "-m", "pytest", *[c["nodeid"] for c in selected],
            "-p", "no:cacheprovider", "--color=no", "-v", "--tb=short",
            f"--alluredir={allure_dir}",
        ]
        # progress.jsonl 路径：env 和 tail 线程都会引用，必须先定义
        progress_path = workdir / "progress.jsonl"
        env = os.environ.copy()
        env.update({
            "BASE_URL": settings.base_url,
            "HTTP_TIMEOUT": str(settings.http_timeout),
            "AUTH_MODE": settings.auth_mode,
            "TEST_USERNAME": settings.test_username,
            "TEST_PASSWORD": settings.test_password,
            "APP_ID": settings.app_id,
            "APP_SECRET": settings.app_secret,
            "APP_EMPLOYEE_ID": settings.app_employee_id,
            "RUN_RESULT_FILE": str(results_file),
            "RUN_PROGRESS_FILE": str(progress_path),
            "ACCURACY_DIFF_FILE": str(workdir / "accuracy_diff.json"),
            "TEST_CONTRACTS_DIR": str(REPO_ROOT / "data" / "test-contracts"),
            "STANDARD_RESULTS_DIR": str(REPO_ROOT / "data" / "standard-results"),
            "AI_JUDGE_ENABLED": "true" if settings.ai_judge_enabled else "false",
            "LLM_BASE_URL": settings.llm_base_url,
            "LLM_API_KEY": settings.llm_api_key,
            "LLM_MODEL": settings.llm_model,
            "LLM_TIMEOUT": str(settings.llm_timeout),
            "AI_JUDGE_SCOPE": settings.ai_judge_scope,
            "SUGGESTION_OVERLAP_THRESHOLD": str(settings.suggestion_overlap_threshold),
            "ACC_GROUP_MATCH": "true" if settings.acc_group_match else "false",
            "ACC_PAIR_THRESHOLD": str(settings.acc_pair_threshold),
            "NO_COLOR": "1",
        })

        popen_kwargs: dict = dict(
            cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True  # 便于整组 kill
            env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(cmd, **popen_kwargs)
        timed_out = threading.Event()

        def _watchdog() -> None:
            try:
                proc.wait(timeout=settings.run_timeout)
            except subprocess.TimeoutExpired:
                timed_out.set()
                _kill_process_tree(proc)

        threading.Thread(target=_watchdog, daemon=True).start()

        # —— 子步骤进度：tail progress.jsonl → 写 DB → SSE 广播 ——

        def _tail_progress() -> None:
            """每 200ms 读一次 progress.jsonl 增量行，写入 run_sub_steps 表。"""
            last_pos = 0
            while proc.poll() is None:
                try:
                    if progress_path.exists():
                        with open(progress_path, encoding="utf-8") as pf:
                            pf.seek(last_pos)
                            for line in pf:
                                if not line.strip():
                                    continue
                                try:
                                    evt = json.loads(line.strip())
                                    case_id = evt.get("case_id") or evt.get("case", "")
                                    step = evt.get("step") or evt.get("type", "")
                                    status = evt.get("status", "running")
                                    # 轮询特殊处理：把 idx/total 拼进 detail
                                    detail_parts = []
                                    for k, v in evt.items():
                                        if k not in ("ts", "case_id", "case", "step", "status", "type"):
                                            detail_parts.append(f"{k}={v}")
                                    detail = ",".join(detail_parts) or None
                                    db.append_sub_step(run_id, case_id, step, status, detail)
                                except Exception:
                                    pass
                            last_pos = pf.tell()
                except Exception:
                    pass
                time.sleep(0.2)

        threading.Thread(target=_tail_progress, daemon=True).start()

        # —— 逐行读 stdout：落日志 + 推进进度 ——
        with open(log_path, "w", encoding="utf-8") as logf:
            for line in proc.stdout:  # type: ignore[union-attr]
                logf.write(line)
                logf.flush()
                match = PROGRESS_RE.match(line.strip())
                if not match:
                    continue
                executed += 1
                outcome = match.group("status")
                if outcome == "PASSED":
                    passed += 1
                elif outcome in ("FAILED", "ERROR"):
                    failed += 1
                nodeid = match.group("nodeid")
                db.update_progress(
                    run_id, executed, passed, failed, name_by_nodeid.get(nodeid, nodeid)
                )

        return_code = proc.wait()

        # —— 汇总结果：results.jsonl 为权威数据源 ——
        details = _read_results(results_file, index)
        for detail in details:
            db.upsert_run_case(
                run_id, detail["case_id"], detail["nodeid"], detail["name"],
                detail["module"], detail["status"], detail["message"], detail["duration_ms"],
            )

        if timed_out.is_set():
            status, error_message = "error", "执行超时，已中断"
        elif return_code == 0:
            status = "passed"
        elif return_code == 1:
            status = "failed"
        else:
            status, error_message = "error", f"pytest 退出码 {return_code}，请查看 pytest.log"

        if not details and executed == 0 and status == "passed":
            status, error_message = "error", "未收集到任何用例结果，请检查用例代码"

        # —— 生成报告（失败不影响上面的结果判定）——
        report_ok, report_path = report.generate(run_id, allure_dir)

        # —— 捕获 accuracy diff：检查 pytest accuracy 用例是否产出了差异 JSON ——
        accuracy_diff_path = workdir / "accuracy_diff.json"
        if accuracy_diff_path.exists():
            try:
                raw = accuracy_diff_path.read_text(encoding="utf-8")
                db.save_accuracy_detail(run_id, raw)
                logger.info("已捕获 accuracy diff，run_id=%s 文件大小=%d", run_id, len(raw))
            except Exception:
                logger.exception("accuracy diff 文件读取失败，run_id=%s", run_id)
        else:
            db.save_accuracy_detail(run_id, None)

    except Exception as exc:
        status, error_message = "error", f"执行异常：{exc}"
        logger.exception("执行异常，run_id=%s", run_id)
    finally:
        duration_ms = int((time.time() - started) * 1000)
        db.finish_run(run_id, status, error_message, report_path, report_ok, duration_ms)
        try:
            removed = report.prune(settings.report_keep)
            if removed:
                db.mark_report_unavailable(removed)
        except Exception:
            logger.exception("报告清理失败")
        state.release()
        logger.info(
            "执行结束 run_id=%s status=%s 通过=%s 失败=%s 耗时=%sms",
            run_id, status, passed, failed, duration_ms,
        )
