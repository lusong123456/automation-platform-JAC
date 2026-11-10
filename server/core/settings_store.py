"""配置覆盖层：data/settings.json 优先于环境变量，由系统设置页维护。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

FILE_NAME = "settings.json"

# 路径类字段（data_dir/db_path）不开放，避免 JSON 自己定位不了自己
EDITABLE_KEYS = {
    "base_url", "http_timeout", "run_timeout", "report_keep",
    "ai_judge_enabled", "llm_base_url", "llm_api_key", "llm_model", "llm_timeout",
    "suggestion_overlap_threshold",
    "auth_mode", "test_username", "test_password", "app_id", "app_secret", "app_employee_id",
}

SENSITIVE_KEYS = {"llm_api_key", "test_password"}


def load(data_dir: Path) -> dict[str, Any]:
    file = data_dir / FILE_NAME
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in data.items() if key in EDITABLE_KEYS}


def save(data_dir: Path, overrides: dict[str, Any]) -> None:
    """稀疏合并写入，临时文件 + 原子替换避免半截文件。"""
    merged = {**load(data_dir), **overrides}
    file = data_dir / FILE_NAME
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, file)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(target, overrides: dict[str, Any] | None = None) -> None:
    """把覆盖值写入运行时配置对象；不传 overrides 时读取文件全量。"""
    values = load(target.data_dir) if overrides is None else overrides
    for key, value in values.items():
        if key in EDITABLE_KEYS:
            setattr(target, key, value)
