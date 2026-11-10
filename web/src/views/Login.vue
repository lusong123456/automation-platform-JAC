<template>
  <div class="login-page">
    <el-card class="login-card">
      <h1 class="title">江淮智能合同审核</h1>
      <p class="subtitle">自动化接口回归测试平台</p>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            size="large"
            show-password
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" size="large" class="submit" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { login } from '../api'

const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await login(form.username, form.password)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/cases'
    await router.replace(redirect)
  } catch (err) {
    ElMessage.error((err as Error).message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
}

.login-card {
  width: 360px;
  padding: 8px 12px 20px;
}

.title {
  margin: 0;
  font-size: 20px;
  color: #303133;
  text-align: center;
}

.subtitle {
  margin: 8px 0 24px;
  font-size: 13px;
  color: #909399;
  text-align: center;
}

.submit {
  width: 100%;
}
</style>
