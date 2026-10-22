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
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
