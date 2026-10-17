import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The server port lives in .env at the repo root (SERVER_PORT): read it without a dependency.
const envFile = resolve(dirname(fileURLToPath(import.meta.url)), '../../.env');
if (existsSync(envFile) && !process.env.SERVER_PORT) {
  try {
    process.loadEnvFile(envFile);
  } catch {
    // A broken .env is reported by the server; the web app falls back to the default port.
  }
}
const serverPort = process.env.SERVER_PORT ?? '3001';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: !process.env.CI && !process.env.NO_OPEN && (process.platform !== 'linux' || !!process.env.DISPLAY),
    proxy: { '/api': `http://127.0.0.1:${serverPort}` },
  },
});
