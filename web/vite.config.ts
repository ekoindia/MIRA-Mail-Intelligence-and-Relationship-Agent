import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  // Matches wa-issue-fetcher's own deployment: reverse-proxied under a
  // path prefix on the shared csp-dashboard nginx host, not a dedicated
  // port. Only the production build carries the prefix — the local dev
  // server always stays at root.
  base: command === 'build' ? '/mira/' : '/',
  server: {
    port: 5173,
  },
}))
