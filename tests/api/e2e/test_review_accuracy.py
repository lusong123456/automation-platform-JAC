"""审核正确性用例：upload→commit→审核→拉取完整结果→和标准答案对比。

和 test_core_flows.py 的区别：后者只验 HTTP 200 + code=200 + data 非空；
本文件验审核结果的**语义正确性**——把后端返回的完整 issue JSON 和标准答案 JSON 做对比。

数据通道：所有合同跑完后汇总 diff 写到 ACCURACY_DIFF_FILE 环境变量指定位置，
runner.py 执行结束时读该文件存入 DB，后端 API 暴露给 Vue 展示。
"""
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import allure
import httpx
import pytest

from conftest import UPLOAD_TIMEOUT, assert_ok, first_id

from ai_judge import AiJudgeError, judge_suggestion_consistent, load_ai_judge_config

_regcase = pytest.mark.regcase

# Allure 报告左侧套件名（用例标题由 conftest 统一取 regcase.name）
pytestmark = [allure.suite("E2E-审核正确性")]

# ══════════════════════════════════════════════════════════════
# 一、纯 Python 比对函数（从 test_procurement_review.py 搬，零外部依赖）
# ══════════════════════════════════════════════════════════════


def _extract_keywords(text: str) -> set:
    """从文本中提取关键词集合（中文 2~4 字滑动窗口 + 英文单词）。"""
    if not text or not text.strip():
        return set()
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]', ' ', text)
    en_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{2,}', cleaned))
    cn_chars = re.findall(r'[\u4e00-\u9fff]+', cleaned)
    cn_words: set = set()
    for segment in cn_chars:
        if len(segment) >= 2:
            for ws in (2, 3):
                for i in range(len(segment) - ws + 1):
                    cn_words.add(segment[i:i + ws])
            if len(segment) <= 6:
                cn_words.add(segment)
    stop_words = {
        "的", "了", "是", "在", "和", "与", "或", "等", "为", "对",
        "请", "应", "需", "须", "未", "不", "无", "有", "可",
        "合同", "条款", "建议", "审核", "检测", "是否",
        "the", "is", "are", "was", "were", "a", "an", "to", "of",
        "in", "for", "and", "or", "not", "be", "this", "that",
    }
    return (cn_words - stop_words) | (en_words - stop_words)


def _keyword_overlap_rate(set_a: set, set_b: set) -> float:
    """Jaccard 相似系数：|A∩B| / |A∪B|。"""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def compare_suggestion_semantic(expected: str, actual: str, threshold: float = 0.3) -> dict:
    """对两条建议文本做关键词重叠率比对。"""
    expected_kw = _extract_keywords(expected)
    actual_kw = _extract_keywords(actual)
    overlap = _keyword_overlap_rate(expected_kw, actual_kw)
    common = sorted(expected_kw & actual_kw)
    match = overlap >= threshold
    desc = (
        f"语义一致（重叠率={overlap:.1%}）" if match
        else f"语义不一致（重叠率={overlap:.1%}，阈值={threshold:.0%}）"
    )
    return {
        "semantic_match": match,
        "overlap_rate": round(overlap, 4),
        "expected_keywords": sorted(expected_kw),
        "actual_keywords": sorted(actual_kw),
        "common_keywords": common,
        "desc": desc,
    }


def _flatten_grouped_json(data: list[dict]) -> dict[str, dict[str, Any]]:
    """把后端分组格式展平成 ruleId → {compliant, suggestion, original, revised} 的 dict。"""
    flat: dict[str, dict[str, Any]] = {}
    for item in data:
        if "issues" in item and isinstance(item["issues"], list):
            for issue in item["issues"]:
                rule_id = issue.get("ruleId", "")
                rule_intent = issue.get("ruleIntent", "")
                key = rule_id if rule_id else rule_intent
                if not key:
                    continue
                suggestion = issue.get("suggestion", "") or issue.get("expectedSuggestion", "")
                flat[key] = {
                    "ruleId": rule_id,
                    "ruleIntent": rule_intent,
                    "compliant": issue.get("compliant", False),
                    "suggestion": suggestion,
                    "original": issue.get("original", "") or "",
                    "revised": issue.get("revised", "") or "",
                }
        else:
            rule_id = item.get("ruleId", "")
            rule_intent = item.get("ruleIntent", "")
            key = rule_id if rule_id else rule_intent
            if not key:
                continue
            flat[key] = {
                "ruleId": rule_id,
                "ruleIntent": rule_intent,
                "compliant": item.get("compliant", False),
                "suggestion": item.get("suggestion", "") or item.get("expectedSuggestion", ""),
                "original": item.get("original", "") or "",
                "revised": item.get("revised", "") or "",
            }
    return flat


