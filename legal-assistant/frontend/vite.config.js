import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '192.168.110.79',
    port: 5173,
    proxy: {
      '/api': 'http://192.168.110.79:8090'
    }
  }
})
