// The console is served by FastAPI at /console (backend/app/main.py), so it is
// built for that base path. During development, `npm run dev` serves it on
// :5173 and forwards /api, /assets and /interviews to the backend on :8000, which keeps the
// session cookie same-origin exactly as it is in production.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/console/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/assets": "http://localhost:8000",
      // The candidate room joins and reports through these.
      "/interviews": "http://localhost:8000",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
