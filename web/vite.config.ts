import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],

server: {
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:10605', // 本机后端开发服务
      changeOrigin: true,
      // 如果后端接口没有 /api 前缀，这一行至关重要
      rewrite: (path) => path.replace(/^\/api/, ''),
      // 问答调试等长请求 / SSE 可能超过默认代理超时
      timeout: 600_000,
      proxyTimeout: 600_000,
      configure: (proxy) => {
        // 避免 Vite 代理把 text/event-stream 整包缓冲，导致调试页干等
        proxy.on('proxyRes', (proxyRes, _req, res) => {
          const ct = String(proxyRes.headers['content-type'] || '')
          if (ct.includes('text/event-stream')) {
            res.setHeader('Cache-Control', 'no-cache, no-transform')
            res.setHeader('X-Accel-Buffering', 'no')
            delete proxyRes.headers['content-length']
          }
        })
      },
    },
    "/articleImg": {
      target: 'http://192.168.10.206:8080/zsltStaticData', // 你的 Docker 宿主机 IP 和映射端口
      changeOrigin: true,
      // 如果后端接口没有 /api 前缀，这一行至关重要
      rewrite: (path) => path.replace(/^\/articleImg/, '')
    },
    '/scraping': {
       target: 'http://127.0.0.1',
        changeOrigin: true
    }
  }
}
})
