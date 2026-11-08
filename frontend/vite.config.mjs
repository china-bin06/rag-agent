import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    proxy: { "/auth": backend, "/rag": backend },
  },
});
