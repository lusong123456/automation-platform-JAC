<template>
  <div class="contract-manager">
    <!-- 顶部卡片：上传 + AI 初稿 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>上传合同 / 生成初稿</span>
        </div>
      </template>
      <div class="upload-zone">
        <el-upload
          :show-file-list="false"
          :before-upload="onBeforeUpload"
          :http-request="doUpload"
          accept=".docx"
          class="upload-btn"
        >
          <el-button type="primary" :loading="uploading">
            <el-icon><Upload /></el-icon>
            上传 .docx
          </el-button>
        </el-upload>

        <el-upload
          :show-file-list="false"
          :before-upload="onBeforeUpload"
          :http-request="doAiDraft"
          accept=".docx"
          class="upload-btn"
        >
          <el-button type="success" :loading="aiDrafting">
            <el-icon><MagicStick /></el-icon>
            AI 自动生成标答初稿
          </el-button>
        </el-upload>

        <!-- 运行中：原地滚动进度链 -->
        <div v-if="aiDrafting && aiDraftProgress.length" class="ai-progress-block">
          <div class="ai-progress-chain">
            <template v-for="key in ['upload','extract','commit','poll','report']" :key="key">
              <span class="step" :class="aiStepClass(key)">
                <span class="step-icon">{{ aiStepIcon(key) }}</span>
                <span class="step-label">{{ AI_DRAFT_STEP_LABEL[key] || key }}</span>
                <span v-if="aiStepElapsed(key)" class="step-elapsed">{{ aiStepElapsed(key) }}s</span>
              </span>
              <span v-if="key !== 'report'" class="arrow">→</span>
            </template>
          </div>
          <div v-if="aiPollExtra()" class="ai-progress-extra">{{ aiPollExtra() }}</div>
          <el-progress :percentage="aiDraftPercent()" :stroke-width="6" class="ai-progress-bar" />
        </div>
        <!-- 空闲：提示文字 -->
        <el-tag v-else type="info" class="hint">
          💡 AI 初稿：上传 docx → 跑完整审核链路 → 后端真实审核结果自动转成标答 JSON，review 后保存即可
        </el-tag>
      </div>
    </el-card>

    <!-- 合同列表 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>合同清单（{{ contracts.length }} 个）</span>
          <el-button link type="primary" :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>

      <el-table v-loading="loading" :data="contracts" stripe size="default" empty-text="暂无合同，请先上传">
        <el-table-column prop="name" label="合同名称" min-width="280" show-overflow-tooltip />
        <el-table-column prop="size_kb" label="大小" width="80">
          <template #default="{ row }">{{ row.size_kb }} KB</template>
        </el-table-column>
        <el-table-column prop="uploaded_at" label="上传时间" width="180" />
        <el-table-column label="标答" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.has_standard" type="success" size="small">已配对</el-tag>
            <el-tag v-else type="warning" size="small">未配对</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.has_standard"
              size="small"
              type="success"
              link
              @click="onNewStandard(row)"
            >新建标答</el-button>
            <template v-else>
              <el-button size="small" type="primary" link @click="onEdit(row)">编辑标答</el-button>
              <el-button size="small" type="warning" link @click="onNewStandard(row)">重置标答</el-button>
            </template>
            <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 标答编辑器抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerTitle"
      size="80%"
      :close-on-click-modal="false"
      destroy-on-close
      class="editor-drawer"
    >
      <div v-if="editingLoading" class="drawer-loading">
        <el-skeleton :rows="10" animated />
      </div>
      <StandardEditor
        v-else
        ref="editorRef"
        v-model="editingData"
      />

      <template #footer>
        <div class="drawer-footer">
          <el-button @click="onDrawerClose">取消</el-button>
          <el-button
            v-if="editingData.length === 0"
            type="info"
            :loading="templating"
            @click="loadTemplate"
          >从模板生成</el-button>
          <el-button
            v-if="editingData.length === 0"
            type="success"
            :loading="aiDraftingDrawer"
            @click="doAiDraftFromDrawer"
          >AI 自动初稿</el-button>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="editingData.length === 0"
            @click="onSave"
          >保存</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MagicStick, Upload } from '@element-plus/icons-vue'

