import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // 启用 WebSocket 支持，有助于 SSE 流式传输
        // SSE 流式传输需要禁用缓冲和设置超时
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // 禁用缓冲，确保 SSE 事件立即转发
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['connection'] = 'keep-alive';
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        },
        // 增加超时时间，防止长响应被截断
        timeout: 300000, // 5 分钟
        proxyTimeout: 300000,
      }
    }
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react-router') || id.includes('react-dom') || id.includes('/react/'))
              return 'vendor-react';
            if (id.includes('antd') || id.includes('@ant-design'))
              return 'vendor-antd';
            if (id.includes('recharts') || id.includes('d3-'))
              return 'vendor-recharts';
            if (id.includes('react-markdown') || id.includes('remark') || id.includes('rehype'))
              return 'vendor-markdown';
          }
        },
      },
    },
  },
})
