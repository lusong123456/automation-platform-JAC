<template>
  <div class="standard-editor">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-radio-group v-model="mode" size="small">
          <el-radio-button value="form">表单模式</el-radio-button>
          <el-radio-button value="json">JSON 模式</el-radio-button>
        </el-radio-group>
        <span class="muted">
          {{ flattenedCount }} 条规则 ·
          {{ compliantCount }} 合规 ·
          {{ compliantCount !== flattenedCount ? flattenedCount - compliantCount : 0 }} 不合规
        </span>
      </div>
      <div class="toolbar-right">
        <el-button size="small" @click="formatJson" v-if="mode === 'json'">格式化</el-button>
        <el-button size="small" @click="validateJson" v-if="mode === 'json'">校验</el-button>
        <el-button size="small" @click="reset">重置</el-button>
      </div>
    </div>

    <!-- 表单模式 -->
    <div v-if="mode === 'form'" class="form-mode">
      <el-empty v-if="!groups.length" description="暂无标答内容，点击上方按钮从模板生成或 AI 自动初稿" />

      <el-collapse v-else v-model="activeCollapse" class="group-collapse">
        <el-collapse-item
          v-for="(group, gIdx) in groups"
          :key="group.parentRuleId"
          :name="group.parentRuleId"
        >
          <template #title>
            <span class="group-title">
              <el-tag size="small" type="info">{{ group.parentRuleId }}</el-tag>
              <span class="group-intent">{{ group.parentRuleIntent || '未命名分组' }}</span>
              <el-tag size="small" type="success">{{ group.issues.length }} 条规则</el-tag>
            </span>
          </template>

          <div v-for="(issue, iIdx) in group.issues" :key="issue.ruleId || iIdx" class="issue-item">
            <div class="issue-header">
              <el-input
                v-model="issue.ruleId"
                size="small"
                class="rule-id-input"
                placeholder="ruleId"
              />
              <el-input
                v-model="issue.ruleIntent"
                size="small"
                class="rule-intent-input"
                placeholder="ruleIntent"
              />
              <el-switch
                v-model="issue.compliant"
                active-text="合规"
                inactive-text="不合规"
              />
              <el-button
                link
                type="danger"
                size="small"
                @click="removeIssue(gIdx, iIdx)"
              >删除</el-button>
            </div>
            <el-form :model="issue" label-width="80px" class="issue-form">
              <el-form-item label="原文">
                <el-input
                  v-model="issue.original"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="合同原文片段"
                />
              </el-form-item>
              <el-form-item label="修订建议">
                <el-input
                  v-model="issue.revised"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="建议修改后的文本（合规时可留空）"
                />
              </el-form-item>
              <el-form-item label="审核建议">
                <el-input
                  v-model="issue.suggestion"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="审核意见说明（合规时可填'审核通过'）"
                />
              </el-form-item>
            </el-form>
          </div>

          <div class="group-footer">
            <el-button size="small" @click="addIssue(gIdx)">+ 新增规则</el-button>
            <el-button size="small" type="danger" link @click="removeGroup(gIdx)">- 删除该分组</el-button>
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- 全局增删 -->
      <div class="global-actions">
        <el-button size="small" @click="addGroup">+ 新增分组</el-button>
      </div>
    </div>

    <!-- JSON 模式 -->
    <div v-else class="json-mode">
      <el-input
        v-model="jsonText"
        type="textarea"
        :autosize="{ minRows: 20 }"
        class="json-textarea"
        placeholder="粘贴或编辑 JSON..."
      />
      <div v-if="jsonError" class="json-error">
        <el-icon><Warning /></el-icon>
        {{ jsonError }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning } from '@element-plus/icons-vue'

import type { StandardGroup, StandardIssue } from '../types'

const props = defineProps<{
  modelValue: StandardGroup[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: StandardGroup[]): void
  (e: 'change'): void
}>()

const groups = ref<StandardGroup[]>(clone(props.modelValue))

// 初始化默认展开所有分组
const activeCollapse = ref<string[]>([])
watch(() => props.modelValue, (v) => {
  groups.value = clone(v)
  activeCollapse.value = groups.value.map(g => g.parentRuleId).filter(Boolean)
}, { immediate: true })

// 双模式同步
const mode = ref<'form' | 'json'>('form')
const jsonText = ref('')
const jsonError = ref<string | null>(null)

watch(mode, (newMode, oldMode) => {
  if (newMode === 'json' && oldMode === 'form') {
    // 表单 → JSON
    jsonText.value = JSON.stringify(groups.value, null, 2)
    jsonError.value = null
  } else if (newMode === 'form' && oldMode === 'json') {
    // JSON → 表单：先校验再同步
    try {
      const parsed = JSON.parse(jsonText.value)
      if (!Array.isArray(parsed)) throw new Error('顶层必须是数组')
      groups.value = parsed as StandardGroup[]
      jsonError.value = null
    } catch (e) {
      ElMessage.error('JSON 解析失败：' + (e as Error).message)
      // 保持原表单数据，不切换
      mode.value = 'json'
    }
  }
})

