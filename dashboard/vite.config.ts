import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server is loopback-only and proxies the API; production builds are
// served by the FastAPI backend from dashboard/dist.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
});
