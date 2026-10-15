import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(rootDir, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      // Docker Desktop on Windows often misses file events; polling keeps HMR in sync with the host volume.
      usePolling: true,
      interval: 800,
    },
    // Browser calls same-origin /api/... → Vite forwards to FastAPI (avoids CORS in Docker/local dev).
    proxy: {
      '/api': {
        target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
