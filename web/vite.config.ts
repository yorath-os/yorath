import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// In dev, /api/* requests go to core (default localhost:8000; in Compose, http://core:8000).
export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', setupFiles: './test/setup.ts', include: ['src/**/*.test.tsx'] },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