def _rule_base_id(rule_id: str) -> str:
    """去掉 ruleId 末尾的运行序号后缀：JAC-ZS-xxx_3 → JAC-ZS-xxx；裸 ID 原样返回。

    后端对同一规则的多处命中按「本次运行」的顺序编号（_1、_2…），编号跨运行不稳定，
    不能作为匹配键；规则基 ID + 命中原文才是稳定标识。
    """
    return re.sub(r"_\d+$", "", (rule_id or "").strip())


def _group_by_rule_base(data: list[dict]) -> dict[str, list[dict]]:
    """把审核结果按规则基 ID 分组，组内保持出现顺序，条目结构与 _flatten_grouped_json 一致。

    同一规则的多次命中各自保留为独立条目（不互相覆盖），这是与 _flatten_grouped_json
    （字典后条覆盖前条）的本质区别。
    """
    groups: dict[str, list[dict]] = {}
    for item in data:
        issues = item["issues"] if "issues" in item and isinstance(item["issues"], list) else [item]
        for issue in issues:
            rule_id = issue.get("ruleId", "")
            rule_intent = issue.get("ruleIntent", "")
            key = _rule_base_id(rule_id) or rule_intent
            if not key:
                continue
            groups.setdefault(key, []).append({
                "ruleId": rule_id,
                "ruleIntent": rule_intent,
                "compliant": issue.get("compliant", False),
                "suggestion": issue.get("suggestion", "") or issue.get("expectedSuggestion", ""),
                "original": issue.get("original", "") or "",
                "revised": issue.get("revised", "") or "",
            })
    return groups


def _pair_group_items(std_list: list[dict], act_list: list[dict],
                      threshold: float = 0.3) -> list[tuple]:
    """组内配对：按命中原文（空则建议文本）的关键词 Jaccard 相似度做全局贪心配对。

    返回 [(std_info|None, act_info|None)]；配上的进入字段级比对，
    标答剩余记 missing（真实漏审），实际剩余记 extra（额外发现）。

    相似度复用 _extract_keywords + _keyword_overlap_rate：
    两边文本都空 = 1.0（互配），一方空一方不空 = 0.0（不配）。
    全局贪心（按相似度降序、互斥取对）在组内小规模条数下与最优解一致，且行为可预测。
    """
    s_texts = [s.get("original") or s.get("suggestion") or s.get("revised") or "" for s in std_list]
    a_texts = [a.get("original") or a.get("suggestion") or a.get("revised") or "" for a in act_list]
    s_kws = [_extract_keywords(t) for t in s_texts]
    a_kws = [_extract_keywords(t) for t in a_texts]

    candidates: list[tuple[float, int, int]] = []
    for i, s_kw in enumerate(s_kws):
        for j, a_kw in enumerate(a_kws):
            sim = _keyword_overlap_rate(s_kw, a_kw)
            if sim >= threshold:
                candidates.append((sim, i, j))
    candidates.sort(key=lambda t: t[0], reverse=True)

    std_used: set[int] = set()
    act_used: set[int] = set()
    pairs: list[tuple] = []
    for sim, i, j in candidates:
        if i in std_used or j in act_used:
            continue
        std_used.add(i)
        act_used.add(j)
        pairs.append((std_list[i], act_list[j]))
    for i, std_info in enumerate(std_list):
        if i not in std_used:
            pairs.append((std_info, None))
    for j, act_info in enumerate(act_list):
        if j not in act_used:
            pairs.append((None, act_info))
    return pairs