import StandardEditor from '../components/StandardEditor.vue'
import { useAiDraft } from '../composables/useAiDraft'
import {
  ApiError,
  deleteContract,
  getStandard,
  getTemplate,
  listContracts,
  refreshCases,
  saveStandard,
  uploadContract,
} from '../api'
import type { ContractItem, StandardGroup } from '../types'
import type { AiDraftSSEEvent } from '../api'

// ─── 状态 ───
const contracts = ref<ContractItem[]>([])
const loading = ref(false)
const uploading = ref(false)

const drawerVisible = ref(false)
const drawerTitle = ref('')
const currentStem = ref('')  // 当前编辑的合同 stem
const editorRef = ref<InstanceType<typeof StandardEditor> | null>(null)
const editingData = ref<StandardGroup[]>([])
const editingLoading = ref(false)
const saving = ref(false)
const templating = ref(false)
const aiDraftingDrawer = ref(false)

// AI 初稿：任务在服务端后台跑，状态存在模块级 store 里，切页不丢
const {
  state: aiDraftState,
  start: startAiDraftTask,
  restore: restoreAiDraft,
  consumeResult: consumeAiDraftResult,
} = useAiDraft()
const aiDrafting = computed(() => aiDraftState.phase === 'running')
const aiDraftProgress = computed(() => aiDraftState.events)
const AI_DRAFT_STEP_LABEL: Record<string, string> = {
  upload: '上传合同',
  extract: '解析合同',
  commit: '提交审核',
  poll: '等待审核结果',
  report: '拉取报告',
}
function aiDraftPercent(): number {
  const steps = ['upload', 'extract', 'commit', 'poll', 'report']
  if (!aiDraftProgress.value.length) return 0
  let lastPollIdx = 0
  let pollTotal = 60
  let doneCount = 0
  let runningCount = 0
  for (const key of steps) {
    const evts = aiDraftProgress.value.filter((e) => e.step === key)
    if (!evts.length) continue
    const last = evts[evts.length - 1]
    if (last.status === 'done') {
      doneCount++
    } else if (last.status === 'running') {
      runningCount++
      if (key === 'poll') {
        lastPollIdx = last.idx || 0
        pollTotal = last.total || 60
      }
    }
  }
  // poll 细化
  if (runningCount > 0 && lastPollIdx > 0) {
    return Math.round(((doneCount + lastPollIdx / pollTotal) / steps.length) * 100)
  }
  return Math.round((doneCount / steps.length) * 100)
}

function _lastStep(key: string): AiDraftSSEEvent | null {
  const evts = aiDraftProgress.value.filter((e) => e.step === key && !e.done)
  return evts.length ? evts[evts.length - 1] : null
}
function aiStepClass(key: string): string {
  const last = _lastStep(key)
  if (!last) return 'pending'
  if (last.status === 'done') return 'done'
  if (last.status === 'fail') return 'fail'
  return 'running'
}
function aiStepIcon(key: string): string {
  const c = aiStepClass(key)
  if (c === 'done') return '✅'
  if (c === 'fail') return '❌'
  if (c === 'running') return '🔄'
  return '⏳'
}
function aiStepElapsed(key: string): number | undefined {
  const last = _lastStep(key)
  return last?.elapsed
}
function aiPollExtra(): string {
  const poll = _lastStep('poll')
  if (!poll || poll.status !== 'running') return ''
  const idx = poll.idx || 0
  const total = poll.total || 60
  const remaining = (total - idx) * 5
  if (remaining > 0) return `第 ${idx}/${total} 次轮询，剩余 ~${remaining}s，后端状态：${poll.auditStatus || '—'}`
  return `第 ${idx}/${total} 次轮询`
}

