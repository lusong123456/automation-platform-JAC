<template>
  <el-container class="layout">
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
        <el-menu-item index="/accuracy">审核正确性</el-menu-item>
      </el-menu>
    </el-header>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
// 以当前路由路径高亮导航项
const activeMenu = computed(() => route.path)
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

.main {
  background: #f5f7fa;
  padding: 16px 24px;
}
</style>
