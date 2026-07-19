import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'vite.svg'],
      manifest: {
        name: 'Agent Memory System',
        short_name: 'AgentMemory',
        description: 'Agent 记忆系统',
        theme_color: '#1677ff',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        // SPA 回退到 index.html，但 /api 不参与导航回退
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            // 仅缓存 /api 的 GET 请求，且排除 SSE/流式端点，避免破坏流式传输
            urlPattern: ({ url, request }) =>
              url.pathname.startsWith('/api') &&
              request.method === 'GET' &&
              !url.pathname.includes('/stream'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-get-cache',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 100, maxAgeSeconds: 60 * 60 * 24 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
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
