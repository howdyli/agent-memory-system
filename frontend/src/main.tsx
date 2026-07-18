import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { registerSW } from 'virtual:pwa-register'
import './index.css'
import App from './App.tsx'

// 注册 Service Worker（autoUpdate：新版本可用时自动更新）
registerSW({
  immediate: true,
  onOfflineReady() {
    console.info('应用已可离线使用')
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
