// 后端 API 契约类型定义（字段名与后端严格一致）

/** 单个用例 */
export interface CaseItem {
  id: string
  name: string
  nodeid: string
}

/** 按模块分组的用例集合 */
export interface ModuleGroup {
  name: string
  category: 'flow' | 'contract'
  cases: CaseItem[]
}

/** GET /api/cases、POST /api/cases/refresh 响应 */
export interface CasesResponse {
  collected_at: string
  total: number
  collect_error: string | null
  modules: ModuleGroup[]
}

/** 执行状态：running 为中间态，其余为终态 */
export type RunStatus = 'running' | 'passed' | 'failed' | 'error'

/** 失败用例明细 */
export interface FailedCase {
  case_id: string
  name: string
  module: string
  status: string
  message: string
}

/** 执行记录行（列表项，无 failed_cases） */
export interface RunListItem {
  run_id: string
  status: RunStatus
  triggered_by: string | null
  case_total: number
  case_executed: number
  case_passed: number
  case_failed: number
  current_case: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  report_url: string | null
  report_ready: boolean
  error_message: string | null
}

/** GET /api/runs/{run_id} 响应 */
export interface RunDetail extends RunListItem {
  failed_cases: FailedCase[]
  /** 子步骤实时事件（SSE 推送） */
  sub_steps?: SubStepEvent[]
}

/** 报告页的单条用例结果 */
export interface ReportCase {
  case_id: string
  nodeid: string
  name: string
  module: string
  category: 'flow' | 'contract'
  status: string
  message: string
  duration_ms: number
}

/** 报告页按模块分组的用例统计 */
export interface ReportModule {
  name: string
  category: 'flow' | 'contract'
  /** flow：IT 流程；contract：合同功能；accuracy：审核正确性 */
  kind: 'flow' | 'contract' | 'accuracy'
  total: number
  passed: number
  failed: number
  skipped: number
  cases: ReportCase[]
}

/** GET /api/runs/{run_id}/report-data 响应 */
export interface RunReportData {
  run: RunListItem
  cases: ReportCase[]
  modules: ReportModule[]
  accuracy: AccuracyRunDetail | null
}

/** 子步骤进度事件（来自 /api/runs/{id}/stream SSE 或 /api/runs/active） */
export interface SubStepEvent {
  id: number
  run_id: string
  case_id: string
  step: string              // upload / extract / commit / poll / report / compare / case_start / case_end
  status: string            // running / done / fail
  detail?: string | null     // 扩展信息：idx/total=18/72 等
  ts: string
}

/** GET /api/runs 响应 */
export interface RunsResponse {
  total: number
  items: RunListItem[]
}

/** POST /api/runs 成功响应 */
export interface RunCreateResponse {
  run_id: string
  status: string
  case_total: number
  started_at: string
}

// ──────────────────────────────────────────────────────────
// 审核正确性（Accuracy）相关类型
// ──────────────────────────────────────────────────────────

/** 每条规则的差异（compliant 或 suggestion） */
export interface AccuracySuggestionDiff {
  semantic_match: boolean
  overlap_rate: number
  expectedSuggestion: string
  actualSuggestion: string
  common_keywords: string[]
  desc: string
  /** 该结论的判定来源：keyword（关键词算法）/ ai（大模型仲裁） */
  judge?: 'keyword' | 'ai'
}

/** AI 仲裁结论（成功时为模型输出，失败时带 error） */
export interface AccuracyAiJudgment {
  /** 失败时为 null */
  consistent: boolean | null
  reason: string
  model: string
  /** 仅失败时存在：超时/连接失败/响应非法等具体原因 */
  error?: string
}

/** 单条规则的审核结果（标答或实际） */
export interface AccuracyRuleResult {
  compliant: boolean | null
  suggestion: string
  original: string
  revised: string
}

/** 差异类型（旧结构 diffs 数组使用） */
export type AccuracyDiffType = 'compliant' | 'suggestion' | 'both' | 'missing'

export interface AccuracyRuleDiff {
  ruleId: string
  ruleIntent: string
  diffType: AccuracyDiffType
  desc: string
  compliantDiff: boolean
  suggestionDiff: AccuracySuggestionDiff | null
  expected: AccuracyRuleResult
  actual: AccuracyRuleResult
}