// ─── 列表加载 ───
async function load() {
  loading.value = true
  try {
    contracts.value = await listContracts()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

// ─── 上传 ───
function onBeforeUpload(file: File): boolean {
  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.error('仅支持 .docx 文件')
    return false
  }
  if (file.size > 50 * 1024 * 1024) {
    ElMessage.error('文件超过 50MB，请使用更小的合同文件')
    return false
  }
  return true
}

async function doUpload({ file }: { file: File }) {
  uploading.value = true
  try {
    const result = await uploadContract(file)
    ElMessage.success(`上传成功：${result.name}`)
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message || '上传失败')
  } finally {
    uploading.value = false
  }
}

/** AI 初稿完成：先自动落盘标答，再打开编辑器给用户 review */
async function openAiDraftResult() {
  const result = consumeAiDraftResult()
  if (!result) return
  currentStem.value = aiDraftState.stem
  aiDraftingDrawer.value = false
  // 先存后弹：用户点「取消」也不影响已保存的 AI 初稿；点「保存」只是覆盖修改后的版本
  try {
    await saveStandard(currentStem.value, result.standard_data)
    ElMessage.success(
      `AI 初稿已自动保存：${result.total_groups} 个分组，${result.total_issues} 条规则（可直接修改后再保存）`
    )
    void refreshCases().catch(() => undefined)
  } catch (e) {
    ElMessage.error(`AI 初稿自动保存失败：${(e as Error).message}`)
  }
  editingData.value = result.standard_data
  drawerTitle.value = `编辑标答 - ${aiDraftState.contractName}（已自动保存）`
  drawerVisible.value = true
  void load()
}

// 任务在后台完成时（无论用户是否还在本页）都会走到这里
watch(
  () => aiDraftState.phase,
  (phase) => {
    if (phase === 'success') void openAiDraftResult()
    else if (phase === 'fail' && aiDraftState.error) ElMessage.error(aiDraftState.error)
  },
)

async function doAiDraft({ file }: { file: File }) {
  try {
    await startAiDraftTask(file)
    await load()   // 合同已在服务端落盘，立即刷新列表
  } catch (e) {
    ElMessage.error((e as Error).message || 'AI 初稿启动失败')
  }
}

// ─── 列表操作 ───
async function onEdit(row: ContractItem) {
  currentStem.value = row.stem
  drawerTitle.value = `编辑标答 - ${row.name}`
  editingData.value = []
  drawerVisible.value = true
  editingLoading.value = true
  try {
    const resp = await getStandard(row.stem)
    editingData.value = resp.data
  } catch (e) {
    const err = e as ApiError
    // 标答不存在（404）是正常场景——打开空抽屉让用户新建
    if (err.code === '404' || err.message.includes('不存在')) {
      editingData.value = []
    } else {
      ElMessage.error(err.message || '加载标答失败')
      drawerVisible.value = false
    }
  } finally {
    editingLoading.value = false
  }
}

async function onNewStandard(row: ContractItem) {
  currentStem.value = row.stem
  drawerTitle.value = `新建标答 - ${row.name}`
  editingData.value = []
  drawerVisible.value = true
  editingLoading.value = true
  try {
    const tpl = await getTemplate(row.name)
    editingData.value = tpl.groups
    ElMessage.success(`已加载模板：${tpl.total_issues} 条规则，请 review 后保存`)
  } catch (e) {
    const err = e as ApiError
    ElMessage.error(err.message || '模板加载失败')
    drawerVisible.value = false
  } finally {
    editingLoading.value = false
  }
}

