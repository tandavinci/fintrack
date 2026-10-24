import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// The dev server proxies /api to the FastAPI backend on :8000 so the browser
// can call the backend without CORS headaches during development.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Use 127.0.0.1 (IPv4) explicitly, not "localhost". On Windows
        // "localhost" can resolve to IPv6 (::1) first, but uvicorn listens on
        // IPv4 by default, causing ECONNREFUSED ::1:8000.
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
