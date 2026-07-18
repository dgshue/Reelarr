import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + WebSocket calls to the FastAPI backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:7979", changeOrigin: true },
      "/ws": { target: "ws://localhost:7979", ws: true },
    },
  },
});
