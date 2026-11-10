"""系统设置接口：读取可配置项，保存后全部热生效。"""

from typing import Any

from fastapi import APIRouter, HTTPException

from server.config import settings
from server.core import settings_store

router = APIRouter(tags=["settings"])

FIELD_SPECS: list[dict[str, Any]] = [
    # —— 被测环境（只读展示，在 deploy/.env 中配置） ——
    {"key": "base_url", "group": "被测环境", "label": "被测环境地址", "type": "string",
     "readonly": True},
    {"key": "test_username", "group": "被测环境", "label": "被测系统账号", "type": "string",
     "readonly": True},
    {"key": "test_password", "group": "被测环境", "label": "被测系统密码", "type": "password",
     "readonly": True},
    # —— 执行与结果 ——
    {"key": "http_timeout", "group": "执行与结果", "label": "单接口超时（秒）", "type": "int",
     "min": 1, "max": 600},
    {"key": "run_timeout", "group": "执行与结果", "label": "整次执行超时（秒）", "type": "int",
     "min": 60, "max": 86400},
    {"key": "report_keep", "group": "执行与结果", "label": "结果保留份数", "type": "int",
     "min": 1, "max": 200},
    # —— AI 语义仲裁 ——
    {"key": "ai_judge_enabled", "group": "AI 语义仲裁", "label": "启用大模型复核", "type": "bool"},
    {"key": "llm_base_url", "group": "AI 语义仲裁", "label": "模型接口地址", "type": "string",
     "help": "OpenAI 兼容接口根地址，需包含 /v1"},
    {"key": "llm_api_key", "group": "AI 语义仲裁", "label": "API Key", "type": "password"},
    {"key": "llm_model", "group": "AI 语义仲裁", "label": "模型名", "type": "string"},
    {"key": "llm_timeout", "group": "AI 语义仲裁", "label": "单次调用超时（秒）", "type": "int",
     "min": 1, "max": 600},
    {"key": "suggestion_overlap_threshold", "group": "AI 语义仲裁", "label": "AI 仲裁阈值",
     "type": "float", "min": 0.01, "max": 1, "help": "关键词重叠率 ≥ 此值判语义一致"},
]

_SPEC_BY_KEY = {spec["key"]: spec for spec in FIELD_SPECS}
_READONLY_KEYS = {spec["key"] for spec in FIELD_SPECS if spec.get("readonly")}


def _check_range(spec: dict, value: float) -> float:
    low, high = spec.get("min"), spec.get("max")
    if (low is not None and value < low) or (high is not None and value > high):
        raise HTTPException(400, detail=f"{spec['label']} 需在 {low} ~ {high} 之间")
    return value


def _validate(spec: dict, value: Any) -> Any:
    key, kind = spec["key"], spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise HTTPException(400, detail=f"{spec['label']} 必须是布尔值")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(400, detail=f"{spec['label']} 必须是整数")
        return _check_range(spec, value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(400, detail=f"{spec['label']} 必须是数字")
        return _check_range(spec, float(value))
    if not isinstance(value, str):
        raise HTTPException(400, detail=f"{spec['label']} 必须是字符串")
    if kind == "select" and value not in [c["value"] for c in spec.get("choices", [])]:
        raise HTTPException(400, detail=f"{spec['label']} 取值不合法")
    if key == "base_url" and value and not value.startswith(("http://", "https://")):
        raise HTTPException(400, detail="被测环境地址必须以 http:// 或 https:// 开头")
    if kind == "password" and len(value) > 512:
        raise HTTPException(400, detail=f"{spec['label']} 过长")
    return value


@router.get("/settings")
def get_settings() -> dict:
    items = []
    for spec in FIELD_SPECS:
        key = spec["key"]
        value = getattr(settings, key)
        item: dict[str, Any] = {
            "key": key,
            "group": spec["group"],
            "label": spec["label"],
            "type": spec["type"],
            "help": spec.get("help", ""),
            "value": "" if key in settings_store.SENSITIVE_KEYS else value,
        }
        if key in settings_store.SENSITIVE_KEYS:
            item["configured"] = bool(value)
        if key in _READONLY_KEYS:
            item["readonly"] = True
        if spec.get("plain"):
            item["plain"] = True
        if spec.get("merged"):
            item["merged"] = True
        for extra in ("choices", "min", "max"):
            if extra in spec:
                item[extra] = spec[extra]
        items.append(item)
    return {"items": items}


@router.put("/settings")
def update_settings(body: dict[str, Any]) -> dict:
    unknown = set(body) - set(_SPEC_BY_KEY)
    if unknown:
        raise HTTPException(400, detail=f"不支持的配置项：{', '.join(sorted(unknown))}")

    clean: dict[str, Any] = {}
    for key, raw in body.items():
        spec = _SPEC_BY_KEY[key]
        if key in _READONLY_KEYS:
            raise HTTPException(400, detail=f"{spec['label']} 为只读项，请在 deploy/.env 中修改")
        if key in settings_store.SENSITIVE_KEYS and raw == "":
            continue  # 留空表示不修改
        clean[key] = _validate(spec, raw)

    if clean:
        settings_store.save(settings.data_dir, clean)
        settings_store.apply(settings, clean)

    return {"saved": list(clean), "logout_required": False}
