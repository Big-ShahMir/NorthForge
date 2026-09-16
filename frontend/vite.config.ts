/// <reference types="vitest/config" />
import path from "node:path";
import { ServerResponse } from "node:http";
import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

// When the backend isn't running, Vite's proxy would otherwise answer with a
// plain-text HTTP 500, which the app mistakes for a crashed API. Respond with
// the same JSON envelope shape the backend uses instead, so the frontend can
// tell "API down" apart from "API returned an error".
function withUnreachableFallback(): Pick<ProxyOptions, "configure"> {
  return {
    configure(proxy) {
      proxy.on("error", (_err, _req, res) => {
        if (!(res instanceof ServerResponse) || res.headersSent) return;
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            data: null,
            error: {
              code: "API_UNREACHABLE",
              message: "The API is not running or not reachable from the dev server.",
              details: null,
            },
            request_id: "dev-proxy",
          }),
        );
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, ...withUnreachableFallback() },
      "/health": { target: API_TARGET, ...withUnreachableFallback() },
      "/ready": { target: API_TARGET, ...withUnreachableFallback() },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
