<template>
  <div v-loading="loading" class="run-report">
    <div class="page-head">
      <el-button link type="primary" :icon="ArrowLeft" @click="goBack">返回执行记录</el-button>
    </div>

    <el-result
      v-if="loadError"
      icon="error"
      title="报告加载失败"
      :sub-title="loadError"
    />

    <template v-else-if="data && run">
      <!-- 执行摘要 -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-header">
            <span>执行报告</span>
            <span class="header-meta">{{ run.run_id }}</span>
          </div>
        </template>

        <div class="summary">
          <div class="ring" :style="{ background: ringBackground }">
            <div class="ring-inner">
              <div class="ring-value">{{ passRate }}<span class="ring-unit">%</span></div>
              <div class="ring-label">用例通过率</div>
            </div>
          </div>

          <div class="summary-main">
            <div class="summary-tags">
              <el-tag :type="statusType[run.status]" effect="dark">{{ statusText[run.status] }}</el-tag>
              <div class="summary-title">
                <div>{{ summaryLine1 }}</div>
                <div>{{ summaryLine2 }}</div>
              </div>
            </div>
            <div class="summary-grid">
              <div class="stat">
                <span class="stat-label">开始时间</span>
                <span class="stat-value">{{ formatLocalTime(run.started_at) }}</span>
              </div>
              <div class="stat">
                <span class="stat-label">结束时间</span>
                <span class="stat-value">{{ formatLocalTime(run.finished_at) }}</span>
              </div>
              <div class="stat">
                <span class="stat-label">总耗时</span>
                <span class="stat-value">{{ formatDurationText(run.duration_ms) }}</span>
              </div>
              <div class="stat">
                <span class="stat-label">操作人</span>
                <span class="stat-value">{{ run.triggered_by || '—' }}</span>
              </div>
            </div>
          </div>
        </div>

        <el-alert
          v-if="run.error_message"
          class="error-alert"
          type="error"
          :closable="false"
          show-icon
          :title="run.error_message"
        />
      </el-card>

      <!-- IT 流程 / 合同审核 两个模块 -->
      <el-card v-for="group in caseGroups" :key="group.key" shadow="never" class="block">
        <template #header>
          <div class="card-header">
            <span>{{ group.title }}</span>
            <span class="header-meta">
              共 {{ group.stats.total }} 条用例 · 通过 {{ group.stats.passed }} · 失败 {{ group.stats.failed }}
            </span>
          </div>
        </template>

        <div class="section-title">用例执行明细</div>
        <el-empty
          v-if="group.modules.length === 0"
          :description="`本次执行未包含${group.title}用例`"
        />

        <div v-for="mod in group.modules" :key="mod.name" class="module-block">
          <div class="module-head">
            <span class="module-name">{{ mod.name }}</span>
            <span class="module-meta">
              共 {{ mod.total }} 条 · 通过 {{ mod.passed }}<template v-if="mod.failed"> · 失败 {{ mod.failed }}</template><template
                v-if="mod.skipped"
              > · 跳过 {{ mod.skipped }}</template>
            </span>
          </div>

          <div v-for="c in mod.cases" :key="c.case_id" class="case-row">
            <div class="case-line">
              <span class="case-dot" :class="'is-' + c.status"></span>
              <span class="case-name">{{ c.name }}</span>
              <span class="case-id">{{ c.case_id }}</span>
              <span class="case-duration">{{ formatDurationText(c.duration_ms) }}</span>
              <span class="case-status" :class="'is-' + c.status">
                {{ caseStatusText[c.status] || c.status }}
              </span>
            </div>

            <div v-if="c.message && c.status !== 'passed'" class="case-failure">
              <el-button link type="danger" size="small" @click="toggleFailure(c.case_id)">
                {{ expandedFailures.includes(c.case_id) ? '收起失败详情' : '查看失败详情' }}
              </el-button>

              <div v-if="expandedFailures.includes(c.case_id)" class="failure-body">
                <div class="failure-reason">{{ failureOf(c.message).reason }}</div>
                <ul v-if="failureOf(c.message).hints.length" class="failure-hints">
                  <li v-for="(hint, idx) in failureOf(c.message).hints" :key="idx">{{ hint }}</li>
                </ul>
                <pre v-if="failureOf(c.message).detail" class="case-message">{{ failureOf(c.message).detail }}</pre>
              </div>
            </div>
          </div>
        </div>

        <template v-if="group.withAccuracy">
          <div class="section-title accuracy-title">审核正确性</div>

          <template v-if="accuracy && contracts.length">
            <div class="acc-overview">
              <div class="acc-stat">
                <span class="acc-num">{{ accTotals.match }}</span>
                <span class="acc-label">一致规则</span>
              </div>
              <div class="acc-stat" :class="{ danger: accTotals.diff > 0 }">
                <span class="acc-num">{{ accTotals.diff }}</span>
                <span class="acc-label">存在差异</span>
              </div>
              <div class="acc-stat">
                <span class="acc-num">{{ accTotals.extra }}</span>
                <span class="acc-label">后端额外发现</span>
              </div>
              <div class="acc-stat" :class="{ danger: accTotals.aiError > 0 }">
                <span class="acc-num">{{ accTotals.aiError }}</span>
                <span class="acc-label">AI 仲裁失败</span>
              </div>
            </div>

            <el-collapse v-model="activeContracts">
              <el-collapse-item
                v-for="contract in contracts"
                :key="contract.contractName"
                :name="contract.contractName"
              >
                <template #title>
                  <div class="contract-title">
                    <span class="contract-name">{{ contract.contractName }}</span>
                    <el-tag v-if="countOf(contract, DIFF_STATUSES) === 0" type="success" size="small">
                      与标答完全一致
                    </el-tag>
                    <el-tag v-else type="danger" size="small">
                      {{ countOf(contract, DIFF_STATUSES) }} 处差异
                    </el-tag>
                  </div>
                </template>

                <div class="contract-meta">
                  <span>标准答案：{{ contract.standardFile }}</span>
                  <el-divider direction="vertical" />
                  <span>规则总数：{{ contract.totalRules }}</span>
                  <el-divider direction="vertical" />
                  <span>实际返回：{{ contract.actualRules }}</span>
                  <el-divider direction="vertical" />
                  <el-tag type="success" size="small">一致 {{ countOf(contract, ['match']) }}</el-tag>
                  <el-tag type="danger" size="small">差异 {{ countOf(contract, DIFF_STATUSES) }}</el-tag>
                  <el-tag v-if="countOf(contract, ['ai_error']) > 0" type="danger" effect="dark" size="small">
                    其中 AI 仲裁失败 {{ countOf(contract, ['ai_error']) }}
                  </el-tag>
                  <el-tag type="primary" size="small">额外发现 {{ countOf(contract, ['extra']) }}</el-tag>
                  <el-radio-group
                    :model-value="filterMode[contract.contractName] || 'diff'"
                    size="small"
                    class="filter-group"
                    @update:model-value="(v: string) => filterMode[contract.contractName] = v"
                  >
                    <el-radio-button value="diff">仅差异</el-radio-button>
                    <el-radio-button value="extra">额外发现</el-radio-button>
                    <el-radio-button value="match">一致项</el-radio-button>
                    <el-radio-button value="all">全部</el-radio-button>
                  </el-radio-group>
                </div>

                <el-table
                  v-if="getFilteredItems(contract).length > 0"
                  :data="getFilteredItems(contract)"
                  border
                  stripe
                  size="small"
                  row-key="ruleId"
                  :expand-row-keys="expandedRows[contract.contractName] || []"
                  @row-click="toggleRowExpand(contract.contractName, $event)"
                >
                  <el-table-column type="expand">
                    <template #default="{ row }">
                      <div
                        v-if="row.aiJudgment"
                        class="ai-banner"
                        :class="row.aiJudgment.error ? 'ai-error' : (row.aiJudgment.consistent ? 'ai-ok' : 'ai-bad')"
                      >
                        <template v-if="row.aiJudgment.error">
                          <strong>AI 语义仲裁失败：</strong>{{ row.aiJudgment.error }}
                          <span class="ai-model">（模型：{{ row.aiJudgment.model || '—' }}；按失败计）</span>
                        </template>
                        <template v-else>
                          <strong>AI 仲裁：{{ row.aiJudgment.consistent ? '一致' : '不一致' }}</strong>
                          <span v-if="row.aiJudgment.reason"> —— {{ row.aiJudgment.reason }}</span>
                          <span class="ai-model">（模型：{{ row.aiJudgment.model }}）</span>
                        </template>
                      </div>

                      <div class="compare-grid">
                        <div class="compare-col expected-col">
                          <div class="col-title">
                            <el-tag type="primary" size="small">标答（标准答案）</el-tag>
                          </div>
                          <template v-if="row.expected">
                            <div class="field-row">
                              <span class="field-label">合规判定：</span>
                              <el-tag v-if="row.expected.compliant === true" type="success" size="small">合规</el-tag>
                              <el-tag v-else-if="row.expected.compliant === false" type="danger" size="small">不合规</el-tag>
                              <el-tag v-else type="info" size="small">—</el-tag>
                            </div>
                            <div class="field-row">
                              <span class="field-label">建议：</span>
                              <span class="field-value">{{ row.expected.suggestion || '—' }}</span>
                            </div>
                            <div class="field-row">
                              <span class="field-label">原文：</span>
                              <span class="field-value">{{ row.expected.original || '—' }}</span>
                            </div>
                            <div class="field-row">
                              <span class="field-label">修改：</span>
                              <span class="field-value">{{ row.expected.revised || '—' }}</span>
                            </div>
                          </template>
                          <div v-else class="no-data">
                            <p>—（标答未覆盖该规则）</p>
                            <p class="extra-tip">后端在此条款上有额外发现。如确认属于标答漏标，请将该规则补充进标答 JSON 后重跑。</p>
                          </div>
                        </div>

                        <div class="compare-col actual-col">
                          <div class="col-title">
                            <el-tag type="warning" size="small">实际（后端返回）</el-tag>
                          </div>
                          <template v-if="row.actual">
                            <div class="field-row">
                              <span class="field-label">合规判定：</span>
                              <el-tag v-if="row.actual.compliant === true" type="success" size="small">合规</el-tag>
                              <el-tag v-else-if="row.actual.compliant === false" type="danger" size="small">不合规</el-tag>
                              <el-tag v-else type="info" size="small">缺失</el-tag>
                            </div>
                            <div class="field-row">
                              <span class="field-label">建议：</span>
                              <span class="field-value">{{ row.actual.suggestion || '—' }}</span>
                            </div>
                            <div class="field-row">
                              <span class="field-label">原文：</span>
                              <span class="field-value">{{ row.actual.original || '—' }}</span>
                            </div>
                            <div class="field-row">
                              <span class="field-label">修改：</span>
                              <span class="field-value">{{ row.actual.revised || '—' }}</span>
                            </div>
                          </template>
                          <div v-else class="no-data">
                            <p>—（实际审核未返回该规则）</p>
                          </div>
                        </div>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="ruleId" label="规则 ID" width="200" show-overflow-tooltip />
                  <el-table-column prop="ruleIntent" label="规则意图" min-width="180" show-overflow-tooltip />
                  <el-table-column label="比对结论" width="120">
                    <template #default="{ row }">
                      <el-tag :type="itemStatusTag[row.status]" size="small">{{ itemStatusText[row.status] }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="判定来源" width="150">
                    <template #default="{ row }">
                      <el-tag :type="resolveJudgeSource(row).type" size="small" effect="light">
                        {{ resolveJudgeSource(row).text }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="desc" label="说明" min-width="280" show-overflow-tooltip />
                </el-table>
                <el-empty v-else :description="emptyText(contract)" />
              </el-collapse-item>
            </el-collapse>
          </template>

          <el-empty v-else description="本次执行未产生审核正确性数据" />
        </template>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'

import { getRunReport } from '../api'
import { formatDurationText, formatLocalTime } from '../utils/datetime'
import type {
  AccuracyCompareItem,
  AccuracyContract,
  ReportModule,
  RunReportData,
  RunStatus,
} from '../types'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const loadError = ref('')
const data = ref<RunReportData | null>(null)

const run = computed(() => data.value?.run)
const modules = computed<ReportModule[]>(() => data.value?.modules ?? [])
const accuracy = computed(() => data.value?.accuracy ?? null)
const contracts = computed<AccuracyContract[]>(() => accuracy.value?.accuracy_data ?? [])

const flowModules = computed(() => modules.value.filter((m) => m.kind !== 'accuracy'))
const contractModules = computed(() => modules.value.filter((m) => m.kind === 'accuracy'))

function sumModules(list: ReportModule[]) {
  return list.reduce(
    (acc, m) => ({
      total: acc.total + m.total,
      passed: acc.passed + m.passed,
      failed: acc.failed + m.failed,
    }),
    { total: 0, passed: 0, failed: 0 },
  )
}

/** 报告按 IT 流程 / 合同审核两个模块展示 */
const caseGroups = computed(() => [
  {
    key: 'flow',
    title: 'IT流程功能测试',
    modules: flowModules.value,
    stats: sumModules(flowModules.value),
    withAccuracy: false,
  },
  {
    key: 'contract',
    title: '合同审核功能测试',
    modules: contractModules.value,
    stats: sumModules(contractModules.value),
    withAccuracy: true,
  },
])

const statusText: Record<RunStatus, string> = {
  running: '执行中',
  passed: '通过',
  failed: '失败',
  error: '异常',
}
const statusType: Record<RunStatus, 'info' | 'success' | 'danger' | 'warning'> = {
  running: 'info',
  passed: 'success',
  failed: 'danger',
  error: 'warning',
}

const caseStatusText: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  error: '异常',
  skipped: '跳过',
}

/** 计入差异的比对结论（extra 为后端额外发现，单独统计） */
const DIFF_STATUSES = ['compliant', 'suggestion', 'both', 'missing', 'ai_error'] as const

const itemStatusText: Record<string, string> = {
  match: '一致',
  compliant: '合规判定差异',
  suggestion: '建议语义差异',
  both: '双重差异',
  missing: '实际漏审',
  extra: '后端额外发现',
  ai_error: 'AI 判定失败',
}
const itemStatusTag: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'primary'> = {
  match: 'success',
  compliant: 'danger',
  suggestion: 'warning',
  both: 'danger',
  missing: 'info',
  extra: 'primary',
  ai_error: 'danger',
}

/** 兼容旧结构：没有 items 时把 diffs 映射为比对项 */
function getItems(contract: AccuracyContract): AccuracyCompareItem[] {
  if (contract.items && contract.items.length > 0) return contract.items
  return (contract.diffs || []).map((d) => ({
    ruleId: d.ruleId,
    ruleIntent: d.ruleIntent,
    status: d.diffType as AccuracyCompareItem['status'],
    desc: d.desc,
    compliantDiff: d.compliantDiff,
    suggestionDiff: d.suggestionDiff,
    expected: d.expected,
    actual: d.actual,
  }))
}

function countOf(contract: AccuracyContract, statuses: readonly string[]): number {
  return getItems(contract).filter((i) => statuses.includes(i.status)).length
}

/** 每个合同独立的筛选模式：diff（默认）/ extra / match / all */
const filterMode = ref<Record<string, string>>({})

function getFilteredItems(contract: AccuracyContract): AccuracyCompareItem[] {
  const items = getItems(contract)
  const mode = filterMode.value[contract.contractName] || 'diff'
  if (mode === 'all') return items
  if (mode === 'match') return items.filter((i) => i.status === 'match')
  if (mode === 'extra') return items.filter((i) => i.status === 'extra')
  return items.filter((i) => (DIFF_STATUSES as readonly string[]).includes(i.status))
}

function emptyText(contract: AccuracyContract): string {
  const mode = filterMode.value[contract.contractName] || 'diff'
  if (mode === 'extra') return '无后端额外发现'
  if (mode === 'match') return '无一致项'
  if (mode === 'all') return '暂无比对数据'
  return '该合同审核结果与标准答案完全一致'
}

/** 判定来源：AI 仲裁 / 语义对比（带重叠率）/ 直接判定 */
function resolveJudgeSource(row: AccuracyCompareItem):
  { text: string; type: 'success' | 'warning' | 'info' | 'danger' } {
  if (row.aiJudgment?.error) return { text: 'AI 仲裁失败', type: 'danger' }
  if (row.aiJudgment || row.suggestionDiff?.judge === 'ai') return { text: 'AI 仲裁', type: 'success' }
  if (row.suggestionDiff) {
    const rate = row.suggestionDiff.overlap_rate
    const pct = typeof rate === 'number' ? `（${(rate * 100).toFixed(1)}%）` : ''
    return { text: `语义对比${pct}`, type: 'warning' }
  }
  return { text: '直接判定', type: 'info' }
}

const activeContracts = ref<string[]>([])
const expandedRows = ref<Record<string, string[]>>({})

/** 点击行展开标答与实际对照 */
function toggleRowExpand(contractName: string, row: AccuracyCompareItem) {
  const key = row.ruleId
  const current = expandedRows.value[contractName] || []
  expandedRows.value[contractName] = current.includes(key)
    ? current.filter((k) => k !== key)
    : [...current, key]
}

/** 全部合同的汇总计数 */
const accTotals = computed(() => {
  let match = 0
  let diff = 0
  let extra = 0
  let aiError = 0
  for (const contract of contracts.value) {
    for (const item of getItems(contract)) {
      if (item.status === 'match') match += 1
      else if (item.status === 'extra') extra += 1
      else if ((DIFF_STATUSES as readonly string[]).includes(item.status)) diff += 1
      if (item.status === 'ai_error') aiError += 1
    }
  }
  return { match, diff, extra, aiError }
})

/** 通过率：以实际执行条数为分母，未执行时退回计划总数 */
const passRate = computed(() => {
  const total = run.value?.case_executed || run.value?.case_total || 0
  if (!total) return 0
  return Math.round(((run.value?.case_passed || 0) / total) * 100)
})

/** 通过部分绿色、未通过部分红色 */
const ringBackground = computed(() => {
  if (!run.value?.case_executed && !run.value?.case_total) return '#ebeef5'
  const rate = passRate.value
  return `conic-gradient(#67c23a 0 ${rate}%, #f56c6c ${rate}% 100%)`
})

/** IT 功能用例汇总说明 */
const summaryLine1 = computed(() => {
  const it = caseGroups.value[0].stats
  return `IT功能测试 ${it.total} 条，流程执行成功 ${it.passed} 条，失败 ${it.failed} 条`
})

/** 审核正确性用例总数与通过数（用例列表里选中的审核详情用例） */
const accuracyCaseTotal = computed(() =>
  contractModules.value.reduce((sum, m) => sum + m.total, 0),
)
const accuracyCasePassed = computed(() =>
  contractModules.value.reduce((sum, m) => sum + m.passed, 0),
)

/**
 * 合同审核按用例维度统计：
 * - 功能：用例流程是否正常走通（合同是否产出了审核结果）
 * - 正确性：该用例的审核结果是否全部一致
 */
const contractSummary = computed(() => {
  const total = accuracyCaseTotal.value
  const covered = contracts.value.length
  const flowPassed = Math.min(total, Math.max(covered, accuracyCasePassed.value))

  let correctFailed = 0
  for (const contract of contracts.value) {
    if (countOf(contract, DIFF_STATUSES) > 0) correctFailed += 1
  }
  const judged = Math.min(total, covered)

  return {
    total,
    flowPassed,
    flowFailed: Math.max(0, total - flowPassed),
    correctPassed: Math.max(0, judged - correctFailed),
    correctFailed,
  }
})

/** 合同审核用例与审核正确性汇总说明 */
const summaryLine2 = computed(() => {
  const c = contractSummary.value
  return `合同审核功能测试 ${c.total} 条，流程执行成功 ${c.flowPassed} 条，失败 ${c.flowFailed} 条，结果与标答一致 ${c.correctPassed} 条，不一致 ${c.correctFailed} 条`
})

/** 失败详情默认折叠，按用例记录展开状态 */
const expandedFailures = ref<string[]>([])

function toggleFailure(caseId: string) {
  expandedFailures.value = expandedFailures.value.includes(caseId)
    ? expandedFailures.value.filter((id) => id !== caseId)
    : [...expandedFailures.value, caseId]
}

interface FailureInfo {
  reason: string
  hints: string[]
  detail: string
}

const failureCache = new Map<string, FailureInfo>()

/** 把 pytest 失败信息转换为可读的原因、排查建议与技术明细 */
function failureOf(message: string): FailureInfo {
  const cached = failureCache.get(message)
  if (cached) return cached
  const info = analyzeFailure(message)
  failureCache.set(message, info)
  return info
}

function analyzeFailure(message: string): FailureInfo {
  const lines = message.split(/\r?\n/).map((line) => line.trimEnd()).filter(Boolean)
  const firstLine = (lines[0] || '').replace(/^(Failed:\s*|AssertionError:\s*|E\s+)/i, '').trim()
  const hints: string[] = []

  if (/超时|timeout|timed out/i.test(message)) {
    const seconds = message.match(/(\d+)\s*s\s*内未完成/)
    hints.push(seconds ? `等待超时：${seconds[1]} 秒内未返回结果` : '请求或任务等待超时')
    hints.push('可能是被测服务处理较慢，或「单接口超时」「整次执行超时」配置偏小')
  }
  if (/status=PROCESSING/i.test(message)) {
    hints.push('审核任务长时间停留在「处理中」，未给出最终结论')
  }
  const httpMatch = message.match(/(?:HTTP|status(?:_code)?[=:]\s*)\s*(\d{3})/i)
  if (httpMatch) {
    const code = Number(httpMatch[1])
    if (code >= 500) hints.push(`被测服务返回 ${code}，属于服务端异常，请查看服务日志`)
    else if (code >= 400) hints.push(`被测服务返回 ${code}，请检查鉴权信息或请求参数`)
  }
  if (/存在差异/.test(message)) {
    hints.push('请查看本页「审核正确性」中的合同明细，逐条核对差异规则')
  }
  if (/assert/i.test(message)) hints.push('断言未通过：实际结果与预期结果不一致')
  if (/connection|connect/i.test(message) && /error|refused|reset|timeout/i.test(message)) {
    hints.push('无法连接被测服务，请检查被测环境地址与网络')
  }
  if (/\b404\b|not found/i.test(message)) hints.push('接口或资源不存在，请确认被测环境版本')

  // 首行已作为「失败原因」展示，技术明细只保留其余内容，避免重复
  const detail = lines
    .slice(1)
    .filter((line) => !/^File "|^self = |^>\s|^={3,}|^-{3,}/.test(line))
    .map((line) => line.replace(/^\s*E\s+/, ''))
    .join('\n')
    .trim()

  return {
    reason: firstLine || '用例执行失败',
    hints: [...new Set(hints)],
    detail,
  }
}

function goBack() {
  router.push({ name: 'runs' })
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await getRunReport(String(route.params.runId || ''))
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.run-report {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  margin-bottom: -4px;
}

.block {
  margin-bottom: 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}

.header-meta {
  font-size: 13px;
  font-weight: 400;
  color: #909399;
}

.error-alert {
  margin-top: 16px;
}

.section-title {
  margin: 4px 0 12px;
  padding-left: 8px;
  border-left: 3px solid var(--el-color-primary);
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.accuracy-title {
  margin-top: 24px;
}

/* —— 摘要 —— */
.summary {
  display: flex;
  align-items: center;
  gap: 32px;
}

.ring {
  flex-shrink: 0;
  width: 132px;
  height: 132px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ring-inner {
  width: 104px;
  height: 104px;
  border-radius: 50%;
  background: #ffffff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.ring-value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
  line-height: 1.1;
}

.ring-unit {
  font-size: 15px;
  font-weight: 600;
  margin-left: 2px;
}

.ring-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.summary-main {
  flex: 1;
  min-width: 0;
}

.summary-tags {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-title {
  font-size: 14px;
  line-height: 1.9;
  color: #303133;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px 24px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-label {
  font-size: 12px;
  color: #909399;
}

.stat-value {
  font-size: 14px;
  color: #303133;
}

/* —— 审核正确性 —— */
.acc-overview {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.acc-stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  background: #f8f9fb;
  border-radius: 6px;
}

.acc-stat.danger {
  background: #fef0f0;
}

.acc-num {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
}

.acc-stat.danger .acc-num {
  color: #f56c6c;
}

.acc-label {
  font-size: 12px;
  color: #909399;
}

.contract-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.contract-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.contract-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f8f9fb;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
}

.filter-group {
  margin-left: auto;
}

/* —— 标答 vs 实际 两列对比 —— */
.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 12px;
}

.compare-col {
  padding: 12px 16px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}

.expected-col {
  border-left: 3px solid #409eff;
}

.actual-col {
  border-left: 3px solid #e6a23c;
}

.col-title {
  margin-bottom: 10px;
}

.field-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-bottom: 8px;
  line-height: 1.6;
}

.field-label {
  flex-shrink: 0;
  font-size: 13px;
  color: #909399;
}

.field-value {
  flex: 1;
  font-size: 13px;
  color: #303133;
  word-break: break-all;
  white-space: pre-wrap;
}

.no-data {
  font-size: 13px;
  line-height: 1.8;
  color: #909399;
}

.extra-tip {
  margin-top: 8px;
  padding: 8px 10px;
  background: #ecf5ff;
  border-left: 3px solid #409eff;
  border-radius: 4px;
  color: #409eff;
  font-size: 12px;
}

/* —— AI 语义仲裁横幅 —— */
.ai-banner {
  margin: 12px 12px 0;
  padding: 8px 12px;
  border-radius: 4px;
  border-left: 3px solid #909399;
  background: #f4f4f5;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
}

.ai-banner.ai-ok {
  border-left-color: #67c23a;
  background: #f0f9eb;
}

.ai-banner.ai-bad {
  border-left-color: #e6a23c;
  background: #fdf6ec;
}

.ai-banner.ai-error {
  border-left-color: #f56c6c;
  background: #fef0f0;
  color: #f56c6c;
}

.ai-model {
  font-size: 12px;
  color: #909399;
}

/* —— 用例执行明细 —— */
.module-block + .module-block {
  margin-top: 18px;
}

.module-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 8px;
  margin-bottom: 6px;
  border-bottom: 1px solid #ebeef5;
}

.module-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.module-meta {
  font-size: 12px;
  color: #909399;
}

.case-row {
  padding: 8px 4px;
}

.case-row + .case-row {
  border-top: 1px dashed #f2f3f5;
}

.case-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.case-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #909399;
}

.case-dot.is-passed {
  background: #67c23a;
}

.case-dot.is-failed,
.case-dot.is-error {
  background: #f56c6c;
}

.case-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-id {
  flex-shrink: 0;
  font-size: 12px;
  color: #c0c4cc;
}

.case-duration {
  flex-shrink: 0;
  font-size: 12px;
  color: #909399;
}

.case-status {
  flex-shrink: 0;
  font-size: 12px;
  color: #909399;
}

.case-status.is-passed {
  color: #529b2e;
}

.case-status.is-failed,
.case-status.is-error {
  color: #f56c6c;
}

.case-failure {
  margin: 4px 0 0 18px;
}

.failure-body {
  margin-top: 6px;
  padding: 12px 14px;
  background: #fef7f7;
  border: 1px solid #fde2e2;
  border-radius: 4px;
}

.failure-reason {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.7;
  color: #c45656;
}

.failure-hints {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
  color: #909399;
}

.case-message {
  margin: 10px 0 0;
  padding: 10px 12px;
  background: #ffffff;
  border: 1px solid #f2f3f5;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.7;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: Consolas, Monaco, monospace;
}
</style>
