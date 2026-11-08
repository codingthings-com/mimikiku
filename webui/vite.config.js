import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// In dev, the transcription socket and monitoring API are proxied so `vite` can front any backend:
//   MIMIKIKU_SERVER=http://gpu-box:7777 npm run dev
const server = process.env.MIMIKIKU_SERVER ?? 'http://localhost:7777'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/listen': { target: server, ws: true },
      '/api': server,
    },
  },
})
