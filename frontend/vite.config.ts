import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // The backend's mount_frontend() serves this directory. Building here means
    // one container and one port — no second service to deploy.
    outDir: "../backend/static",
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
