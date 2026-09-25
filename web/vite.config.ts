import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget =
  process.env.MOUVADAH_PROXY_TARGET ??
  process.env.TASKABLE_PROXY_TARGET ??
  "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy REST + SSE to the local FastAPI process so the UI can use
    // relative URLs and avoid CORS friction during development.
    proxy: {
      "/mcp": { target: apiProxyTarget, changeOrigin: false },
      "/oauth": { target: apiProxyTarget, changeOrigin: false },
      "/.well-known": { target: apiProxyTarget, changeOrigin: false },
      "/api/v1": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