// 监听 groups 变更，只 emit change 事件（不 emit update:modelValue，避免父组件回写 → clone → 死循环）
watch(groups, (v) => {
  emit('change')
  // 如果在 JSON 模式，实时同步 text（但不强制重新 parse）
  if (mode.value === 'json') {
    try {
      // 只有 text 当前是合法 JSON 时才更新，避免覆盖用户正在编辑但未 parse 的半成品
      JSON.parse(jsonText.value)
      jsonText.value = JSON.stringify(v, null, 2)
    } catch {
      // 用户正在编辑半成品，不覆盖
    }
  }
}, { deep: true })

// 外部数据首次进入：clone 到内部 state；后续不再自动跟随父组件变化（单向数据流）
onMounted(() => {
  groups.value = clone(props.modelValue || [])
  activeCollapse.value = groups.value.map(g => g.parentRuleId).filter(Boolean)
})

// ─── 统计 ───
const flattenedCount = computed(() =>
  groups.value.reduce((s, g) => s + (g.issues?.length || 0), 0)
)
const compliantCount = computed(() =>
  groups.value.reduce((s, g) =>
    s + (g.issues?.filter(i => i.compliant).length || 0), 0
  )
)

// ─── 表单操作 ───
function addGroup() {
  const idx = groups.value.length
  groups.value.push({
    parentRuleId: `NEW_GROUP_${Date.now()}`,
    parentRuleIntent: '新分组',
    issues: [{
      ruleId: '',
      ruleIntent: '',
      compliant: true,
      original: '',
      revised: '',
      suggestion: '',
    }],
  })
  activeCollapse.value.push(groups.value[idx].parentRuleId)
}

function removeGroup(gIdx: number) {
  groups.value.splice(gIdx, 1)
}

function addIssue(gIdx: number) {
  const newIssue: StandardIssue = {
    ruleId: '',
    ruleIntent: '',
    compliant: true,
    original: '',
    revised: '',
    suggestion: '',
  }
  groups.value[gIdx].issues.push(newIssue)
}

function removeIssue(gIdx: number, iIdx: number) {
  groups.value[gIdx].issues.splice(iIdx, 1)
}

function reset() {
  groups.value = clone(props.modelValue)
  ElMessage.info('已重置为上次保存的状态')
}

// ─── JSON 操作 ───
function formatJson() {
  try {
    jsonText.value = JSON.stringify(JSON.parse(jsonText.value), null, 2)
    jsonError.value = null
    ElMessage.success('格式化成功')
  } catch (e) {
    jsonError.value = 'JSON 解析失败：' + (e as Error).message
    ElMessage.error(jsonError.value)
  }
}

function validateJson() {
  try {
    const parsed = JSON.parse(jsonText.value)
    if (!Array.isArray(parsed)) throw new Error('顶层必须是数组')
    // 简易字段校验
    for (let gi = 0; gi < parsed.length; gi++) {
      const g = parsed[gi]
      if (!g.parentRuleId) throw new Error(`第 ${gi + 1} 个分组缺少 parentRuleId`)
      if (!Array.isArray(g.issues)) throw new Error(`第 ${gi + 1} 个分组 issues 必须是数组`)
      for (let ii = 0; ii < g.issues.length; ii++) {
        const i = g.issues[ii]
        if (typeof i.compliant !== 'boolean') {
          throw new Error(`分组 ${gi + 1} 的第 ${ii + 1} 条规则 compliant 必须是布尔`)
        }
      }
    }
    jsonError.value = null
    ElMessage.success('JSON 校验通过')
  } catch (e) {
    jsonError.value = (e as Error).message
    ElMessage.error('校验失败：' + jsonError.value)
  }
}

// ─── 工具 ───
function clone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj))
}

/** 供父组件调用：统一获取干净的数据（不管当前在哪个模式） */
function getCleanGroups(): StandardGroup[] {
  if (mode.value === 'json') {
    try {
      const parsed = JSON.parse(jsonText.value)
      if (Array.isArray(parsed)) return parsed as StandardGroup[]
    } catch {
      // fall through
    }
  }
  return JSON.parse(JSON.stringify(groups.value))
}

defineExpose({ getCleanGroups })
</script>

<style scoped>
.standard-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.muted {
  color: #909399;
  font-size: 12px;
}

.group-collapse {
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.group-intent {
  font-weight: 500;
  color: #303133;
}

.issue-item {
  padding: 12px;
  margin-bottom: 8px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}

.issue-header {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}

.rule-id-input {
  width: 180px;
}

.rule-intent-input {
  flex: 1;
}

.issue-form {
  padding-left: 4px;
}

.group-footer {
  display: flex;
  gap: 16px;
  padding-top: 8px;
  border-top: 1px dashed #dcdfe6;
  margin-top: 8px;
}

.global-actions {
  text-align: center;
  padding: 8px;
}

.json-mode {
  position: relative;
}

.json-textarea {
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.json-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 8px 12px;
  background: #fef0f0;
  border: 1px solid #fde2e2;
  color: #f56c6c;
  border-radius: 4px;
  font-size: 13px;
}
</style>
