// 应用入口：完整引入 Element Plus 与路由
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'

createApp(App).use(ElementPlus).use(router).mount('#app')
