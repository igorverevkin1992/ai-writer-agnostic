import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

// dev-прокси к серверу панели: сервер требует локальный Host и Origin (аудит 4.2),
// поэтому Host подменяется, а Origin dev-сервера (5173) снимается.
const toPanel: ProxyOptions = {
  target: "http://127.0.0.1:8765",
  changeOrigin: true,
  configure: (proxy) => {
    proxy.on("proxyReq", (proxyReq) => proxyReq.removeHeader("origin"));
  },
};

// Сборка кладётся в данные python-пакета: авторам панели не нужен Node (NFR-1).
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../konveyer/data/панель",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": toPanel,
      "/dashboard": toPanel,
    },
  },
});
