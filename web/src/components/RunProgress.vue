<template>
  <el-card shadow="never" class="run-progress">
    <template #header>
      <div class="rp-header">
        <span>执行进度</span>
        <el-tag :type="statusType[run.status]" size="small">{{ statusText[run.status] }}</el-tag>
      </div>
    </template>

    <el-progress :percentage="percentage" :status="progressStatus" />
    <div class="rp-line">
      <template v-if="running">
        正在执行 {{ run.case_executed }}/{{ run.case_total }}
        <span v-if="currentCaseName" class="muted">：{{ currentCaseName }}</span>
      </template>
      <template v-else>
        已执行 {{ run.case_executed }}/{{ run.case_total }}
      </template>
    </div>

    <!-- 子步骤实时链（仅在 running 且有数据时显示） -->
    <div v-if="running && runningCaseSteps.length > 0" class="rp-sub-steps">
      <div class="sub-title">当前用例详细进度</div>
      <div class="step-chain">
        <div
          v-for="(step, idx) in runningCaseSteps"
          :key="idx"
          class="step-item"
          :class="stepClass(idx)"
        >
          <span class="step-icon">{{ stepIcon(idx) }}</span>
          <span class="step-name">{{ stepLabel(step) }}</span>
          <span v-if="stepExtra(idx)" class="step-extra">{{ stepExtra(idx) }}</span>
        </div>
      </div>
    </div>

    <!-- 完成态：统计 + 报告 + 失败列表 -->
    <template v-if="!running">
      <div class="rp-stats">
        <span class="pass">通过 {{ run.case_passed }}</span>
        <span class="fail">失败 {{ run.case_failed }}</span>
        <span v-if="run.duration_ms !== null" class="muted">耗时 {{ (run.duration_ms / 1000).toFixed(1) }} 秒</span>
        <span v-if="run.triggered_by" class="muted">操作人 {{ run.triggered_by }}</span>
      </div>

      <el-alert
        v-if="run.status === 'error' && run.error_message"
        class="rp-block"
        type="error"
        :closable="false"
        show-icon
        :title="run.error_message"
      />

      <el-table
        v-if="run.failed_cases.length > 0"
        class="rp-block"
        :data="run.failed_cases"
        size="small"
        border
      >
        <el-table-column prop="name" label="用例名" min-width="180" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column prop="status" label="状态" width="90" />
        <el-table-column prop="message" label="失败摘要" min-width="240" show-overflow-tooltip />
      </el-table>

      <div class="rp-report">
        <el-button
          v-if="run.report_ready && run.report_url"
          type="primary"
          link
          @click="openReport"
        >
          查看报告
        </el-button>
        <span v-else class="muted">报告不存在或已按保留策略清理</span>
      </div>

      <div class="rp-actions">
        <el-button size="small" @click="emit('retry')">再次执行</el-button>
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { RunDetail, RunStatus, SubStepEvent } from '../types'

const props = defineProps<{
  run: RunDetail
  subSteps: SubStepEvent[]
  latestSubStep: SubStepEvent | null
}>()

const emit = defineEmits<{
  (e: 'retry'): void
}>()

const statusText: Record<RunStatus, string> = {
  running: '执行中',
  passed: '全部通过',
  failed: '存在失败',
  error: '执行异常',
}

const statusType: Record<RunStatus, 'primary' | 'success' | 'danger' | 'warning' | 'info'> = {
  running: 'primary',
  passed: 'success',
  failed: 'danger',
  error: 'warning',
}

const running = computed(() => props.run.status === 'running')

const percentage = computed(() => {
  const total = props.run.case_total || 0
  if (total === 0) return 0
  return Math.min(100, Math.round((props.run.case_executed / total) * 100))
})

const progressStatus = computed<'success' | 'exception' | undefined>(() => {
  if (props.run.status === 'passed') return 'success'
  if (props.run.status === 'failed' || props.run.status === 'error') return 'exception'
  return undefined
})

/** 当前正在跑的用例名（从 subSteps 里找最后一个 case_start） */
const currentCaseName = computed(() => {
  const events = props.subSteps
  let name = props.run.current_case || ''
  // 找最后一个有 running 状态的事件的 case_id
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].step === 'case_start' || events[i].status === 'running') {
      name = events[i].case_id
      break
    }
  }
  return name.replace(/^test_/, '').replace(/_/g, ' ')
})

/**
 * step 中文 label 映射（覆盖所有测试文件里用的 step key）
 * 未覆盖的 step key 直接显示 key 名
 */
const STEP_LABEL_MAP: Record<string, string> = {
  // test_review_accuracy 审核正确性
  upload: '上传合同',
  extract: '预解析',
  commit: '提交审核',
  poll: '等待审核结果',
  poll_start: '等待审核结果',
  report: '拉取报告',

  // test_core_flows 登录认证
  iam_entry: 'IAM 授权入口',
  auth_ok: '鉴权验证',
  logout: '登出',

  // test_core_flows 上传链路
  save: '保存解析结果',
  list_check: '文件列表查询',

  // test_core_flows 审核链路（和 review_accuracy 共用 upload/extract/commit/poll/report）
  feedback: '报告反馈',

  // test_core_flows 合同对比
  compare: '发起智能对比',

  // test_core_flows 外部平台对接
  platform_review: '平台发起审核',
  platform_task_status: '查询任务状态',
  platform_review_detail: '获取审核详情',
}

