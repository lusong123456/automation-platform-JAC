"""SQLite 数据访问层：执行总记录（runs）与用例级结果（run_cases）。

平台单进程单实例，写入用进程内锁串行化，读取各自建连接，开 WAL 模式。
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from server.config import settings

_write_lock = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    triggered_by   TEXT,
    case_total     INTEGER NOT NULL,
    case_executed  INTEGER NOT NULL DEFAULT 0,
    case_passed    INTEGER NOT NULL DEFAULT 0,
    case_failed    INTEGER NOT NULL DEFAULT 0,
    current_case   TEXT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    duration_ms    INTEGER,
    report_path    TEXT,
    report_ready   INTEGER NOT NULL DEFAULT 0,
    error_message  TEXT,
    case_ids       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_cases (
    run_id      TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    nodeid      TEXT NOT NULL,
    name        TEXT,
    module      TEXT,
    status      TEXT,
    message     TEXT,
    duration_ms INTEGER,
    PRIMARY KEY (run_id, case_id)
);

CREATE TABLE IF NOT EXISTS run_sub_steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL,
    case_id  TEXT NOT NULL,
    step     TEXT NOT NULL,       -- 步骤名：upload / extract / commit / poll / report / compare ...
    status   TEXT NOT NULL,       -- running / done / fail
    detail   TEXT,                -- 扩展信息：poll 次数、错误消息等
    ts       TEXT NOT NULL        -- 事件时间（UTC ISO）
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_steps_run_id ON run_sub_steps(run_id, ts DESC);
"""


