<template>
  <div class="case-list">
    <el-alert
      v-if="data && data.collect_error"
      class="block"
      type="warning"
      :closable="false"
      show-icon
      title="用例清单收集失败，请联系乙方处理"
      :description="data.collect_error"
    />

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>用例清单</span>
          <div class="header-right">
            <span class="label">操作人</span>
            <el-input
              v-model="operator"
              class="operator-input"
              placeholder="留空默认当前登录用户"
              clearable
            />
          </div>
        </div>
      </template>
      <div class="meta">
        <span>共 {{ total }} 条用例</span>
        <span v-if="data" class="muted">清单收集时间：{{ formatLocalTime(data.collected_at) }}</span>
      </div>
    </el-card>

    <div v-loading="loading" class="groups">
      <el-empty
        v-if="!loading && modules.length === 0"
        description="暂无用例，请联系乙方维护用例代码"
      />
      <CaseGroup
        v-if="flowModules.length"
        title="IT流程功能测试"
        :modules="flowModules"
        v-model="selectedIds"
      />
      <CaseGroup
        v-if="contractModules.length"
        title="合同审核功能测试"
        :modules="contractModules"
        :show-module="false"
        v-model="selectedIds"
      />
    </div>

    <RunProgress
      v-if="run"
      :run="run"
      :sub-steps="subSteps"
      :latest-sub-step="latestSubStep"
      @retry="onExecute"
    />

    <!-- 底部固定操作条 -->
    <div class="action-bar">
      <span class="selected-count">已选 {{ selectedIds.length }} / {{ total }}</span>
      <el-button
        type="primary"
        :disabled="selectedIds.length === 0 || running"
        :loading="submitting"
        @click="onExecute"
      >
        {{ running ? '执行中…' : '执行' }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import CaseGroup from '../components/CaseGroup.vue'
import RunProgress from '../components/RunProgress.vue'
import { ApiError, createRun, getCases } from '../api'
import { useRun } from '../composables/useRun'
import { formatLocalTime } from '../utils/datetime'
import type { CasesResponse, SubStepEvent } from '../types'

const router = useRouter()
const { run, polling, error, start, subSteps, latestSubStep } = useRun()

const data = ref<CasesResponse | null>(null)
const loading = ref(false)
const submitting = ref(false)
const selectedIds = ref<string[]>([])

// 操作人：localStorage 持久化，键固定为 operator
const operator = ref(localStorage.getItem('operator') || '')
watch(operator, (value) => {
  localStorage.setItem('operator', value)
})

const modules = computed(() => data.value?.modules ?? [])
const flowModules = computed(() => modules.value.filter((m) => m.category !== 'contract'))
const contractModules = computed(() => modules.value.filter((m) => m.category === 'contract'))
const total = computed(() => data.value?.total ?? 0)
const running = computed(() => polling.value)

/** 加载清单 */
async function load() {
  loading.value = true
  try {
    data.value = await getCases()
    pruneSelection()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

/** 移除清单中已不存在的选中项 */
function pruneSelection() {
  const validIds = new Set(modules.value.flatMap((m) => m.cases.map((c) => c.id)))
  selectedIds.value = selectedIds.value.filter((id) => validIds.has(id))
}

/** 触发执行 */
async function onExecute() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先勾选用例')
    return
  }
  submitting.value = true
  try {
    const res = await createRun([...selectedIds.value], operator.value.trim())
    start(res.run_id)
  } catch (e) {
    const err = e as ApiError
    if (err.code === 'RUN_IN_PROGRESS') {
      ElMessage.warning('已有任务在执行中')
      try {
        await ElMessageBox.confirm(
          '已有任务在执行中，是否前往执行记录页查看该任务？',
          '提示',
          { confirmButtonText: '查看该任务', cancelButtonText: '取消', type: 'warning' },
        )
        router.push('/runs')
      } catch {
        // 用户取消跳转，忽略
      }
    } else {
      ElMessage.error(err.message || '触发执行失败')
    }
  } finally {
    submitting.value = false
  }
}

// 轮询异常时给出提示
watch(error, (message) => {
  if (message) {
    ElMessage.error(message)
  }
})

onMounted(load)
</script>

<style scoped>
.case-list {
  position: relative;
  padding-bottom: 72px;
}

.block {
  margin-bottom: 12px;
}

.muted {
  color: #909399;
  font-size: 13px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  color: #606266;
}

.label {
  color: #606266;
  font-size: 13px;
}

.operator-input {
  width: 180px;
}

.groups {
  min-height: 120px;
}

.action-bar {
  position: fixed;
  right: 24px;
  bottom: 16px;
  left: 24px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 20px;
  padding: 12px 20px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.selected-count {
  color: #303133;
  font-size: 14px;
}

/* 收集失败详情保留换行 */
:deep(.el-alert__description) {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
