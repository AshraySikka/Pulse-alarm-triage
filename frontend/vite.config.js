import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the app on :5173 and forwards /api to Django on :8000.
// Build: assets are served by Django under /static/, so the base changes.
// On Vercel the app is served from the site root instead (Vercel sets VERCEL during builds).
export default defineConfig(({ command }) => ({
  base: command === "build" && !process.env.VERCEL ? "/static/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/admin": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
}));
