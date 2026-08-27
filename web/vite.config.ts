import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  // Only the production build is ever served from a subpath (the rack
  // server's nginx serves it under /mira/) — the local dev server always
  // stays at root so `npm run dev` behavior is unchanged.
  base: command === 'build' ? '/mira/' : '/',
  server: {
    port: 5173,
  },
}))
