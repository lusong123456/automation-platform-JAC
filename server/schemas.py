"""API 请求模型。"""

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    """触发一次执行：勾选的用例 ID 列表 + 操作人。"""

    case_ids: list[str] = Field(default_factory=list, description="用例 ID 列表")
    triggered_by: str | None = Field(default=None, description="操作人，用于执行记录留痕")


# ──────────────────────────────────────────────────────────
# 合同-标答管理相关模型
# ──────────────────────────────────────────────────────────

class StandardIssue(BaseModel):
    """标答单条规则 issue。"""
    ruleId: str
    ruleIntent: str
    compliant: bool
    original: str = ""
    revised: str = ""
    suggestion: str = ""


class StandardGroup(BaseModel):
    """标答分组（parentRuleId 层级）。"""
    parentRuleId: str
    parentRuleIntent: str
    issues: list[StandardIssue]


class StandardSaveRequest(BaseModel):
    """保存标答 JSON 的请求体。"""
    data: list[StandardGroup]


class ContractTemplateRequest(BaseModel):
    """生成模板的可选参数。"""
    contract_type: str | None = Field(default=None, description="合同类型关键词，用于匹配 TYPE_SPECIFIC_REVIEW 组")
