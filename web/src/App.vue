<template>
  <router-view v-if="isLogin" />
  <el-container v-else class="layout">
    <el-header class="header">
      <div class="header-title">江淮智能合同审核 · 自动化接口回归测试平台</div>
      <el-menu
        class="nav"
        mode="horizontal"
        :default-active="activeMenu"
        :ellipsis="false"
        router
      >
        <el-menu-item index="/cases">用例列表</el-menu-item>
        <el-menu-item index="/contracts">合同/标答管理</el-menu-item>
        <el-menu-item index="/runs">执行记录</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
      <el-button v-if="showLogout" link class="logout" @click="onLogout">退出登录</el-button>
    </el-header>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getMe, logout } from './api'

const route = useRoute()
const router = useRouter()
const isLogin = computed(() => route.name === 'login')
const showLogout = ref(false)
// 以当前路由路径高亮导航项（报告页归属执行记录）
const activeMenu = computed(() => (route.path.startsWith('/runs/') ? '/runs' : route.path))

// 仅鉴权开启时展示退出入口
onMounted(async () => {
  try {
    showLogout.value = (await getMe()).auth_enabled
  } catch {
    // 未登录由路由守卫处理
  }
})

async function onLogout() {
  await logout().catch(() => undefined)
  router.replace({ name: 'login' })
}
</script>

<style scoped>
.layout {
  min-height: 100vh;
}

.header {
  display: flex;
  align-items: center;
  gap: 32px;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
}

.nav {
  flex: 1;
  border-bottom: none;
}

.logout {
  flex-shrink: 0;
}

.main {
  background: #f5f7fa;
  padding: 16px 24px;
}
</style>
