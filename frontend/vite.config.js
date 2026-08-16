import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置：开发服务器把后端接口代理到本地 FastAPI，避免跨域问题
// 用 127.0.0.1 而不是 localhost，避免某些系统把 localhost 解析到 IPv6 导致连接失败
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 这四个路径是后端 API，其余请求由 Vite 处理（开发模式专用；
      // 正式使用时由 FastAPI 直接托管前端静态文件，无需本代理）
      '/upload': 'http://127.0.0.1:8000',
      '/process': 'http://127.0.0.1:8000',
      '/download': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
