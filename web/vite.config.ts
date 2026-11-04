import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态代理：/api 与 /reports 转发到本地后端（uvicorn，端口 8000）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/reports': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
