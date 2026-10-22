import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // "@/api/client" instead of "../../../api/client"
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    // 0.0.0.0 so it's reachable from outside the container
    host: '0.0.0.0',
    port: 5173,
    // bind mounts don't send fs events on windows/mac, poll for hot reload
    watch: { usePolling: true },
    // the public tunnel only reaches this server, so the api is served from the same address
    proxy: {
      '/api': { target: 'http://backend:8000', changeOrigin: true },
    },
    allowedHosts: ['.trycloudflare.com'],
  },
})