const DEFAULT_STEP_ORDER = ['upload', 'extract', 'commit', 'poll', 'report']


/** 当前用例的 step 顺序：从 subSteps 里按首次出现顺序收集，去重后返回 */
const runningCaseSteps = computed(() => {
  const events = props.subSteps
  if (!events.length) return []

  let currentCaseId = ''
  let startIdx = -1
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].step === 'case_start') {
      currentCaseId = events[i].case_id
      startIdx = i
      break
    }
  }
  if (startIdx === -1) return []

  const caseEvents = events.slice(startIdx)
  // 收集本 case 的所有 step（去重保序）
  const seen = new Set<string>()
  const stepKeys: string[] = []
  for (const e of caseEvents) {
    const key = e.step
    if (key === 'case_start' || key === 'case_end') continue
    // poll 系列统一合并到 poll
    const normalized = key.startsWith('poll') ? 'poll' : key
    if (!seen.has(normalized)) {
      seen.add(normalized)
      stepKeys.push(normalized)
    }
  }

  // 用 events 推导每步状态：取最后一次该 step 的事件
  const steps: Array<{ key: string; status: 'pending' | 'running' | 'done' | 'fail'; detail?: string }> = []
  for (const key of stepKeys) {
    const matched = caseEvents.filter((e) => {
      const ekey = e.step.startsWith('poll') ? 'poll' : e.step
      return ekey === key
    })
    if (matched.length === 0) {
      steps.push({ key, status: 'pending' })
      continue
    }
    const last = matched[matched.length - 1]
    if (last.status === 'done') {
      steps.push({ key, status: 'done', detail: last.detail || undefined })
    } else if (last.status === 'fail') {
      steps.push({ key, status: 'fail', detail: last.detail || undefined })
    } else {
      steps.push({ key, status: 'running', detail: last.detail || undefined })
    }
  }
  return steps
})

function stepIcon(idx: number): string {
  const s = runningCaseSteps.value[idx]?.status
  if (s === 'done') return '✅'
  if (s === 'running') return '🔄'
  if (s === 'fail') return '❌'
  return '⏳'
}

function stepClass(idx: number): string {
  const s = runningCaseSteps.value[idx]?.status
  return `status-${s}`
}

function stepLabel(step: { key: string }): string {
  return STEP_LABEL_MAP[step.key] || step.key
}

function stepExtra(idx: number): string {
  const step = runningCaseSteps.value[idx]
  if (!step?.detail) return ''
  // detail 格式示例：idx=18,total=72,auditStatus=RUNNING
  const parts = step.detail.split(',')
  const obj: Record<string, string> = {}
  for (const p of parts) {
    const [k, v] = p.split('=')
    if (k) obj[k] = v
  }
  if (step.key === 'poll' && step.status === 'running') {
    const idx = parseInt(obj.idx || '0')
    const total = parseInt(obj.total || '1')
    const remaining = (total - idx) * 5
    if (remaining > 0) return `第 ${idx}/${total} 次轮询，剩余 ~${remaining}s`
    return `第 ${idx}/${total} 次轮询`
  }
  if (step.key === 'poll' && step.status === 'done') {
    return `完成 ${obj.idx || '?'} 次轮询`
  }
  if (step.key === 'report' && obj.issues) {
    return `发现 ${obj.issues} 条`
  }
  return ''
}

/** 新标签页打开 Allure 报告 */
function openReport() {
  if (props.run.report_url) {
    window.open(props.run.report_url, '_blank')
  }
}
</script>

<style scoped>
.run-progress {
  margin-bottom: 80px;
}

.rp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rp-line {
  margin-top: 12px;
  color: #606266;
  font-size: 14px;
}

.rp-sub-steps {
  margin-top: 16px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 6px;
  border: 1px solid #ebeef5;
}

.sub-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 8px;
  font-weight: 500;
}

.step-chain {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #606266;
  transition: all 0.2s;
}

.step-item.status-done {
  color: #67c23a;
}

.step-item.status-running {
  color: #409eff;
  font-weight: 500;
}

.step-item.status-fail {
  color: #f56c6c;
}

.step-item.status-pending {
  color: #c0c4cc;
}

.step-icon {
  width: 18px;
  text-align: center;
}

.step-extra {
  color: #909399;
  font-size: 13px;
  margin-left: auto;
}

.status-running .step-icon {
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.rp-stats {
  display: flex;
  gap: 20px;
  margin: 16px 0 4px;
  font-size: 14px;
}

.rp-stats .pass {
  color: #67c23a;
}

.rp-stats .fail {
  color: #f56c6c;
}

.rp-block {
  margin-top: 12px;
}

.rp-report {
  margin-top: 12px;
}

.muted {
  color: #909399;
  font-size: 13px;
}

.rp-actions {
  margin-top: 12px;
}
</style>