def compare_results_with_standard(actual_items: list[dict], standard_items: list[dict],
                                  suggestion_threshold: float = 0.3,
                                  ai_judge: dict | None = None,
                                  pair_threshold: float = 0.3,
                                  group_match: bool = True) -> dict:
    """把后端实际审核结果和标准答案 JSON 做全量对比（双向）。

    匹配模式（ACC_GROUP_MATCH，默认 true）：
      true  —— 两级匹配：按规则基 ID（ruleId 去掉运行序号后缀 _N）分组，组内按命中
               原文相似度（ACC_PAIR_THRESHOLD）配对后逐对比对。一条规则多处命中时
               不受运行序号漂移影响，missing/extra 才是真实的漏审/多查。
      false —— 兼容模式（改造前行为）：ruleId（空则 ruleIntent）精确相等匹配，
               同 ID 多条时后者覆盖前者。

    ai_judge：AI 仲裁配置（load_ai_judge_config() 的返回值）。None 表示未开启，
              行为与改造前完全一致（纯关键词判定）。

    AI 仲裁规则：关键词判「不一致」时调用大模型复核（scope=all 时每对都调）；
      模型判一致 → 该条转为 match；模型判不一致 → 保留差异；
      模型调用失败/超时/响应非法 → 该条记 status=ai_error 并计入失败，不降级。

    返回 dict：
      items: 全量规则对比记录（含一致项），每条带 status：
             match / compliant / suggestion / both / missing / extra / ai_error
      matchCount / diffCount / extraCount / aiErrorCount: 统计计数
    """
    empty = {"items": [], "matchCount": 0, "diffCount": 0, "extraCount": 0, "aiErrorCount": 0}
    if not isinstance(standard_items, list) or not isinstance(actual_items, list):
        return empty
    ai_scope = (ai_judge or {}).get("scope", "inconsistent")

    def _info_dict(info: dict) -> dict:
        return {
            "compliant": info["compliant"],
            "suggestion": info["suggestion"],
            "original": info.get("original", ""),
            "revised": info.get("revised", ""),
        }

    items: list[dict] = []
    match_count = 0
    diff_count = 0
    ai_error_count = 0
    extra_count = 0

    # 组装配对流水线：(标答条目|None, 实际条目|None)
    if group_match:
        std_groups = _group_by_rule_base(standard_items)
        act_groups = _group_by_rule_base(actual_items)
        pairs: list[tuple] = []
        for base_id, std_list in std_groups.items():
            pairs.extend(_pair_group_items(std_list, act_groups.pop(base_id, []), pair_threshold))
        for act_list in act_groups.values():  # 实际有、标答没有 → extra
            pairs.extend((None, a) for a in act_list)
    else:
        # 兼容模式：ruleId（空则 ruleIntent）精确相等匹配
        std_map = _flatten_grouped_json(standard_items)
        act_map = _flatten_grouped_json(actual_items)
        pairs = []
        for rule_key, std_info in std_map.items():
            pairs.append((std_info, act_map.get(rule_key)))
        for rule_key, act_info in act_map.items():
            if rule_key not in std_map:
                pairs.append((None, act_info))

    for std_info, act_info in pairs:
        # —— 实际有、标答没有 → extra（额外发现，默认不计失败，仅提示人工评审标答是否漏标）——
        if std_info is None:
            items.append({
                "ruleId": act_info["ruleId"], "ruleIntent": act_info["ruleIntent"],
                "status": "extra",
                "desc": f"后端额外发现（标答未覆盖该规则），实际判定={'合规' if act_info['compliant'] else '不合规'}",
                "compliantDiff": False, "suggestionDiff": None,
                "expected": None,
                "actual": _info_dict(act_info),
            })
            extra_count += 1
            continue

        std_compliant = std_info["compliant"]
        std_suggestion = std_info["suggestion"]
        rule_id = std_info["ruleId"]
        rule_intent = std_info["ruleIntent"]

        expected = _info_dict(std_info)

        # —— 标答有、实际缺失 → missing（组模式下即真实漏审）——
        if act_info is None:
            items.append({
                "ruleId": rule_id, "ruleIntent": rule_intent,
                "status": "missing",
                "desc": "标答有此规则，实际审核结果缺失",
                "compliantDiff": True, "suggestionDiff": None,
                "expected": expected,
                "actual": None,
            })
            diff_count += 1
            continue

        act_compliant = act_info["compliant"]
        act_suggestion = act_info["suggestion"]
        compliant_diff = (std_compliant != act_compliant)

        suggestion_diff = None
        suggestion_diff_flag = False
        ai_judgment = None
        ai_error = None
        if not std_compliant and not act_compliant and std_suggestion:
            sem = compare_suggestion_semantic(std_suggestion, act_suggestion, threshold=suggestion_threshold)
            keyword_match = sem["semantic_match"]
            suggestion_diff_flag = not keyword_match

            # AI 仲裁：两段建议都有内容，且（关键词判不一致 或 AI_JUDGE_SCOPE=all）时介入
            if ai_judge and act_suggestion and (ai_scope == "all" or not keyword_match):
                try:
                    ai_judgment = judge_suggestion_consistent(std_suggestion, act_suggestion, ai_judge)
                except AiJudgeError as exc:
                    ai_error = str(exc)
                else:
                    suggestion_diff_flag = not ai_judgment["consistent"]

            if suggestion_diff_flag:
                suggestion_diff = {
                    "semantic_match": False,
                    "overlap_rate": sem["overlap_rate"],
                    "expectedSuggestion": std_suggestion,
                    "actualSuggestion": act_suggestion,
                    "common_keywords": sem["common_keywords"],
                    "judge": "ai" if ai_judgment else "keyword",
                    "desc": (
                        f"AI 判定不一致：{ai_judgment['reason']}" if ai_judgment
                        else sem["desc"]
                    ),
                }

        # AI 仲裁失败：直接判失败，不降级、不沿用关键词结论
        if ai_error:
            items.append({
                "ruleId": rule_id, "ruleIntent": rule_intent,
                "status": "ai_error",
                "desc": f"AI 语义仲裁失败：{ai_error}",
                "compliantDiff": compliant_diff, "suggestionDiff": None,
                "expected": expected,
                "actual": _info_dict(act_info),
                "aiJudgment": {
                    "consistent": None, "reason": "",
                    "model": ai_judge["model"], "error": ai_error,
                },
            })
            ai_error_count += 1
            continue

        # 一致项也记录，供页面"展示通过项"
        if not (compliant_diff or suggestion_diff_flag):
            item = {
                "ruleId": rule_id, "ruleIntent": rule_intent,
                "status": "match",
                "desc": "AI 仲裁：一致" if ai_judgment else "一致",
                "compliantDiff": False, "suggestionDiff": None,
                "expected": expected,
                "actual": _info_dict(act_info),
            }
            if ai_judgment:
                item["aiJudgment"] = ai_judgment
            items.append(item)
            match_count += 1
            continue

        # 确定差异类型（suggestion_diff 仅在建议判定为不一致时才有值）
        if compliant_diff and suggestion_diff_flag:
            status = "both"
            label = "AI 判定建议语义不一致" if ai_judgment else "建议语义不一致"
            desc = f"合规判定不一致 + {label}（重叠率={suggestion_diff['overlap_rate']:.1%}）"
        elif compliant_diff:
            status = "compliant"
            desc = (
                f"合规判定不一致：标答={'合规' if std_compliant else '不合规'}，"
                f"实际={'合规' if act_compliant else '不合规'}"
            )
        else:
            status = "suggestion"
            desc = (
                f"AI 判定建议语义不一致（重叠率={suggestion_diff['overlap_rate']:.1%}）" if ai_judgment
                else f"建议语义不一致（重叠率={suggestion_diff['overlap_rate']:.1%}）"
            )

        diff_item = {
            "ruleId": rule_id, "ruleIntent": rule_intent,
            "status": status,
            "desc": desc,
            "compliantDiff": compliant_diff, "suggestionDiff": suggestion_diff,
            "expected": expected,
            "actual": _info_dict(act_info),
        }
        if ai_judgment:
            diff_item["aiJudgment"] = ai_judgment
        items.append(diff_item)
        diff_count += 1

    return {"items": items, "matchCount": match_count, "diffCount": diff_count,
            "extraCount": extra_count, "aiErrorCount": ai_error_count}


