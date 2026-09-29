import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const serverPort = process.env.SERVER_PORT ?? '3001';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: !process.env.CI && !process.env.NO_OPEN && (process.platform !== 'linux' || !!process.env.DISPLAY),
    proxy: { '/api': `http://127.0.0.1:${serverPort}` },
  },
});
