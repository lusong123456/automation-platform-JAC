<template>
  <el-card shadow="never" class="case-group">
    <div class="group-header">
      <el-checkbox
        :model-value="allSelected"
        :indeterminate="indeterminate"
        @change="toggleModule"
      >
        本模块全选 · {{ module.name }}
      </el-checkbox>
      <span class="group-count">{{ module.cases.length }} 条</span>
    </div>
    <div class="group-body">
      <el-checkbox
        v-for="item in module.cases"
        :key="item.id"
        :model-value="modelValue.includes(item.id)"
        @change="handleCaseChange(item.id, $event)"
      >
        <span :title="item.id">{{ item.name }}</span>
      </el-checkbox>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { ModuleGroup } from '../types'

const props = defineProps<{
  module: ModuleGroup
  modelValue: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const moduleIds = computed(() => props.module.cases.map((c) => c.id))

const allSelected = computed(() => {
  const ids = moduleIds.value
  return ids.length > 0 && ids.every((id) => props.modelValue.includes(id))
})

// 部分选中：有选中但未全选
const indeterminate = computed(() => {
  const ids = moduleIds.value
  const selectedCount = ids.filter((id) => props.modelValue.includes(id)).length
  return selectedCount > 0 && selectedCount < ids.length
})

/** 本模块全选 / 取消全选 */
function toggleModule(checked: boolean | string | number) {
  const ids = moduleIds.value
  const next = new Set(props.modelValue)
  if (checked) {
    ids.forEach((id) => next.add(id))
  } else {
    ids.forEach((id) => next.delete(id))
  }
  emit('update:modelValue', Array.from(next))
}

/** 单个用例勾选切换 */
function toggleCase(id: string, checked: boolean | string | number) {
  const next = new Set(props.modelValue)
  if (checked) {
    next.add(id)
  } else {
    next.delete(id)
  }
  emit('update:modelValue', Array.from(next))
}

/** 供模板 v-for 内使用的包装方法 */
function handleCaseChange(id: string, checked: boolean | string | number) {
  toggleCase(id, checked)
}
</script>

<style scoped>
.case-group {
  margin-bottom: 12px;
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.group-count {
  color: #909399;
  font-size: 13px;
}

.group-body {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
</style>
