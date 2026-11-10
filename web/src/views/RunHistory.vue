<template>
  <div class="run-history">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>执行记录</span>
          <span class="header-meta">共 {{ total }} 次</span>
        </div>
      </template>
      <el-table v-loading="loading" :data="items" border stripe>
        <el-table-column label="开始时间" min-width="170">
          <template #default="{ row }">{{ formatLocalTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType[row.status as RunStatus]" size="small">
              {{ statusText[row.status as RunStatus] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="triggered_by" label="操作人" width="120" />
        <el-table-column prop="case_total" label="用例总数" width="100" align="center" />
        <el-table-column prop="case_passed" label="通过" width="80" align="center" />
        <el-table-column prop="case_failed" label="失败" width="80" align="center" />
        <el-table-column label="耗时" width="110" align="center">
          <template #default="{ row }">{{ formatDurationText(row.duration_ms) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRunReport(row)">查看报告</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          :page-size="limit"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="load"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'

import { listRuns } from '../api'
import { formatDurationText, formatLocalTime } from '../utils/datetime'
import type { RunListItem, RunStatus } from '../types'

const router = useRouter()
const limit = 20
const page = ref(1)
const total = ref(0)
const items = ref<RunListItem[]>([])
const loading = ref(false)

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

/** 服务端分页加载 */
async function load() {
  loading.value = true
  try {
    const res = await listRuns(limit, (page.value - 1) * limit)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

/** 跳转平台内的人类可读报告页 */
function openRunReport(row: RunListItem) {
  router.push({ name: 'run-report', params: { runId: row.run_id } })
}

onMounted(load)
</script>

<style scoped>
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

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
