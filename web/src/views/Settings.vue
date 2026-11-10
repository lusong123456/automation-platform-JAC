<template>
  <div class="settings-page">
    <el-card class="settings-card">
      <template #header>
        <div class="head">
          <span class="title">系统设置</span>
          <div class="actions">
            <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          </div>
        </div>
      </template>

      <el-form label-width="180px" class="form">
        <template v-for="group in groups" :key="group.name">
          <div class="group-title">{{ group.name }}</div>
          <el-form-item v-for="item in group.list" :key="item.key" :label="item.label">
            <span v-if="item.readonly" class="readonly-value">{{ readonlyText(item) }}</span>
            <template v-else>
              <el-input
                v-if="item.type === 'string'"
                v-model="model[item.key]"
                class="field"
              />
              <el-input
                v-else-if="item.type === 'password'"
                v-model="model[item.key]"
                type="password"
                show-password
                class="field"
                :placeholder="item.configured ? '已配置，留空表示不修改' : '未配置'"
              />
              <el-input-number
                v-else-if="item.type === 'int' || item.type === 'float'"
                v-model="model[item.key]"
                class="field"
                :min="item.min"
                :max="item.max"
                :step="item.type === 'float' ? 0.05 : 1"
                :precision="item.type === 'float' ? 2 : 0"
              />
              <el-switch v-else-if="item.type === 'bool'" v-model="model[item.key]" />
              <el-select v-else-if="item.type === 'select'" v-model="model[item.key]" class="field">
                <el-option
                  v-for="choice in item.choices"
                  :key="choice.value"
                  :label="choice.label"
                  :value="choice.value"
                />
              </el-select>
            </template>
            <div class="hint">
              <span>{{ item.help }}</span>
            </div>
          </el-form-item>
        </template>
      </el-form>
    </el-card>

    <el-card class="settings-card">
      <template #header>
        <div class="head">
          <span class="title">用户管理</span>
          <el-button type="primary" @click="openCreate">新增用户</el-button>
        </div>
      </template>

      <el-table v-loading="usersLoading" :data="users" size="default">
        <el-table-column prop="username" label="用户名" min-width="160" />
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatLocalTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="danger" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="users-empty">暂无附加用户，点击右上角「新增用户」创建</div>
        </template>
      </el-table>
    </el-card>

    <el-dialog v-model="createVisible" title="新增用户" width="460px">
      <el-form label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { createUser, deleteUser, getSettings, listUsers, updateSettings } from '../api'
import { formatLocalTime } from '../utils/datetime'
import type { ManagedUser, SettingsItem } from '../types'

const items = ref<SettingsItem[]>([])
const model = reactive<Record<string, unknown>>({})
const saving = ref(false)

const users = ref<ManagedUser[]>([])
const usersLoading = ref(false)
const createVisible = ref(false)
const creating = ref(false)
const form = reactive({ username: '', password: '' })

const groups = computed(() => {
  const map = new Map<string, SettingsItem[]>()
  for (const item of items.value) {
    if (item.merged) continue // 合并到上一行的项不单独渲染
    map.set(item.group, [...(map.get(item.group) ?? []), item])
  }
  return [...map].map(([name, list]) => ({ name, list }))
})

async function load() {
  try {
    const response = await getSettings()
    items.value = response.items
    for (const item of response.items) {
      model[item.key] = item.type === 'password' ? '' : item.value
    }
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

async function loadUsers() {
  usersLoading.value = true
  try {
    users.value = await listUsers()
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    usersLoading.value = false
  }
}

/** 只读项展示：密码默认只显示是否已配置，标记 plain 的明文展示 */
function readonlyText(item: SettingsItem): string {
  if (item.type === 'password' && !item.plain) {
    return item.configured ? '已配置' : '未配置'
  }
  return String(item.value ?? '') || '未配置'
}

/** 只收集改动项：密码项非空才提交，留空表示不修改 */
function collectChanges(): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const item of items.value) {
    if (item.readonly) continue
    const value = model[item.key]
    if (item.type === 'password') {
      if (value) payload[item.key] = value
      continue
    }
    if (value !== item.value && value !== null && value !== undefined) {
      payload[item.key] = value
    }
  }
  return payload
}

async function save() {
  const payload = collectChanges()
  if (!Object.keys(payload).length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  saving.value = true
  try {
    const result = await updateSettings(payload)
    ElMessage.success(`已保存 ${result.saved.length} 项`)
    await load()
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    saving.value = false
  }
}

function openCreate() {
  form.username = ''
  form.password = ''
  createVisible.value = true
}

async function submitCreate() {
  if (!form.username.trim()) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (form.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  creating.value = true
  try {
    await createUser({
      username: form.username.trim(),
      password: form.password,
    })
    ElMessage.success('用户已创建')
    createVisible.value = false
    await loadUsers()
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    creating.value = false
  }
}

async function onDelete(row: ManagedUser) {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username}」？删除后该账号将立即无法登录。`,
      '删除用户',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteUser(row.id)
    ElMessage.success('用户已删除')
    await loadUsers()
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

onMounted(() => {
  void load()
  void loadUsers()
})
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-card {
  width: 100%;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.title {
  font-size: 15px;
  font-weight: 600;
}

.form {
  margin-top: 8px;
}

.group-title {
  margin: 20px 0 12px;
  padding-left: 8px;
  border-left: 3px solid var(--el-color-primary);
  font-size: 14px;
  font-weight: 600;
}

.field {
  width: 100%;
  max-width: 720px;
}

.readonly-value {
  font-size: 14px;
  color: #606266;
  line-height: 32px;
}

.users-empty {
  padding: 16px 0;
  font-size: 13px;
  color: #909399;
}

.hint {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #909399;
}
</style>
