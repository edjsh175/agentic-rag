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
      rewrite: (path) => path.replace(/^\/api/, '')
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