def now_iso() -> str:
    """统一存 UTC 时间并带时区标记，由前端按浏览器本地时区展示。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    settings.database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.database), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(DDL)
        # 向后兼容：如果旧 runs 表没有 accuracy_detail 列则补上
        cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()]
        if "accuracy_detail" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN accuracy_detail TEXT")
        conn.commit()
    finally:
        conn.close()


def _write(sql: str, params: tuple = ()) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


def _query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# —— 写入 ——

def create_run(run_id: str, case_ids: list[str], triggered_by: str, case_total: int) -> None:
    _write(
        "INSERT INTO runs (id, status, triggered_by, case_total, started_at, case_ids) "
        "VALUES (?, 'running', ?, ?, ?, ?)",
        (run_id, triggered_by, case_total, now_iso(), json.dumps(case_ids, ensure_ascii=False)),
    )


def update_progress(run_id: str, executed: int, passed: int, failed: int, current_case: str) -> None:
    _write(
        "UPDATE runs SET case_executed = ?, case_passed = ?, case_failed = ?, current_case = ? WHERE id = ?",
        (executed, passed, failed, current_case, run_id),
    )


def upsert_run_case(run_id: str, case_id: str, nodeid: str, name: str, module: str,
                    status: str, message: str, duration_ms: int) -> None:
    _write(
        "INSERT OR REPLACE INTO run_cases "
        "(run_id, case_id, nodeid, name, module, status, message, duration_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, case_id, nodeid, name, module, status, message, duration_ms),
    )


def finish_run(run_id: str, status: str, error_message: str | None,
               report_path: str | None, report_ready: bool, duration_ms: int) -> None:
    _write(
        "UPDATE runs SET status = ?, finished_at = ?, duration_ms = ?, error_message = ?, "
        "report_path = ?, report_ready = ? WHERE id = ?",
        (status, now_iso(), duration_ms, error_message, report_path, 1 if report_ready else 0, run_id),
    )


def mark_interrupted_runs() -> int:
    """服务启动时把残留的 running 记录标记为中断，避免僵尸状态。"""
    rows = _query("SELECT id FROM runs WHERE status = 'running'")
    if not rows:
        return 0
    for row in rows:
        _write(
            "UPDATE runs SET status = 'error', finished_at = ?, "
            "error_message = '服务重启导致执行中断' WHERE id = ?",
            (now_iso(), row["id"]),
        )
    return len(rows)


def mark_report_unavailable(run_ids: list[str]) -> None:
    for run_id in run_ids:
        _write("UPDATE runs SET report_ready = 0, report_path = NULL WHERE id = ?", (run_id,))


def save_accuracy_detail(run_id: str, accuracy_detail_json: str) -> None:
    _write("UPDATE runs SET accuracy_detail = ? WHERE id = ?", (accuracy_detail_json, run_id))


# —— 读取 ——

def _row_to_run(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["report_ready"] = bool(data.get("report_ready"))
    data["report_url"] = f"/reports/{data['id']}/index.html" if data["report_ready"] else None
    data["case_ids"] = json.loads(data.get("case_ids") or "[]")
    return data


def get_run(run_id: str) -> dict | None:
    rows = _query("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not rows:
        return None
    run = _row_to_run(rows[0])
    run["failed_cases"] = [
        {
            "case_id": r["case_id"],
            "name": r["name"],
            "module": r["module"],
            "status": r["status"],
            "message": r["message"],
        }
        for r in _query(
            "SELECT * FROM run_cases WHERE run_id = ? AND status IN ('failed', 'error') "
            "ORDER BY rowid", (run_id,)
        )
    ]
    return run


def list_runs(limit: int, offset: int) -> tuple[int, list[dict]]:
    total = _query("SELECT COUNT(*) AS c FROM runs")[0]["c"]
    rows = _query(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    items = [_row_to_run(r) for r in rows]
    for item in items:
        item.pop("failed_cases", None)
    return total, items


def get_running_run() -> dict | None:
    rows = _query("SELECT * FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1")
    return _row_to_run(rows[0]) if rows else None


def list_accuracy_runs(limit: int = 20) -> list[dict]:
    """返回所有含 accuracy_detail 的 run（即执行过审核正确性用例的 run）。"""
    rows = _query(
        "SELECT id, status, triggered_by, started_at, finished_at, duration_ms, accuracy_detail "
        "FROM runs WHERE accuracy_detail IS NOT NULL AND accuracy_detail != '' "
        "ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    items = []
    for r in rows:
        d = dict(r)
        try:
            detail = json.loads(d.pop("accuracy_detail"))
        except Exception:
            detail = []
        items.append({
            "run_id": d["id"],
            "status": d["status"],
            "triggered_by": d["triggered_by"],
            "started_at": d["started_at"],
            "finished_at": d["finished_at"],
            "duration_ms": d["duration_ms"],
            "total_contracts": len(detail),
            "diff_contracts": sum(1 for c in detail if c.get("diffCount", 0) > 0),
            "accuracy_data": detail,
        })
    return items


def get_accuracy_detail(run_id: str) -> dict | None:
    row = _query(
        "SELECT id, status, started_at, finished_at, accuracy_detail FROM runs WHERE id = ?",
        (run_id,),
    )
    if not row:
        return None
    d = dict(row[0])
    try:
        detail = json.loads(d.pop("accuracy_detail") or "[]")
    except Exception:
        detail = []
    return {
        "run_id": d["id"],
        "status": d["status"],
        "started_at": d["started_at"],
        "finished_at": d["finished_at"],
        "accuracy_data": detail,
    }


# —— 子步骤进度 ——

def append_sub_step(run_id: str, case_id: str, step: str, status: str, detail: str | None = None) -> None:
    """写一条子步骤事件（runner 尾随时调用）。"""
    _write(
        "INSERT INTO run_sub_steps (run_id, case_id, step, status, detail, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, case_id, step, status, detail, now_iso()),
    )


def list_sub_steps(run_id: str, case_id: str | None = None) -> list[dict]:
    """查询某次执行的子步骤（可选按用例过滤）。"""
    sql = "SELECT * FROM run_sub_steps WHERE run_id = ?"
    params: list = [run_id]
    if case_id:
        sql += " AND case_id = ?"
        params.append(case_id)
    sql += " ORDER BY id ASC"
    return [dict(r) for r in _query(sql, tuple(params))]


def get_latest_sub_step(run_id: str) -> dict | None:
    """SSE 轮询时用：取最新一条事件，有变化就推出去。"""
    rows = _query(
        "SELECT * FROM run_sub_steps WHERE run_id = ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    )
    return dict(rows[0]) if rows else None
