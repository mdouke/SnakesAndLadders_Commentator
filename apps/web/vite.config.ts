import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Node provides this at build time; the web tsconfig has no Node types.
declare const process: { env: Record<string, string | undefined> };

// The API server port can be changed for a second, isolated dev setup.
const api = `http://127.0.0.1:${process.env.COMMENTATOR_API_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": api,
      "/health": api,
      "/ws": { target: api.replace("http", "ws"), ws: true }
    }
  }
});
