<template>
  <div class="accuracy-review">
    <!-- 上半：执行记录列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>审核正确性记录</span>
          <el-button type="primary" :icon="Refresh" circle @click="loadRuns" />
        </div>
      </template>

      <el-table v-loading="loading" :data="runs" border stripe highlight-current-row
                @row-click="selectRun" :current-row-key="selectedRunId">
        <el-table-column label="开始时间" min-width="170">
          <template #default="{ row }">{{ formatLocalTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType[row.status]" size="small">{{ statusText[row.status] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="triggered_by" label="操作人" width="120" />
        <el-table-column label="合同总数" width="90" align="center">
          <template #default="{ row }">{{ row.total_contracts }}</template>
        </el-table-column>
        <el-table-column label="差异合同" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.diff_contracts > 0" type="danger" size="small">
              {{ row.diff_contracts }}
            </el-tag>
            <span v-else class="pass">全部通过</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100" align="center">
          <template #default="{ row }">{{ formatDuration(row.duration_ms) }}</template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && runs.length === 0" class="empty">
        <el-empty description="暂无审核正确性记录 — 勾选 ACC-001/002/003 用例执行一次即可生成" />
      </div>
    </el-card>

    <!-- 下半：选中 run 的 diff 详情 -->
    <el-card v-if="selectedDetail" shadow="never" class="detail-card">
      <template #header>
        <div class="card-header">
          <span>
            执行详情：{{ selectedDetail.run_id }}
            <el-tag :type="statusType[selectedDetail.status]" size="small" style="margin-left: 8px">
              {{ statusText[selectedDetail.status] }}
            </el-tag>
          </span>
        </div>
      </template>

      <el-collapse v-model="activeContracts">
        <el-collapse-item
          v-for="contract in selectedDetail.accuracy_data"
          :key="contract.contractName"
          :title="contract.contractName"
          :name="contract.contractName"
        >
          <!-- 统计徽标 + 筛选器 -->
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
              size="small" class="filter-group"
              @update:model-value="(v: string) => filterMode[contract.contractName] = v"
            >
              <el-radio-button value="diff">仅差异</el-radio-button>
              <el-radio-button value="extra">额外发现</el-radio-button>
              <el-radio-button value="match">一致项</el-radio-button>
              <el-radio-button value="all">全部</el-radio-button>
            </el-radio-group>
          </div>

          <el-table v-if="getFilteredItems(contract).length > 0" :data="getFilteredItems(contract)" border stripe size="small"
                    row-key="ruleId"
                    :expand-row-keys="expandedRows[contract.contractName] || []"
                    @row-click="toggleRowExpand(contract.contractName, $event)">
            <el-table-column type="expand">
              <template #default="{ row }">
                <!-- AI 语义仲裁结论横幅 -->
                <div v-if="row.aiJudgment" class="ai-banner"
                     :class="row.aiJudgment.error ? 'ai-error' : (row.aiJudgment.consistent ? 'ai-ok' : 'ai-bad')">
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
                  <!-- 左列：标答 -->
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

                  <!-- 右列：实际 -->
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
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import { listAccuracyRuns, getAccuracyDetail } from '../api'
import { formatLocalTime } from '../utils/datetime'
import type {
  AccuracyCompareItem, AccuracyContract, AccuracyItemStatus,
  AccuracyRunDetail, AccuracyRunSummary,
} from '../types'

const loading = ref(false)
const runs = ref<AccuracyRunSummary[]>([])
const selectedRunId = ref<string | null>(null)
const selectedDetail = ref<AccuracyRunDetail | null>(null)
const activeContracts = ref<string[]>([])
const expandedRows = ref<Record<string, string[]>>({})
/** 每个合同独立的筛选模式：diff（默认）/ extra / match / all */
const filterMode = ref<Record<string, string>>({})

/** 计入"差异"的 status 集合（extra 不计入；ai_error 属于判定失败，同样计入） */
const DIFF_STATUSES = ['compliant', 'suggestion', 'both', 'missing', 'ai_error'] as const

const statusText: Record<string, string> = {
  running: '执行中',
  passed: '通过',
  failed: '失败',
  error: '异常',
}
const statusType: Record<string, 'info' | 'success' | 'danger' | 'warning'> = {
  running: 'info',
  passed: 'success',
  failed: 'danger',
  error: 'warning',
}

