// 路由：使用 hash 模式，避免服务端路由 fallback
import { createRouter, createWebHashHistory } from 'vue-router'

import AccuracyReview from '../views/AccuracyReview.vue'
import CaseList from '../views/CaseList.vue'
import ContractManager from '../views/ContractManager.vue'
import RunHistory from '../views/RunHistory.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/cases' },
    { path: '/cases', name: 'cases', component: CaseList },
    { path: '/runs', name: 'runs', component: RunHistory },
    { path: '/accuracy', name: 'accuracy', component: AccuracyReview },
    { path: '/contracts', name: 'contracts', component: ContractManager },
  ],
})

export default router