/** 全量比对记录的状态（新结构 items 数组使用） */
export type AccuracyItemStatus =
  | 'match' | 'compliant' | 'suggestion' | 'both' | 'missing' | 'extra' | 'ai_error'

export interface AccuracyCompareItem {
  ruleId: string
  ruleIntent: string
  status: AccuracyItemStatus
  desc: string
  compliantDiff: boolean
  suggestionDiff: AccuracySuggestionDiff | null
  /** extra（后端额外发现）时为 null —— 标答未覆盖该规则 */
  expected: AccuracyRuleResult | null
  /** missing（实际漏审）时为 null */
  actual: AccuracyRuleResult | null
  /** AI 仲裁参与过该条时存在（成功或失败） */
  aiJudgment?: AccuracyAiJudgment
}

/** 单个合同的审核正确性结果 */
export interface AccuracyContract {
  contractName: string
  standardFile: string
  totalRules: number
  actualRules: number
  diffCount: number
  /** 新结构：一致/额外发现/AI 仲裁失败计数与全量比对记录 */
  matchCount?: number
  extraCount?: number
  aiErrorCount?: number
  items?: AccuracyCompareItem[]
  /** 旧结构兼容：仅差异列表 */
  diffs?: AccuracyRuleDiff[]
}

/** GET /api/accuracy/runs 列表项 */
export interface AccuracyRunSummary {
  run_id: string
  status: RunStatus
  triggered_by: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  total_contracts: number
  diff_contracts: number
}

/** GET /api/accuracy/runs/{run_id} 详情 + items 列表里的完整项 */
export interface AccuracyRunDetail extends AccuracyRunSummary {
  accuracy_data: AccuracyContract[]
}

// ──────────────────────────────────────────────────────────
// 合同-标答管理相关类型
// ──────────────────────────────────────────────────────────

/** 单个合同在列表里的项 */
export interface ContractItem {
  name: string
  stem: string
  size_kb: number
  uploaded_at: string
  has_standard: boolean
  standard_name?: string
}

/** GET /api/contracts 响应 */
export interface ContractsResponse {
  items: ContractItem[]
}

/** 标答单条 issue */
export interface StandardIssue {
  ruleId: string
  ruleIntent: string
  compliant: boolean
  original: string
  revised: string
  suggestion: string
}

/** 标答分组 */
export interface StandardGroup {
  parentRuleId: string
  parentRuleIntent: string
  issues: StandardIssue[]
}

/** GET /api/standards/{name} 响应 */
export interface StandardResponse {
  name: string
  stem: string
  data: StandardGroup[]
}

/** GET /api/template 响应 */
export interface TemplateResponse {
  contract_type: string | null
  groups: StandardGroup[]
  total_issues: number
}

/** POST /api/contracts/ai-draft 响应 */
export interface AiDraftResponse {
  contract_name: string
  standard_data: StandardGroup[]
  total_groups: number
  total_issues: number
  note: string
}

// ──────────────────────────────────────────────────────────
// 系统设置相关类型
// ──────────────────────────────────────────────────────────

export type SettingsValueType = 'string' | 'int' | 'float' | 'bool' | 'password' | 'select'

export interface SettingsChoice {
  label: string
  value: string
}

/** 单个配置项；敏感项 value 恒为空，只返回 configured */
export interface SettingsItem {
  key: string
  group: string
  label: string
  type: SettingsValueType
  value: string | number | boolean
  help: string
  configured?: boolean
  readonly?: boolean
  /** 只读密码项为 true 时明文展示（用于平台登录密码） */
  plain?: boolean
  /** 与上一项合并到同一行展示（仅占位，不单独渲染） */
  merged?: boolean
  choices?: SettingsChoice[]
  min?: number
  max?: number
}

export interface SettingsResponse {
  items: SettingsItem[]
}

/** 平台用户（设置页用户管理） */
export interface ManagedUser {
  id: number
  username: string
  display_name: string
  created_at: string
}

export interface UsersResponse {
  items: ManagedUser[]
}

export interface SettingsUpdateResult {
  saved: string[]
  logout_required: boolean
}
