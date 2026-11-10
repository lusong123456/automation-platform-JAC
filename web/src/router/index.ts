// 路由：使用 hash 模式，避免服务端路由 fallback
import { createRouter, createWebHashHistory } from 'vue-router'

import { getMe } from '../api'
import CaseList from '../views/CaseList.vue'
import ContractManager from '../views/ContractManager.vue'
import Login from '../views/Login.vue'
import RunHistory from '../views/RunHistory.vue'
import RunReport from '../views/RunReport.vue'
import Settings from '../views/Settings.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    { path: '/', redirect: '/cases' },
    { path: '/cases', name: 'cases', component: CaseList },
    { path: '/runs', name: 'runs', component: RunHistory },
    { path: '/runs/:runId/report', name: 'run-report', component: RunReport },
    { path: '/contracts', name: 'contracts', component: ContractManager },
    { path: '/settings', name: 'settings', component: Settings },
  ],
})

// 鉴权未开启或已登录时放行，否则跳登录页并记住来源
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  try {
    const me = await getMe()
    if (!me.auth_enabled || me.username) return true
  } catch {
    // /auth/me 返回 401，按未登录处理
  }
  return { name: 'login', query: { redirect: to.fullPath } }
})

export default router
