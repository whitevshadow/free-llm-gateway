import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Single-container (Docker/local): the backend's mount_frontend() serves
    // ../backend/static, so uvicorn hosts UI + API on one port. On Vercel there
    // is no backend to serve from, and Vercel expects the default `dist` inside
    // the project root — so build there instead. Vercel sets VERCEL=1 for us.
    outDir: process.env.VERCEL ? "dist" : "../backend/static",
    emptyOutDir: true,
  },
  server: {
    // In dev, Vite runs on :5173 and the API on :8000. Proxying keeps the app's
    // fetch calls same-origin, so there is no CORS special-casing in the client.
    proxy: {
      "/v1": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