# ══════════════════════════════════════════════════════════════
# 二、后端 itemViews → 分组格式 字段映射
# ══════════════════════════════════════════════════════════════


def _map_item_views_to_grouped(item_views: list[dict]) -> list[dict]:
    """把后端 report/view 返回的 itemViews 转成分组格式 [{parentRuleId, parentRuleIntent, issues}]。"""
    grouped = []
    for g in item_views:
        issues = []
        for issue in g.get("issues", []) or []:
            issues.append({
                "ruleId": issue.get("ruleId", ""),
                "ruleIntent": issue.get("intent", ""),
                "compliant": issue.get("compliant", False),
                "original": issue.get("original", ""),
                "revised": issue.get("revised", ""),
                "suggestion": issue.get("suggestion", ""),
            })
        grouped.append({
            "parentRuleId": g.get("ruleId", ""),
            "parentRuleIntent": g.get("intent", ""),
            "issues": issues,
        })
    return grouped


# ══════════════════════════════════════════════════════════════
# 三、测试数据 fixture
# ══════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# 合同文件和标准答案随镜像打包到 /app/data/ 下；本地裸跑时读仓库同级目录
_contracts_env = os.getenv("TEST_CONTRACTS_DIR", "")
_standard_env = os.getenv("STANDARD_RESULTS_DIR", "")
TEST_CONTRACTS_DIR = Path(_contracts_env) if _contracts_env else (REPO_ROOT / "data" / "test-contracts")
STANDARD_RESULTS_DIR = Path(_standard_env) if _standard_env else (REPO_ROOT / "data" / "standard-results")

