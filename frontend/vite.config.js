import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import dns from 'node:dns';

// Resolve "localhost" to IPv4 (127.0.0.1) first inside this Node process.
// Docker Desktop on Windows/WSL publishes the backend on both IPv4 and IPv6,
// but its IPv6 ([::1]) relay can wedge — it accepts the TCP connection yet
// never answers. The browser resolves "localhost" to ::1 first and gets stuck
// on that dead relay ("Network error"). By routing all API calls through the
// dev proxy below (so the browser only talks to this dev server) and forcing
// the proxy's own lookups to IPv4, we sidestep the broken IPv6 path entirely.
dns.setDefaultResultOrder('ipv4first');

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@features': fileURLToPath(new URL('./src/features', import.meta.url)),
      '@components': fileURLToPath(new URL('./src/components', import.meta.url)),
      '@hooks': fileURLToPath(new URL('./src/hooks', import.meta.url)),
      '@utils': fileURLToPath(new URL('./src/utils', import.meta.url)),
      '@services': fileURLToPath(new URL('./src/services', import.meta.url)),
      '@styles': fileURLToPath(new URL('./src/styles', import.meta.url)),
      '@app': fileURLToPath(new URL('./src/app', import.meta.url)),
      '@assets': fileURLToPath(new URL('./src/assets', import.meta.url)),
      '@config': fileURLToPath(new URL('./src/config', import.meta.url)),
    },
  },

  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `
          @use "@/styles/variables" as *;
          @use "@/styles/mixins" as *;
        `,
      },
    },
  },

  server: {
    port: 3000,
    proxy: {
      // Backend routes live under /api/v1/... — forward the path AS-IS (no
      // rewrite). The previous config stripped the leading /api, which would
      // have 404'd every request had the app ever used the proxy.
      '/api': {
        target: process.env.VITE_API_BASE_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});