async function onDelete(row: ContractItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除合同「${row.name}」吗？${row.has_standard ? '同名标答也会一起删除。' : ''}`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const result = await deleteContract(row.stem)
    const parts = []
    if (result.deleted.contract) parts.push('合同')
    if (result.deleted.standard) parts.push('标答')
    ElMessage.success(`已删除：${parts.join(' + ')}`)
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message || '删除失败')
  }
}

// ─── 抽屉内操作 ───
async function loadTemplate() {
  templating.value = true
  try {
    const tpl = await getTemplate(currentStem.value)
    editingData.value = tpl.groups
    ElMessage.success(`模板已加载：${tpl.total_issues} 条规则`)
  } catch (e) {
    ElMessage.error((e as Error).message || '模板加载失败')
  } finally {
    templating.value = false
  }
}

async function doAiDraftFromDrawer() {
  const file = await pickDocxFile()
  if (!file) return
  aiDraftingDrawer.value = true
  try {
    await startAiDraftTask(file)
    await load()
    // 完成后由 watch(aiDraftState.phase) 统一把结果回填到当前抽屉
  } catch (e) {
    aiDraftingDrawer.value = false
    ElMessage.error((e as Error).message || 'AI 初稿启动失败')
  }
}

/** 浏览器端选择文件 */
function pickDocxFile(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.docx'
    input.onchange = () => {
      const file = input.files?.[0] || null
      if (file && !file.name.toLowerCase().endsWith('.docx')) {
        ElMessage.error('请选择 .docx 文件')
        resolve(null)
        return
      }
      resolve(file)
    }
    input.click()
  })
}

async function onSave() {
  saving.value = true
  try {
    // 从编辑器获取干净的数据（不管当前在表单还是 JSON 模式）
    const cleanData = editorRef.value?.getCleanGroups() || editingData.value
    if (!cleanData.length) {
      ElMessage.warning('没有可保存的内容')
      return
    }
    await saveStandard(currentStem.value, cleanData)
    // 保存成功后刷新用例清单，让新合同自动生成测试用例
    try {
      const manifest = await refreshCases()
      ElMessage.success(
        `标答已保存，用例清单已刷新：共 ${manifest.total} 条用例`
      )
    } catch {
      // 刷新失败不影响保存本身
      ElMessage.success('标答已保存（用例清单刷新失败，请稍后手动刷新）')
    }
    drawerVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message || '保存失败')
  } finally {
    saving.value = false
  }
}

function onDrawerClose() {
  drawerVisible.value = false
}

onMounted(async () => {
  void load()
  const restored = await restoreAiDraft().catch(() => false)
  if (!restored) return
  await load()
  // 切页期间任务已完成：直接回填编辑器（consume 保证只弹一次）
  if (aiDraftState.phase === 'success') void openAiDraftResult()
  else if (aiDraftState.phase === 'fail') {
    ElMessage.error(aiDraftState.error || 'AI 初稿生成失败')
  }
})
</script>

<style scoped>
.contract-manager {
  padding-bottom: 24px;
}

.block {
  margin-bottom: 12px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.upload-zone {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
}

.upload-btn {
  display: inline-block;
}

.hint {
  max-width: 500px;
  line-height: 1.5;
}

.drawer-loading {
  padding: 24px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

/* AI 初稿原地进度链 */
.ai-progress-block {
  flex: 1 0 100%;
  margin-top: 8px;
}
.ai-progress-chain {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
  font-size: 13px;
  line-height: 1.8;
}
.ai-progress-chain .step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #f5f7fa;
  color: #909399;
  transition: all 0.2s;
}
.ai-progress-chain .step.done { background: #f0f9eb; color: #67c23a; }
.ai-progress-chain .step.running { background: #ecf5ff; color: #409eff; font-weight: 600; }
.ai-progress-chain .step.fail { background: #fef0f0; color: #f56c6c; }
.ai-progress-chain .step-icon { font-size: 14px; }
.ai-progress-chain .step-elapsed { color: #909399; font-size: 12px; margin-left: 2px; }
.ai-progress-chain .arrow { color: #c0c4cc; margin: 0 2px; font-weight: bold; }

.ai-progress-extra {
  font-size: 12px;
  color: #409eff;
  margin-top: 4px;
}
.ai-progress-bar {
  margin-top: 8px;
  max-width: 600px;
}
</style>
