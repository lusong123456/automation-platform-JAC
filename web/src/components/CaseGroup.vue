<template>
  <el-card shadow="never" class="case-card">
    <div class="card-header">
      <el-checkbox
        :model-value="allSelected"
        :indeterminate="indeterminate"
        @change="toggleAll"
      >
        {{ title }}
      </el-checkbox>
      <span class="card-count">共 {{ rows.length }} 条</span>
    </div>
    <div class="card-body">
      <el-checkbox
        v-for="row in rows"
        :key="row.id"
        class="case-item"
        :model-value="modelValue.includes(row.id)"
        @change="handleCaseChange(row.id, $event)"
      >
        <span class="case-text" :title="row.id">
          <template v-if="showModule">
            <span class="module-name">{{ row.module }}</span>
            <span class="dot">·</span>
          </template>
          <span class="case-name">{{ row.name }}</span>
        </span>
      </el-checkbox>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { ModuleGroup } from '../types'

const props = withDefaults(
  defineProps<{
    title: string
    modules: ModuleGroup[]
    modelValue: string[]
    showModule?: boolean
  }>(),
  { showModule: true },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

/** 拍平为一维，展示「模块名 · 用例名」 */
const rows = computed(() =>
  props.modules.flatMap((m) => m.cases.map((c) => ({ ...c, module: m.name }))),
)

const allSelected = computed(
  () => rows.value.length > 0 && rows.value.every((r) => props.modelValue.includes(r.id)),
)

const indeterminate = computed(() => {
  const selected = rows.value.filter((r) => props.modelValue.includes(r.id)).length
  return selected > 0 && selected < rows.value.length
})

/** 整卡全选 / 取消全选 */
function toggleAll(checked: boolean | string | number) {
  const next = new Set(props.modelValue)
  rows.value.forEach((r) => (checked ? next.add(r.id) : next.delete(r.id)))
  emit('update:modelValue', Array.from(next))
}

function handleCaseChange(id: string, checked: boolean | string | number) {
  const next = new Set(props.modelValue)
  if (checked) {
    next.add(id)
  } else {
    next.delete(id)
  }
  emit('update:modelValue', Array.from(next))
}
</script>

<style scoped>
.case-card {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #ebeef5;
}

.card-header :deep(.el-checkbox__label) {
  font-size: 15px;
  font-weight: 600;
}

.card-count {
  color: #909399;
  font-size: 13px;
}

.card-body {
  padding-top: 6px;
}

.case-item {
  display: flex;
  width: 100%;
  height: 32px;
  margin-right: 0;
  padding: 0 8px;
  border-radius: 4px;
}

.case-item:hover {
  background: #f5f7fa;
}

.case-item :deep(.el-checkbox__label) {
  flex: 1;
  min-width: 0;
}

.case-text {
  display: flex;
  align-items: baseline;
  gap: 2px;
  overflow: hidden;
  white-space: nowrap;
}

.module-name {
  flex: none;
  font-weight: 600;
}

.dot {
  flex: none;
  color: #c0c4cc;
}

.case-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