def _auto_pair_contracts() -> list[tuple[str, str]]:
    """自动扫描 test-contracts/ 和 standard-results/，按文件名前缀配对。

    约定：合同 xxx.docx 对应标答 xxx.json；若不存在则跳过。
    加新合同时直接丢文件到两个目录即可，无需改代码。
    """
    pairs: list[tuple[str, str]] = []
    for docx in sorted(TEST_CONTRACTS_DIR.glob("*.docx")):
        std = STANDARD_RESULTS_DIR / f"{docx.stem}.json"
        if std.exists():
            pairs.append((docx.name, std.name))
    return pairs


def _load_accuracy_diff_collector():
    """读取已有的 accuracy 汇总 diff，避免覆盖其他用例。"""
    collector_path = os.getenv("ACCURACY_DIFF_FILE")
    if collector_path:
        p = Path(collector_path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return []


def _save_accuracy_diff_collector(acc_diff_file: str, collector: list[dict]) -> None:
    Path(acc_diff_file).parent.mkdir(parents=True, exist_ok=True)
    Path(acc_diff_file).write_text(json.dumps(collector, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# 四、辅助：单合同审核完整链路
# ══════════════════════════════════════════════════════════════


def _run_single_contract_review(api: httpx.Client, docx_path: Path, poll_timeout: int = 360) -> list[dict]:
    """跑一条合同的完整审核链路，返回后端分组格式的审核结果。

    链路（实测验证过）：
      1. POST /ai-contract/file/upload
      2. POST /ai-contract/file/extract/keyword?fileId=xxx
      3. POST /ai-contract/file/task/commit?taskId=xxx
      4. 轮询 GET /ai-contract/file/task/item?taskCode=xxx → taskStatus=FINISHED
      5. GET /ai-contract/file/report/view?taskCode=xxx&fileId=xxx
    """
    # 把 progress 钩子带进来（conftest.py 里定义）
    from conftest import _progress

    # Step 1: upload
    _progress({"step": "upload", "status": "running", "case": docx_path.name})
    with allure.step(f"上传合同 {docx_path.name}"):
        with open(docx_path, "rb") as fh:
            r = api.post("/ai-contract/file/upload",
                         files={"file": (docx_path.name, fh, "application/octet-stream")},
                         timeout=UPLOAD_TIMEOUT)
        j1 = assert_ok(r, "合同上传")
        data = j1["data"]
        file_id = data["id"]
        task_id = data["taskId"]
        task_code = data["taskCode"]
        allure.attach(str(file_id), name="fileId", attachment_type=allure.attachment_type.TEXT)
        allure.attach(str(task_id), name="taskId", attachment_type=allure.attachment_type.TEXT)
        allure.attach(task_code, name="taskCode", attachment_type=allure.attachment_type.TEXT)
    _progress({"step": "upload", "status": "done"})

    # Step 2: extract/keyword（预解析，建议做）
    _progress({"step": "extract", "status": "running"})
    with allure.step("预解析：关键字段提取"):
        r2 = api.post("/ai-contract/file/extract/keyword", params={"fileId": file_id})
        assert_ok(r2, "预解析")
    _progress({"step": "extract", "status": "done"})

    # Step 3: commit
    _progress({"step": "commit", "status": "running"})
    with allure.step("提交审核 task/commit"):
        r3 = api.post("/ai-contract/file/task/commit", params={"taskId": task_id})
        assert_ok(r3, "task/commit")
    _progress({"step": "commit", "status": "done"})

    # Step 4: 轮询等审核完成
    poll_total = poll_timeout // 5
    _progress({"step": "poll_start", "status": "running", "total": poll_total})
    with allure.step(f"轮询 task/item（最多 {poll_timeout}s）"):
        status = None
        elapsed = 0
        poll_idx = 0
        while elapsed < poll_timeout:
            time.sleep(5)
            elapsed += 5
            poll_idx += 1
            r4 = api.get("/ai-contract/file/task/item", params={"taskCode": task_code})
            j4 = r4.json()
            data4 = j4.get("data") or {}
            status = data4.get("taskStatus") or data4.get("auditStatus")
            allure.attach(f"t={elapsed}s status={status}", name="轮询快照", attachment_type=allure.attachment_type.TEXT)
            _progress({"step": "poll", "status": "running", "idx": poll_idx, "total": poll_total, "auditStatus": status})
            if status in ("FINISHED", "FINISH", "COMPLETED", "DONE", "SUCCESS"):
                break
            if status in ("FAIL", "ERROR", "FAILED", "REJECTED"):
                pytest.fail(f"审核失败 status={status} data={data4}")
        else:
            pytest.fail(f"审核超时：{poll_timeout}s 内未完成，最后 status={status}")
    _progress({"step": "poll", "status": "done", "idx": poll_idx, "total": poll_total})

    # Step 5: report/view
    _progress({"step": "report", "status": "running"})
    with allure.step("拉取完整审核结果 report/view"):
        r5 = api.get("/ai-contract/file/report/view", params={"taskCode": task_code, "fileId": file_id})
        j5 = assert_ok(r5, "report/view")
        item_views = (j5.get("data") or {}).get("itemViews") or []
        allure.attach(
            json.dumps({"itemViews_count": len(item_views)}, ensure_ascii=False),
            name="审核结果概览", attachment_type=allure.attachment_type.JSON,
        )
    _progress({"step": "report", "status": "done", "issues": len(item_views)})

    # 字段映射
    return _map_item_views_to_grouped(item_views)


# ══════════════════════════════════════════════════════════════
# 五、pytest 用例
# ══════════════════════════════════════════════════════════════


# 自动扫描目录，动态生成 accuracy 用例（每个合同一条，带独立 case_id）
for _i, (_docx_name, _std_json_name) in enumerate(_auto_pair_contracts(), start=1):
    _case_id = f"ACC-{_i:03d}"
    _case_name = Path(_docx_name).stem

    def _make_test(docx_name: str, std_json_name: str, case_id: str, case_name: str):
        @_regcase(id=case_id, module="审核正确性", name=case_name)
        @allure.story("E2E-审核正确性")
        @allure.severity(allure.severity_level.CRITICAL)
        def _test(api):
            _run_accuracy(api, docx_name, std_json_name)
        _test.__name__ = f"test_accuracy_{case_id.lower()}"
        _test.__doc__ = f"审核正确性：{case_name}"
        return _test

    globals()[f"test_accuracy_{_case_id.lower()}"] = _make_test(
        _docx_name, _std_json_name, _case_id, _case_name
    )


def _run_accuracy(api: httpx.Client, docx_name: str, std_json_name: str) -> None:
    """单合同审核正确性测试的公共主体。"""
    docx_path = TEST_CONTRACTS_DIR / docx_name
    std_path = STANDARD_RESULTS_DIR / std_json_name
    acc_diff_file = os.getenv("ACCURACY_DIFF_FILE")

    if not docx_path.exists():
        pytest.skip(f"测试合同不存在：{docx_path}")
    if not std_path.exists():
        pytest.skip(f"标准答案不存在：{std_path}")

    # 0. AI 仲裁配置校验：开启但配置不全/非法 → 直接失败，不进入审核链路
    try:
        ai_judge_cfg = load_ai_judge_config()
    except AiJudgeError as exc:
        pytest.fail(f"AI 语义仲裁配置错误：{exc}")
    if ai_judge_cfg:
        allure.attach(
            json.dumps({"model": ai_judge_cfg["model"], "scope": ai_judge_cfg["scope"],
                        "timeout": ai_judge_cfg["timeout"]}, ensure_ascii=False, indent=2),
            name="AI 仲裁配置", attachment_type=allure.attachment_type.JSON,
        )

    # 1. 跑审核链路，拿后端实际结果
    actual_items = _run_single_contract_review(api, docx_path)

    # 2. 加载标准答案
    with allure.step(f"加载标准答案 {std_json_name}"):
        standard_items = json.loads(std_path.read_text(encoding="utf-8"))

    # 3. 语义比对（全量双向：含一致项 match 和额外发现项 extra）
    with allure.step("语义比对：实际审核结果 vs 标准答案"):
        threshold_raw = (os.getenv("SUGGESTION_OVERLAP_THRESHOLD") or "0.3").strip()
        try:
            suggestion_threshold = float(threshold_raw)
        except ValueError:
            pytest.exit(f"SUGGESTION_OVERLAP_THRESHOLD 必须是数字，当前值：{threshold_raw!r}")
        if not 0 < suggestion_threshold <= 1:
            pytest.exit(f"SUGGESTION_OVERLAP_THRESHOLD 必须在 (0, 1] 区间，当前值：{suggestion_threshold}")
        # 匹配模式：组内配对（默认，与运行序号解耦）；false 回退 ruleId 精确匹配
        group_match = (os.getenv("ACC_GROUP_MATCH") or "true").strip().lower() not in ("0", "false", "no", "off")
        pair_threshold = suggestion_threshold
        if group_match and (os.getenv("ACC_PAIR_THRESHOLD") or "").strip():
            pair_raw = os.getenv("ACC_PAIR_THRESHOLD").strip()
            try:
                pair_threshold = float(pair_raw)
            except ValueError:
                pytest.exit(f"ACC_PAIR_THRESHOLD 必须是数字，当前值：{pair_raw!r}")
            if not 0 < pair_threshold <= 1:
                pytest.exit(f"ACC_PAIR_THRESHOLD 必须在 (0, 1] 区间，当前值：{pair_raw}")
        compare_result = compare_results_with_standard(
            actual_items, standard_items,
            suggestion_threshold=suggestion_threshold,
            ai_judge=ai_judge_cfg,
            pair_threshold=pair_threshold,
            group_match=group_match,
        )
    items = compare_result["items"]
    match_count = compare_result["matchCount"]
    diff_count = compare_result["diffCount"]
    extra_count = compare_result["extraCount"]
    ai_error_count = compare_result["aiErrorCount"]
    diff_items = [i for i in items if i["status"] in ("compliant", "suggestion", "both", "missing", "ai_error")]

    # 4. Allure 留证
    allure.attach(
        json.dumps({"total_actual": len(_flatten_grouped_json(actual_items)),
                    "total_standard": len(_flatten_grouped_json(standard_items)),
                    "match_mode": "grouped" if group_match else "ruleid",
                    "match_count": match_count,
                    "diff_count": diff_count,
                    "extra_count": extra_count,
                    "ai_error_count": ai_error_count}, ensure_ascii=False, indent=2),
        name="对比统计", attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(items, ensure_ascii=False, indent=2),
        name="全量比对详情", attachment_type=allure.attachment_type.JSON,
    )
    for d in diff_items:
        step_text = f"ruleId={d['ruleId']} status={d['status']}"
        with allure.step(step_text):
            allure.attach(json.dumps(d, ensure_ascii=False, indent=2),
                          name=d["ruleIntent"], attachment_type=allure.attachment_type.JSON)

    # 5. 先写 diff 汇总文件（必须在 pytest.fail 之前，否则有差异时文件永远写不出来）
    if acc_diff_file:
        collector = _load_accuracy_diff_collector()
        collector.append({
            "contractName": docx_name,
            "standardFile": std_json_name,
            "totalRules": len(_flatten_grouped_json(standard_items)),
            "actualRules": len(_flatten_grouped_json(actual_items)),
            "matchCount": match_count,
            "diffCount": diff_count,
            "extraCount": extra_count,
            "aiErrorCount": ai_error_count,
            "items": items,
        })
        _save_accuracy_diff_collector(acc_diff_file, collector)

    # 6. 断言（diff 已写入文件，runner 能读到并入库供页面展示）
    #    extra（后端额外发现）默认不计失败；ACC_EXTRA_AS_DIFF=true 时计入
    #    ai_error（AI 仲裁调用失败/超时/响应非法）一律计入失败，不降级
    extra_as_diff = os.getenv("ACC_EXTRA_AS_DIFF", "").strip().lower() in ("1", "true", "yes")
    fail_count = diff_count + ai_error_count + (extra_count if extra_as_diff else 0)
    if fail_count:
        pytest.fail(
            f"审核结果与标准答案存在差异（差异 {diff_count} 条，AI 仲裁失败 {ai_error_count} 条，"
            f"额外发现 {extra_count} 条{'（extra 按配置计入失败）' if extra_as_diff else '（extra 不计入失败）'}）\n"
            f"详细对比见 Allure 附件「全量比对详情」"
        )