/** 比对结论列：七种状态的文案和颜色 */
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

/** 判定来源：AI 仲裁 / 语义对比（带重叠率）/ 直接判定 —— 每行都有值 */
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

/** 统一数据源：新结构用 items；旧结构把 diffs 映射为伪 items（status 取 diffType） */
function getItems(contract: AccuracyContract): AccuracyCompareItem[] {
  if (contract.items && contract.items.length > 0) return contract.items
  return (contract.diffs || []).map(d => ({
    ruleId: d.ruleId,
    ruleIntent: d.ruleIntent,
    status: d.diffType as AccuracyItemStatus,
    desc: d.desc,
    compliantDiff: d.compliantDiff,
    suggestionDiff: d.suggestionDiff,
    expected: d.expected,
    actual: d.actual,
  }))
}

function countOf(contract: AccuracyContract, statuses: readonly string[]): number {
  return getItems(contract).filter(i => statuses.includes(i.status)).length
}

/** 按当前筛选模式过滤该合同的比对记录 */
function getFilteredItems(contract: AccuracyContract): AccuracyCompareItem[] {
  const items = getItems(contract)
  const mode = filterMode.value[contract.contractName] || 'diff'
  if (mode === 'all') return items
  if (mode === 'match') return items.filter(i => i.status === 'match')
  if (mode === 'extra') return items.filter(i => i.status === 'extra')
  return items.filter(i => (DIFF_STATUSES as readonly string[]).includes(i.status))
}

function emptyText(contract: AccuracyContract): string {
  const mode = filterMode.value[contract.contractName] || 'diff'
  if (mode === 'extra') return '无后端额外发现'
  if (mode === 'match') return '无一致项'
  if (mode === 'all') return '暂无比对数据'
  return '该合同审核结果与标准答案完全一致 🎉'
}

function formatDuration(ms: number | null): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60), sec = s % 60
  return `${m}m ${sec}s`
}

/** 点击差异行切换展开状态，显示标答 vs 实际两列对比 */
function toggleRowExpand(contractName: string, row: any) {
  const key = row.ruleId
  const current = expandedRows.value[contractName] || []
  if (current.includes(key)) {
    expandedRows.value[contractName] = current.filter(k => k !== key)
  } else {
    expandedRows.value[contractName] = [...current, key]
  }
}

async function loadRuns() {
  loading.value = true
  try {
    const res = await listAccuracyRuns(20)
    runs.value = res.items
  } catch (e: any) {
    ElMessage.error(e?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function selectRun(row: AccuracyRunSummary) {
  selectedRunId.value = row.run_id
  try {
    selectedDetail.value = await getAccuracyDetail(row.run_id)
    // 默认展开全部合同面板（方便对比）
    activeContracts.value = selectedDetail.value.accuracy_data.map(c => c.contractName)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载详情失败')
  }
}

onMounted(loadRuns)
</script>

<style scoped>
.accuracy-review {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.detail-card {
  min-height: 300px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}
.empty {
  padding: 20px 0;
}
.contract-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  color: #606266;
  font-size: 13px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f8f9fb;
  border-radius: 4px;
}
.filter-group {
  margin-left: auto;
}
.pass {
  color: #67c23a;
  font-weight: 500;
}
.muted {
  color: #909399;
}
.overlap-low {
  color: #f56c6c;
  font-weight: 500;
}

/* —— 标答 vs 实际 两列对比 —— */
.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 12px;
}
.compare-col {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px 16px;
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
  font-weight: 600;
}
.field-row {
  display: flex;
  align-items: flex-start;
  margin-bottom: 8px;
  line-height: 1.6;
}
.field-label {
  flex-shrink: 0;
  width: 72px;
  color: #909399;
  font-size: 13px;
}
.field-value {
  flex: 1;
  color: #303133;
  font-size: 13px;
  word-break: break-all;
}

/* —— AI 语义仲裁横幅 —— */
.ai-banner {
  margin: 12px 12px 0;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
  border-left: 3px solid #909399;
  background: #f4f4f5;
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
  color: #909399;
  font-size: 12px;
}

/* extra / missing 时一侧无数据的占位样式 */
.no-data {
  color: #909399;
  font-size: 13px;
  line-height: 1.8;
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
</style>
